"""Checking that a component's spec.json is something the engine can actually run.

Every failure reported here would otherwise have happened *during* a run, after other
steps had executed, cost money, or written files. A `run.command` pointing at a missing
file, an `effort` its adapter rejects, an `input_schema` that is not a valid schema: all
load fine as JSON and blow up later.

These run inside `validate()`, so `atf run` performs them before the first step and
`atf lint` performs them without running anything. Same checks either way, which is the
only arrangement where a green lint tells you something.

The rule for what belongs here: **check what the runtime reads.** `invoke()` reads
`input_schema`, `run.command` and `name`, so those are required and typed. `exit_codes`
and `timeout_seconds` have defaults, so they are checked only when present.

This module does not import `engine`, so the dependency runs one way and there is no cycle.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

import adapters

# What a tool's spec.json has to look like for `invoke()` to be able to run it.
TOOL_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "version": {"type": "integer"},
        "description": {"type": "string", "minLength": 1},
        "doc": {"type": "string"},
        "run": {
            "type": "object",
            "properties": {
                # argv, not a shell string: the first element is resolved against the
                # component's own directory and the rest are passed through untouched.
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "input": {"enum": ["stdin_json"]},
                "output": {"enum": ["stdout_text"]},
                "cwd": {"enum": ["workspace"]},
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["command"],
        },
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        # JSON object keys are strings, so the exit code is spelled "3", not 3.
        "exit_codes": {
            "type": "object",
            "propertyNames": {"pattern": "^[0-9]+$"},
            "additionalProperties": {"type": "string"},
        },
        # Checked rather than described, because it is the gate between a tool an agent
        # was granted and the workspace. Nothing approves a call an agent makes for
        # itself, so `validate()` reads `filesystem` to decide whether granting this tool
        # has to be said out loud. Spelled "rw" or left out, that gate silently opens.
        "permissions": {
            "type": "object",
            "properties": {
                "filesystem": {"enum": ["none", "read", "write"]},
                "network": {"type": "boolean"},
            },
            "required": ["filesystem"],
        },
        # Names the tool expects in its environment, granted by the step that runs it.
        # `validate()` reads it to refuse granting a credentialled tool to an agent, which
        # would call it without a step having declared anything.
        "secrets": {"type": "array", "items": {"type": "string"}},
        "requires": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "description", "run", "input_schema", "permissions"],
}

# An agent is config plus a prompt. `adapter` is the only field the engine cannot proceed
# without; the rest are passed to the adapter, which declares its own schema for them.
AGENT_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "kind": {"const": "agent"},
        "version": {"type": "integer"},
        "description": {"type": "string", "minLength": 1},
        "system_prompt": {"type": "string", "minLength": 1},
        "adapter": {"type": "string", "minLength": 1},
        "model": {"type": "string", "minLength": 1},
        "effort": {"type": "string"},
        "max_budget_usd": {"type": "number", "exclusiveMinimum": 0},
        "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
        "output_schema": {"type": "object"},
        # Names of the engine's own tools, not the runtime's. An adapter exposes them to
        # the turn however it can; what a flow declares stays the same across adapters.
        "tools": {"type": "array", "items": {"type": "string"}},
        # Not forwarded anywhere. It is the engine's own gate on granting a tool that
        # changes the workspace, enforced in `validate()` where the tools are resolved.
        "unattended": {"type": "boolean"},
    },
    "required": ["name", "description", "adapter"],
}

# Agent fields the engine forwards to the adapter, and the adapter's name for each. Kept
# beside the schema so adding a pass-through field means editing one place, not two.
FORWARDED = {
    "model": "model",
    "effort": "effort",
    "max_budget_usd": "max_budget_usd",
    "timeout_seconds": "timeout_seconds",
    "tools": "tools",
}


def adapter_parameters(spec: dict[str, Any]) -> dict[str, Any]:
    """The adapter parameters an agent asks for, under the adapter's own names for them.

    Built here rather than in the executor because the lint probe and the turn itself have
    to send the same thing. A check that validated a different payload from the one that
    runs is a check that passes on a spec the run then rejects.
    """
    parameters = {
        parameter: spec[field]
        for field, parameter in FORWARDED.items()
        if spec.get(field) is not None
    }
    # Agent vocabulary stays runtime-neutral: output_schema is what an agent declares,
    # json_schema is this particular adapter's parameter name. It is not in FORWARDED
    # because that maps on `is not None` and an empty schema must not be sent.
    if spec.get("output_schema"):
        parameters["json_schema"] = spec["output_schema"]
    return parameters


class SpecError(Exception):
    """A component's spec.json cannot be run as written.

    Subclassed from Exception rather than FlowError to keep this module free of an engine
    import. `validate()` re-raises it as a FlowError, so the CLI's error surface is
    unchanged.
    """


def _describe(errors: list, subject: str) -> str:
    """`subject: where: what`, with `where` omitted when the whole document is the subject.

    A single-value check reports on something the subject already names, so a location
    prefix produced messages like "an invalid limit: <root>: 'many' is not …".
    """
    parts = []
    for error in sorted(errors, key=lambda error: list(error.path)):
        location = "/".join(map(str, error.path))
        parts.append(f"{location}: {error.message}" if location else error.message)
    return f"{subject}: {'; '.join(parts)}"


def _check_against(schema: dict[str, Any], document: Any, subject: str) -> None:
    errors = list(Draft202012Validator(schema).iter_errors(document))
    if errors:
        raise SpecError(_describe(errors, subject))


def _check_is_schema(candidate: Any, subject: str) -> None:
    """A declared schema has to be a schema.

    `input_schema` goes straight to the validator at run time, so a typo like
    `"type": "objekt"` is caught by nothing until a payload arrives. Draft 2020-12's own
    meta-schema catches it here instead.
    """
    try:
        Draft202012Validator.check_schema(candidate)
    except SchemaError as exc:
        raise SpecError(f"{subject} is not a valid JSON Schema: {exc.message}") from exc


def check_tool_spec(spec: dict[str, Any], base: Path, where: str) -> None:
    """Everything about a tool that has to hold before a step can call it."""
    _check_against(TOOL_SPEC_SCHEMA, spec, f"{where} is not a runnable tool spec")

    for field in ("input_schema", "output_schema"):
        if field in spec:
            _check_is_schema(spec[field], f"{where}: {field}")

    # The command is resolved against the component's directory, exactly as invoke() does
    # it. A spec naming a script that was never committed, or that lost its executable bit
    # in a copy, is the most common way a tool fails on someone else's machine.
    executable = (base / spec["run"]["command"][0]).resolve()
    if not executable.is_file():
        raise SpecError(
            f"{where}: run.command points at {spec['run']['command'][0]}, which does not "
            f"exist in {base.name}/"
        )
    if not os.access(executable, os.X_OK):
        raise SpecError(
            f"{where}: {spec['run']['command'][0]} is not executable. chmod +x it, or the "
            "engine cannot run this tool"
        )


def check_agent_spec(spec: dict[str, Any], where: str) -> None:
    """Everything about an agent that has to hold before a step can run it.

    The last block is the interesting one. Rather than restating what an adapter accepts,
    it builds the payload the engine would build and asks the adapter's own schema. A bad
    `effort` is caught by the rule that would have rejected it mid-run, and adding an
    adapter parameter needs no change here.
    """
    _check_against(AGENT_SPEC_SCHEMA, spec, f"{where} is not a runnable agent spec")

    if "output_schema" in spec:
        _check_is_schema(spec["output_schema"], f"{where}: output_schema")

    try:
        adapter = adapters.get(spec["adapter"])
    except adapters.AdapterError as exc:
        raise SpecError(f"{where}: {exc}") from exc

    probe: dict[str, Any] = {"prompt": "probe", "system": "probe", **adapter_parameters(spec)}
    if spec.get("tools"):
        # The engine builds the real one from where it is installed, which is not knowable
        # from a spec. A placeholder is enough: what is being asked is whether the adapter
        # accepts a turn that has tools at all.
        probe["tool_server"] = ["probe"]

    _check_against(
        adapter.INPUT_SCHEMA, probe, f"{where} would be rejected by adapter {adapter.NAME}"
    )


def check_step_input(step: dict[str, Any], spec: dict[str, Any], where: str) -> None:
    """The step's `input` against the tool's declared schema, as far as is knowable."""
    _check_input(f"step '{step['id']}'", step.get("input") or {}, spec, where)


def _check_input(subject: str, supplied: dict[str, Any], spec: dict[str, Any], where: str) -> None:
    """What can be said about a tool's input without running the flow.

    Most values are templates resolved per run. Three things can be checked now, and each
    is a real runtime failure otherwise:

      a key the tool does not accept       caught by additionalProperties on its schema
      a required key not supplied          the template would never have filled it in
      a literal value of the wrong type    "max_lines: many" is wrong today

    A templated value is treated as present and otherwise unexamined. The alternative is
    guessing what it renders to, and a lint that guesses is a lint people switch off.
    """
    schema = spec["input_schema"]
    properties = schema.get("properties") or {}

    if schema.get("additionalProperties") is False:
        unknown = sorted(set(supplied) - set(properties))
        if unknown:
            raise SpecError(
                f"{subject} passes {', '.join(unknown)} to {spec['name']}, which "
                f"does not accept {'them' if len(unknown) > 1 else 'it'}. {where} allows "
                f"{', '.join(sorted(properties)) or 'nothing'}"
            )

    missing = sorted(set(schema.get("required") or []) - set(supplied))
    if missing:
        raise SpecError(
            f"{subject} does not pass {', '.join(missing)} to {spec['name']}, "
            f"which {where} requires"
        )

    for key, value in supplied.items():
        if key in properties and not _templated(value):
            _check_against(
                properties[key],
                value,
                f"{subject} passes an invalid {key} to {spec['name']}",
            )


def _templated(value: Any) -> bool:
    """Whether a value is decided at run time rather than written in the flow."""
    return isinstance(value, str) and "{{" in value
