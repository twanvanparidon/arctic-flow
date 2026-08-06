#!/usr/bin/env python3
"""Flow execution: a push-based graph of tool and agent steps.

The engine proper. Its sibling `specs.py` holds the checks that run before any of this.

Push, not pull. A step declares where its result goes next (`push`, or `switch` to choose
one branch from its own output); nothing declares what it depends on. The engine derives
the reverse edges and delivers results forward.

The interesting part is a branch that is *not* taken. Its edge is marked skipped, and
skipping propagates: a step whose every inbound edge is skipped is itself skipped, which
skips its outbound edges in turn. That is what lets a downstream join run on both paths
instead of waiting forever for a branch the flow never entered.

A skipped step still resolves in templates, as the literal "(not run)", so a prompt can
acknowledge the gap rather than silently omitting it.

An agent step may also carry a `gate`: a tool that has to accept the result before it is
pushed anywhere. A rejection is not a failure. The step runs again with what the gate said
appended to its prompt, until the gate passes or the attempts run out. That loop is inside
the step, not in the graph, because the graph has no cycles and every turn is a fresh
session: the retry has to carry its own history.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from jsonschema import Draft202012Validator

import adapters
from engine import specs
from paths.resolver import LookupError_, Paths
from vault.vault import Vault, VaultError

TEMPLATE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

# The virtual source of the flow's first push.
START = "__start__"

SKIPPED_RESULT = {"skipped": True, "text": "(not run)", "json": None}

# How many turns a gated step gets when it does not say. Three is one answer plus two
# chances to act on what the gate said, which is where the returns flatten out.
DEFAULT_GATE_ATTEMPTS = 3


class FlowError(RuntimeError):
    """A flow is malformed, or one of its steps failed."""


# --------------------------------------------------------------------------- #
# templating
# --------------------------------------------------------------------------- #


def render(text: str, context: dict[str, Any]) -> str:
    """Substitute {{ dotted.path }} against context.

    Non-string values are inserted as indented JSON, so a template can reference a
    typed step result directly. An unresolvable path is an error rather than an
    empty string, because a silently blank prompt is far more expensive to debug.
    """

    def substitute(match: re.Match[str]) -> str:
        cursor: Any = context
        for part in match.group(1).split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                raise FlowError(f"template references unknown value {{{{ {match.group(1)} }}}}")
            cursor = cursor[part]
        return cursor if isinstance(cursor, str) else json.dumps(cursor, indent=2)

    return TEMPLATE.sub(substitute, text)


def template_refs(value: Any) -> list[str]:
    """Every {{ path }} appearing anywhere in a nested structure."""
    if isinstance(value, str):
        return TEMPLATE.findall(value)
    if isinstance(value, dict):
        return [ref for item in value.values() for ref in template_refs(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in template_refs(item)]
    return []


# --------------------------------------------------------------------------- #
# components
# --------------------------------------------------------------------------- #


def load_component(paths: Paths, kind: str, name: str) -> tuple[Path, dict[str, Any]]:
    try:
        base = paths.find(kind, str(name))
    except LookupError_ as exc:
        raise FlowError(str(exc)) from exc
    spec_path = base / "spec.json"
    try:
        return base, json.loads(spec_path.read_text())
    except json.JSONDecodeError as exc:
        raise FlowError(f"{paths.display(spec_path)} is not valid JSON: {exc}") from exc


def load_agent(paths: Paths, name: str) -> tuple[dict[str, Any], str]:
    """An agent is config plus a prompt: spec.json and the file it points at.

    A separate markdown file keeps prompts editable and reviewable as prose, instead of
    escaped into a JSON string literal.
    """
    base, spec = load_component(paths, "agent", name)
    prompt_file = base / spec.get("system_prompt", "agent.md")
    if not prompt_file.is_file():
        raise FlowError(
            f"agent '{name}' points at {prompt_file.name} for its system prompt, which is missing"
        )
    system = prompt_file.read_text().strip()
    if not system:
        raise FlowError(f"agent '{name}' has an empty system prompt ({prompt_file.name})")
    return spec, system


def check_payload(schema: dict[str, Any], payload: dict[str, Any], who: str) -> None:
    """Validate a payload against a component's declared schema.

    Shared by tools and adapters. An adapter has no spec.json but still declares an
    INPUT_SCHEMA, so both get the same guarantee from the same validator.
    """
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path)
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors
        )
        raise FlowError(f"input rejected by {who}: {detail}")


def spawn(
    base: Path,
    spec: dict[str, Any],
    payload: dict[str, Any],
    paths: Paths,
    secrets: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Validate the payload against the component's own schema, then run it.

    The exit code is handed back rather than judged here, because the two callers read it
    differently: a tool step treats anything but 0 as a failed run, a gate treats it as the
    verdict and wants the output either way.

    `secrets` go into the child's environment and nowhere else, and only the names the
    step declared are present. A component cannot read a secret it was not granted.
    """
    check_payload(spec["input_schema"], payload, f"{spec['name']}/spec.json")

    run = spec["run"]
    timeout = run.get("timeout_seconds", 60)
    command = [str((base / run["command"][0]).resolve()), *run["command"][1:]]
    try:
        return subprocess.run(
            command,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=paths.workspace,
            env=child_environment(secrets),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FlowError(f"{spec['name']} exceeded its {timeout}s timeout") from exc


def exit_summary(spec: dict[str, Any], proc: subprocess.CompletedProcess[str]) -> str:
    """What a non-zero exit meant, in the component's own words where it has them."""
    meaning = spec.get("exit_codes", {}).get(str(proc.returncode), "unspecified exit code")
    stderr = proc.stderr.strip().splitlines()
    return f"{spec['name']} failed (exit {proc.returncode}: {meaning})" + (
        f". {stderr[-1]}" if stderr else ""
    )


def invoke(
    base: Path,
    spec: dict[str, Any],
    payload: dict[str, Any],
    paths: Paths,
    secrets: dict[str, str] | None = None,
) -> str:
    """Run a component and return its stdout. Any exit but 0 fails the step."""
    proc = spawn(base, spec, payload, paths, secrets)
    if proc.returncode != 0:
        raise FlowError(exit_summary(spec, proc))
    return proc.stdout


def child_environment(secrets: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a component runs in: ours, plus its granted secrets.

    With one correction that only matters in a frozen build. PyInstaller prepends its
    bundle to the dynamic-library search path, and everything the engine spawns inherits
    it, so a system binary linking the same library loads *our* copy:

        openssl: .../_internal/libcrypto.so.3: version `OPENSSL_3.2.0' not found

    The engine exists to spawn subprocesses, so this breaks tools on any machine whose
    OpenSSL is newer than the build's. PyInstaller saves each variable it rewrites as
    <NAME>_ORIG; restore those, and drop the ones that had no original value.
    """
    env = dict(os.environ)

    if getattr(sys, "frozen", False):
        for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "LIBPATH"):
            original = env.pop(f"{name}_ORIG", None)
            if original is None:
                env.pop(name, None)
            else:
                env[name] = original

    env.update(secrets or {})
    return env


def maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# graph
# --------------------------------------------------------------------------- #


def outbound_targets(step: dict[str, Any]) -> list[str]:
    """Every step this one could hand to, whichever branch is taken."""
    if "push" in step:
        return list(step["push"] or [])
    if "switch" in step:
        targets = [t for branch in (step.get("cases") or {}).values() for t in (branch or [])]
        targets += list(step.get("default") or [])
        return list(dict.fromkeys(targets))
    return []


def build_graph(steps: list[dict[str, Any]]) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    outbound = {step["id"]: outbound_targets(step) for step in steps}
    inbound: dict[str, set[str]] = defaultdict(set)
    for sid, targets in outbound.items():
        for target in targets:
            inbound[target].add(sid)
    return outbound, inbound


def ancestors_of(sid: str, inbound: dict[str, set[str]]) -> set[str]:
    """Everything transitively upstream: what a step's templates may read."""
    seen: set[str] = set()
    queue = list(inbound.get(sid, set()))
    while queue:
        current = queue.pop()
        if current in seen or current == START:
            continue
        seen.add(current)
        queue.extend(inbound.get(current, set()))
    return seen


# --------------------------------------------------------------------------- #
# validation: everything checkable without running a step
# --------------------------------------------------------------------------- #


def load_flow(path: Path) -> dict[str, Any]:
    flow = yaml.safe_load(path.read_text())
    if not isinstance(flow, dict):
        raise FlowError(f"{path}: flow file must contain a YAML mapping")
    return flow


def check_gate_shape(sid: str, step: dict[str, Any]) -> None:
    """A gate's own keys, before any of them is resolved.

    Two of these are refusals rather than type checks. A gate retries the step it guards,
    so a tool step could only be handed the same input again and return the same answer,
    and a gate with nothing to say would produce the identical prompt a second time. Both
    spend the attempts to arrive back where they started.
    """
    if "tool" in step:
        raise FlowError(
            f"step '{sid}' has a gate, but it runs the tool '{step['tool']}'. A gate retries "
            "its step, and a tool given the same input returns the same result. Gates apply "
            "to agent steps"
        )

    gate = step["gate"]
    if not isinstance(gate, dict):
        raise FlowError(f"step '{sid}' gate must be a mapping with a 'tool' and a 'feedback'")

    for field in ("tool", "feedback"):
        value = gate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise FlowError(f"step '{sid}' gate needs a '{field}'")

    if "max_attempts" not in gate:
        return
    allowed = gate["max_attempts"]
    # YAML 1.1 reads `yes` as True, and bool is an int in Python, so the type check has to
    # rule it out first or `max_attempts: yes` would pass as 1.
    if isinstance(allowed, bool) or not isinstance(allowed, int) or allowed < 2:
        raise FlowError(
            f"step '{sid}' gate max_attempts must be an integer of 2 or more. One attempt "
            "leaves no turn to act on the feedback, which is what a gate is for"
        )


def validate(flow: dict[str, Any], paths: Paths) -> list[dict[str, Any]]:
    for field in ("flow", "start", "steps"):
        if field not in flow:
            raise FlowError(f"flow is missing required field '{field}'")

    steps = flow["steps"]
    if not isinstance(steps, list) or not steps:
        raise FlowError("'steps' must be a non-empty list")

    by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        sid = step.get("id")
        if not sid:
            raise FlowError("every step needs an 'id'")
        if sid in by_id:
            raise FlowError(f"duplicate step id '{sid}'")
        if ("tool" in step) == ("agent" in step):
            raise FlowError(f"step '{sid}' must set exactly one of 'tool' or 'agent'")
        if "agent" in step and not step.get("prompt"):
            raise FlowError(f"agent step '{sid}' needs a 'prompt'")
        if step.get("secrets") is not None:
            declared = step["secrets"]
            if not isinstance(declared, list) or not all(isinstance(n, str) for n in declared):
                raise FlowError(f"step '{sid}' secrets must be a list of names")
            if len(set(declared)) != len(declared):
                raise FlowError(f"step '{sid}' lists a secret more than once")
        if "push" in step and "switch" in step:
            raise FlowError(
                f"step '{sid}' sets both 'push' and 'switch'. A step either hands its result "
                "onward unconditionally or chooses one branch, not both"
            )
        if "gate" in step:
            check_gate_shape(sid, step)
        by_id[sid] = step

    if flow["start"] not in by_id:
        raise FlowError(f"start references unknown step '{flow['start']}'")

    for sid, step in by_id.items():
        if "switch" not in step:
            if "cases" in step or "default" in step:
                raise FlowError(
                    f"step '{sid}' has 'cases' or 'default' but no 'switch' to select them"
                )
            continue

        if not isinstance(step["switch"], str) or not step["switch"].strip():
            raise FlowError(
                f"step '{sid}' needs a 'switch' expression, e.g. \"{{{{ this.json.verdict }}}}\""
            )
        cases = step.get("cases")
        if not isinstance(cases, dict) or not cases:
            raise FlowError(f"step '{sid}' has a switch but no 'cases'")
        for key, branch in cases.items():
            if not isinstance(key, str):
                # YAML 1.1 reads bare on/off/yes/no/true/false as booleans, which
                # would never match the rendered string. Say so instead of silently
                # falling through to default.
                raise FlowError(
                    f"step '{sid}' case key {key!r} is not a string. YAML reads bare "
                    "on/off/yes/no/true/false as booleans, so quote it"
                )
            if branch is not None and not isinstance(branch, list):
                raise FlowError(f"step '{sid}' case '{key}' must be a list of step ids")
        if step.get("default") is not None and not isinstance(step["default"], list):
            raise FlowError(f"step '{sid}' default must be a list of step ids")

    outbound, inbound = build_graph(steps)
    for sid, targets in outbound.items():
        for target in targets:
            if target not in by_id:
                raise FlowError(f"step '{sid}' pushes to unknown step '{target}'")
            if target == sid:
                raise FlowError(f"step '{sid}' pushes to itself")

    for sid in by_id:
        if sid != flow["start"] and not inbound.get(sid):
            raise FlowError(
                f"step '{sid}' is unreachable: nothing pushes to it, and it is not the start"
            )

    # Kahn's algorithm over the forward edges: what cannot be settled is a cycle.
    remaining = {sid: set(inbound.get(sid, set())) - {START} for sid in by_id}
    settled: set[str] = set()
    while True:
        ready = {sid for sid, sources in remaining.items() if sources <= settled}
        if not ready:
            break
        settled |= ready
        for sid in ready:
            del remaining[sid]
    if remaining:
        raise FlowError(f"steps form a cycle: {', '.join(sorted(remaining))}")

    # A template may read inputs, or a step that is genuinely upstream of it. `this` is the
    # running step's own result, so it means something only in a switch or a gate, and
    # `gate` is what the gate said, which exists only in the feedback that answers it.
    declared_inputs = set((flow.get("inputs") or {}).keys())

    def check_refs(
        sid: str,
        refs: list[str],
        *,
        allow_this: bool = False,
        allow_gate: bool = False,
        to_model: bool = False,
    ) -> None:
        upstream = ancestors_of(sid, inbound)
        for ref in refs:
            root, _, rest = ref.partition(".")
            if root == "this":
                if not allow_this:
                    raise FlowError(
                        f"step '{sid}' uses {{{{ this.* }}}} outside its switch or gate. 'this' "
                        "is the step's own result, so it exists only where that result already "
                        "does"
                    )
            elif root == "gate":
                if not allow_gate:
                    raise FlowError(
                        f"step '{sid}' uses {{{{ gate.* }}}} outside its gate feedback. What "
                        "the gate said exists only once it has rejected a result"
                    )
            elif root == "inputs":
                if rest.split(".")[0] not in declared_inputs:
                    raise FlowError(f"step '{sid}' references undeclared input '{rest}'")
            elif root == "steps":
                target = rest.split(".")[0]
                if target not in by_id:
                    raise FlowError(f"step '{sid}' references unknown step '{target}'")
                if target not in upstream:
                    raise FlowError(
                        f"step '{sid}' reads from '{target}', which is not upstream of it. "
                        f"'{target}' may not have run when '{sid}' does"
                    )
            elif root == "secrets":
                name = rest.split(".")[0]
                if to_model:
                    # Templating a secret into a prompt sends it to the model, and it is
                    # then in the conversation for the rest of the session. A step's
                    # secrets still reach its adapter through the environment.
                    raise FlowError(
                        f"step '{sid}' puts {{{{ secrets.{name} }}}} in an agent prompt. That "
                        "sends the secret to the model. Declare it in 'secrets' and let the "
                        "adapter read it from the environment instead"
                    )
                if name not in (by_id[sid].get("secrets") or []):
                    raise FlowError(
                        f"step '{sid}' uses {{{{ secrets.{name} }}}} without declaring it. "
                        "Add it to that step's 'secrets' list, so what a step can read is "
                        "visible where the step is defined"
                    )
            else:
                raise FlowError(f"step '{sid}' references unknown namespace '{root}'")

    for sid, step in by_id.items():
        # An agent step's body is model-facing because its prompt is in there. Which is
        # why the gate is checked apart from it: half of a gate reaches the model and half
        # of it does not.
        to_model = "agent" in step
        body = {k: v for k, v in step.items() if k not in ("id", "switch", "gate")}
        check_refs(sid, template_refs(body), to_model=to_model)
        if step.get("switch"):
            check_refs(sid, template_refs(step["switch"]), allow_this=True, to_model=to_model)
        gate = step.get("gate")
        if gate:
            # A gate's input is a tool's input, so a secret the step declared may be
            # templated into it. Its feedback becomes the next prompt, so one may not.
            check_refs(sid, template_refs(gate.get("input") or {}), allow_this=True)
            check_refs(
                sid,
                template_refs(gate["feedback"]),
                allow_this=True,
                allow_gate=True,
                to_model=True,
            )

    # Shape before contents. `output: "{{ steps.x.text }}"` is the natural typo for
    # `output: {template: ...}`, and without this it reached .get() on a str and came out as
    # an AttributeError traceback rather than a flow error.
    declared_output = flow.get("output") or {}
    if not isinstance(declared_output, dict):
        raise FlowError(
            "'output' must be a mapping with a 'template' key, not "
            f"{type(declared_output).__name__}. Write `output:` then `  template: ...`"
        )

    output_template = declared_output.get("template")
    if output_template:
        for ref in template_refs(output_template):
            root, _, rest = ref.partition(".")
            if root == "inputs":
                if rest.split(".")[0] not in declared_inputs:
                    raise FlowError(f"output references undeclared input '{rest}'")
            elif root == "steps":
                if rest.split(".")[0] not in by_id:
                    raise FlowError(f"output references unknown step '{rest.split('.')[0]}'")
            else:
                raise FlowError(f"output references unknown namespace '{root}'")

    # Every component the flow names is loaded and checked as far as it can be without
    # running: that its spec is one the engine can execute, and that the flow's own use of
    # it agrees with what it declares. Everything found here would otherwise have surfaced
    # mid-run, after earlier steps had already spent time and money.
    for step in by_id.values():
        try:
            if "tool" in step:
                base, spec = load_component(paths, "tool", step["tool"])
                where = paths.display(base / "spec.json")
                specs.check_tool_spec(spec, base, where)
                specs.check_step_input(step, spec, where)
                continue

            base, spec = load_component(paths, "agent", step["agent"])
            # load_agent rather than the spec alone, so a missing or empty agent.md is
            # reported here too.
            load_agent(paths, step["agent"])
            specs.check_agent_spec(spec, paths.display(base / "spec.json"))

            # A gate is a tool run, held to the tool contract in full. Otherwise the first
            # thing to discover a gate that cannot run is the answer it was meant to check.
            if "gate" in step:
                gate_base, gate_spec = load_component(paths, "tool", step["gate"]["tool"])
                gate_where = paths.display(gate_base / "spec.json")
                specs.check_tool_spec(gate_spec, gate_base, gate_where)
                specs.check_gate_input(step, gate_spec, gate_where)
        except specs.SpecError as exc:
            raise FlowError(str(exc)) from exc

        # An agent's tool grant is checked here because the agent declares it, not the
        # flow. The tools have to resolve *and* the refusal below still applies: naming
        # one that does not exist is the more confusing error, so it is reported first.
        for tool in spec.get("tools") or []:
            load_component(paths, "tool", tool)
        if spec.get("tools"):
            raise FlowError(
                f"agent '{step['agent']}' requests in-turn tools "
                f"({', '.join(sorted(spec['tools']))}), which the engine cannot dispatch yet. "
                "Give it the output of a tool step instead"
            )

    return steps


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GateOutcome:
    """What a gate said about the result it was given.

    `text` is what the next attempt is told, so it is the tool's own output rather than
    the engine's summary of it. `json` is that output parsed when it parses, which is how
    a gate reporting structured findings stays readable in a feedback template.
    """

    ok: bool
    text: str
    json: Any = None


def check_gate(
    gate: dict[str, Any],
    result: dict[str, Any],
    context: dict[str, Any],
    paths: Paths,
    secrets: dict[str, str],
) -> GateOutcome:
    """Run a step's gate against the result the step just produced.

    Exit 0 passes. Anything else is a verdict rather than a broken run, so the output is
    kept and handed back instead of raised: what the check printed is all the next attempt
    has to go on. Both streams are read, because a check that prints its findings and one
    that prints a single line on the way out are equally common.

    A gate that is itself broken reports its own error through the same path. That spends
    the attempts before the step fails, which is the price of letting any tool be a gate.
    """
    base, spec = load_component(paths, "tool", gate["tool"])
    gate_context = {**context, "this": result, "secrets": secrets}
    payload = {
        key: render(value, gate_context) if isinstance(value, str) else value
        for key, value in (gate.get("input") or {}).items()
    }
    proc = spawn(base, spec, payload, paths, secrets=secrets)
    parsed = maybe_json(proc.stdout)
    if proc.returncode == 0:
        return GateOutcome(ok=True, text=proc.stdout, json=parsed)

    said = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
    return GateOutcome(ok=False, text=said or exit_summary(spec, proc), json=parsed)


def run_step(
    step: dict[str, Any],
    context: dict[str, Any],
    paths: Paths,
    vault: Vault | None = None,
    notify: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    granted: dict[str, str] = {}
    if step.get("secrets"):
        if vault is None:
            # execute() prefixes the step id onto step failures, so don't repeat it.
            raise FlowError(
                "wants secrets but no vault is open: pass --vault, or set it on the flow"
            )
        granted = vault.select(list(step["secrets"]))

    if "tool" in step:
        base, spec = load_component(paths, "tool", step["tool"])
        # A tool may also take a secret as an input value, so its own schema documents
        # what it expects. Agent steps deliberately cannot: see validate().
        step_context = {**context, "secrets": granted}
        payload = {
            key: render(value, step_context) if isinstance(value, str) else value
            for key, value in (step.get("input") or {}).items()
        }
        stdout = invoke(base, spec, payload, paths, secrets=granted)
        return {"text": stdout, "json": maybe_json(stdout)}

    return run_agent(step, context, paths, granted, notify or (lambda _event: None))


def run_agent(
    step: dict[str, Any],
    context: dict[str, Any],
    paths: Paths,
    secrets: dict[str, str],
    notify: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """An agent turn, repeated while the step's gate rejects the answer.

    Without a gate this runs once and the loop is not a loop. With one, a rejected answer
    is not an error: the next turn gets the original prompt plus the gate's `feedback`,
    which is where `{{ gate.text }}` and the rejected `{{ this.text }}` go. The prompt has
    to carry that itself, because each turn is a fresh session with no memory of the last.
    """
    agent, system = load_agent(paths, step["agent"])
    try:
        adapter = adapters.get(agent["adapter"])
    except adapters.AdapterError as exc:
        raise FlowError(str(exc)) from exc

    gate = step.get("gate")
    allowed = (gate or {}).get("max_attempts", DEFAULT_GATE_ATTEMPTS)
    first = render(step["prompt"], context)
    prompt = first
    spent = 0.0
    attempt = 0

    while True:
        attempt += 1
        result = agent_turn(adapter, agent, system, prompt, secrets)
        if gate is None:
            return result

        # Every attempt was paid for. The envelope only carries what the last turn cost,
        # so a gated step reports the total or the trace under-counts a retried step.
        spent += result.get("cost_usd") or 0.0
        result["cost_usd"] = spent
        result["attempts"] = attempt

        outcome = check_gate(gate, result, context, paths, secrets)
        notify(
            {
                "kind": "gated",
                "step": step["id"],
                "tool": gate["tool"],
                "attempt": attempt,
                "of": allowed,
                "ok": outcome.ok,
            }
        )
        if outcome.ok:
            return result
        if attempt >= allowed:
            # execute() prefixes the step id onto step failures, so don't repeat it.
            raise FlowError(
                f"did not pass gate '{gate['tool']}' in {allowed} attempts. {outcome.text}"
            )
        feedback_context = {
            **context,
            "this": result,
            "gate": {"text": outcome.text, "json": outcome.json},
        }
        prompt = f"{first}\n\n{render(gate['feedback'], feedback_context)}"


def agent_turn(
    adapter: ModuleType,
    agent: dict[str, Any],
    system: str,
    prompt: str,
    secrets: dict[str, str],
) -> dict[str, Any]:
    """One turn through the agent's adapter, as the normalised envelope plus `json`."""
    payload: dict[str, Any] = {"prompt": prompt, "system": system}
    for key in ("model", "effort", "max_budget_usd"):
        if agent.get(key) is not None:
            payload[key] = agent[key]
    # Agent vocabulary stays runtime-neutral: output_schema is what an agent declares,
    # json_schema is this particular adapter's parameter name.
    if agent.get("output_schema"):
        payload["json_schema"] = agent["output_schema"]

    check_payload(adapter.INPUT_SCHEMA, payload, f"adapter {adapter.NAME}")
    try:
        envelope = adapter.run(payload, child_environment(secrets))
    except adapters.AdapterError as exc:
        raise FlowError(f"{adapter.NAME}: {exc}") from exc

    result = dict(envelope)
    result["json"] = maybe_json(envelope.get("text", ""))
    return result


def chosen_targets(
    step: dict[str, Any], result: dict[str, Any], inputs: dict[str, Any], results: dict[str, Any]
) -> list[str]:
    """Which of a step's possible targets actually receive its result."""
    if "push" in step:
        return list(step["push"] or [])
    if "switch" not in step:
        return []

    context = {"inputs": inputs, "steps": results, "this": result}
    value = render(step["switch"], context).strip()
    cases = step.get("cases") or {}
    if value in cases:
        return list(cases[value] or [])
    if "default" in step:
        return list(step["default"] or [])
    # execute() prefixes the step id onto step failures, so don't repeat it here.
    raise FlowError(
        f"switched on '{value}', which matches no case ({', '.join(cases)}) and there is no default"
    )


def execute(
    flow: dict[str, Any],
    steps: list[dict[str, Any]],
    inputs: dict[str, Any],
    paths: Paths,
    vault: Vault | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the flow. `on_event` is told when steps start, finish, skip and fail.

    The observer emits facts and `cli/progress.py` renders them, so this module never
    decides what progress looks like. Events arrive from worker threads, so an observer
    has to be safe to call concurrently.
    """
    notify = on_event or (lambda _event: None)
    by_id = {step["id"]: step for step in steps}
    outbound, inbound = build_graph(steps)

    edge: dict[tuple[str, str], str] = {
        (source, target): "pending" for target, sources in inbound.items() for source in sources
    }
    edge[(START, flow["start"])] = "delivered"
    inbound[flow["start"]].add(START)

    state = {sid: "waiting" for sid in by_id}
    results: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []

    def propagate_skips() -> None:
        """A step whose every inbound edge was skipped never runs, nor does its
        subtree. Without this, a join downstream of an untaken branch waits forever."""
        changed = True
        while changed:
            changed = False
            for sid in by_id:
                if state[sid] != "waiting":
                    continue
                sources = inbound.get(sid, set())
                if sources and all(edge[(s, sid)] == "skipped" for s in sources):
                    state[sid] = "skipped"
                    results[sid] = dict(SKIPPED_RESULT)
                    for target in outbound[sid]:
                        edge[(sid, target)] = "skipped"
                    trace.append({"step": sid, "skipped": True})
                    notify({"kind": "skipped", "step": sid})
                    changed = True

    with ThreadPoolExecutor(max_workers=max(1, len(steps))) as pool:
        running: dict[Any, tuple[str, float]] = {}
        while True:
            propagate_skips()
            for sid in by_id:
                if state[sid] != "waiting":
                    continue
                sources = inbound.get(sid, set())
                if any(edge[(s, sid)] == "pending" for s in sources):
                    continue
                if not any(edge[(s, sid)] == "delivered" for s in sources):
                    continue
                state[sid] = "running"
                step = by_id[sid]
                notify(
                    {
                        "kind": "started",
                        "step": sid,
                        "component": f"tool {step['tool']}"
                        if "tool" in step
                        else f"agent {step['agent']}",
                    }
                )
                # Snapshot at submit time: everything upstream has already resolved,
                # so a step never observes a partial result.
                context = {"inputs": inputs, "steps": dict(results)}
                running[pool.submit(run_step, by_id[sid], context, paths, vault, notify)] = (
                    sid,
                    time.monotonic(),
                )

            if not running:
                break

            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                sid, started = running.pop(future)
                elapsed = round((time.monotonic() - started) * 1000)
                try:
                    results[sid] = future.result()
                    state[sid] = "done"
                    # Resolving the push is inside the same guard on purpose. An
                    # unmatched switch fails *after* the step itself succeeded, and
                    # leaving it outside meant a step could start, never report a
                    # result either way, and vanish from the progress output.
                    targets = chosen_targets(by_id[sid], results[sid], inputs, results)
                except (FlowError, VaultError) as exc:
                    # Scrub before the message travels: a failing component may echo a
                    # secret it was given, and this text reaches logs and terminals.
                    message = vault.scrub(str(exc)) if vault else str(exc)
                    trace.append({"step": sid, "ms": elapsed, "ok": False, "error": message})
                    notify({"kind": "failed", "step": sid, "ms": elapsed, "error": message})
                    raise FlowError(f"step '{sid}': {message}") from exc

                for target in outbound[sid]:
                    edge[(sid, target)] = "delivered" if target in targets else "skipped"
                entry = {
                    "step": sid,
                    "ms": elapsed,
                    "ok": True,
                    "pushed_to": targets,
                    "cost_usd": results[sid].get("cost_usd"),
                }
                # Only where a gate ran. A key reading `null` on every step of every
                # ungated flow says nothing and is in the way of what the trace is for.
                if results[sid].get("attempts"):
                    entry["attempts"] = results[sid]["attempts"]
                trace.append(entry)
                notify({"kind": "finished", "is_switch": "switch" in by_id[sid], **entry})

    return results, trace


def run_flow(
    flow: dict[str, Any],
    inputs: dict[str, Any],
    paths: Paths,
    vault: Vault | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Validate, execute, and render the flow's output."""
    steps = validate(flow, paths)
    results, trace = execute(flow, steps, inputs, paths, vault, on_event)
    template = (flow.get("output") or {}).get("template")
    rendered = (
        render(template, {"inputs": inputs, "steps": results})
        if template
        else json.dumps(results, indent=2)
    )
    return rendered.strip(), trace


def check_inputs(flow: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    """Reject unknown and missing inputs before anything runs.

    Takes a mapping, not the CLI's `KEY=VALUE` strings. How inputs were typed is a front
    end's business; the engine only has an opinion about which names are allowed.
    """
    declared = flow.get("inputs") or {}
    for name, meta in declared.items():
        if (meta or {}).get("required") and name not in supplied:
            raise FlowError(f"missing required input '{name}'")
    for name in supplied:
        if name not in declared:
            raise FlowError(f"unknown input '{name}' (declared: {', '.join(declared) or 'none'})")
    return dict(supplied)
