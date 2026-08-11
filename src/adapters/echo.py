"""Answer a turn from the request instead of from a model.

A real adapter, not a stand-in for one: the engine cannot tell it apart from `claude_code`,
which is what makes it useful. It needs no runtime on the machine, no network and no
account, so a flow's graph, its branches, its loops and every template in it can be
exercised for free. `atf run` against it is a dry run that really runs.

That is also the only way the end-to-end suite reaches an agent step. `ADAPTERS` is static
imports frozen into the binary, so a test cannot register one from outside; an adapter that
answers without a runtime has to ship to be reachable at all.

The prompt steers it, so a flow decides what happens with nothing patched anywhere. The
first word of the first line may be a directive:

    !fail <detail>      the runtime refused: raises AdapterRunFailed
    !json <one line>    answer with exactly that JSON, so a switch can be driven
    !invocation         answer with a JSON report of what the engine sent

Anything else is answered with the prompt itself. That is what makes a loop observable: a
pass reads what the last one produced out of `steps`, so a second turn whose prompt guards
on that genuinely differs from the first.

An agent declaring `output_schema` gets the smallest object satisfying it rather than the
prompt, so a flow written for a real runtime dry-runs without being edited. `!json` overrides
that, because the smallest instance takes an enum's first value and a switch needs to be
pointed at a particular case.

Tools are accepted and never dispatched. There is no runtime here to serve them to, and
serving them would make this a second engine loop rather than a way of looking at the first.
They are in the schema because it is closed and `check_agent_spec` probes with them, so
leaving them out would refuse every agent that grants a tool. `!invocation` reports both the
names and the `tool_server` argv the engine built, which is the only way to see that command
without a model on the other end of it.

The vocabulary is deliberately the one `tests/support/fake_claude.py` answers to. Two ways
of running an agent step without a model that disagreed on how to ask would be worse than
either alone.
"""

from __future__ import annotations

import json
from typing import Any

from adapters.errors import AdapterRunFailed

NAME = "echo"
DESCRIPTION = "Answer from the request rather than a model: no runtime, no network, no cost."

# A flat rate per turn, so a flow that looped twice reports twice this and the total is
# checkable against a number rather than a range.
COST_PER_TURN = 0.01

# Only what the engine sends: `specs.FORWARDED` plus the two `agent_turn` always builds. An
# agent spec written against claude_code therefore runs against this one unedited. Closed and
# with the same enum on `effort`, so `check_agent_spec` rejects the same nonsense at lint time.
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "minLength": 1,
            "description": "The user turn. Answered with itself, unless it opens with a "
            "directive: !fail, !json or !invocation.",
        },
        "system": {
            "type": "string",
            "description": "The system prompt. Reported by !invocation, and otherwise unused: "
            "there is no model here for it to steer.",
        },
        "model": {
            "type": "string",
            "description": "Echoed back as 'requested_model'. Accepted so an agent spec does "
            "not have to drop it to dry-run.",
        },
        "effort": {
            "type": "string",
            "enum": ["low", "medium", "high", "xhigh", "max"],
            "description": "Accepted and ignored, with the same enum a real runtime enforces.",
        },
        "json_schema": {
            "type": "object",
            "description": "When set, the answer is the smallest object satisfying it rather "
            "than the prompt. !json overrides.",
        },
        "max_budget_usd": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Accepted and ignored. A turn costs a flat "
            f"${COST_PER_TURN}, which no budget can exceed.",
        },
        "timeout_seconds": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Accepted and ignored. Answering from the request takes no time.",
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Accepted and never dispatched: there is no runtime here to serve "
            "them to. !invocation reports them, so a flow can check what it granted.",
        },
        "tool_server": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "argv the engine would have the runtime spawn. Reported by "
            "!invocation and otherwise unused, which is the one way to see it without a model.",
        },
    },
    "required": ["prompt"],
    "additionalProperties": False,
}

FAIL = "!fail"
JSON = "!json"
INVOCATION = "!invocation"

# The report an !invocation turn returns carries the environment, so it is filtered to one
# prefix rather than printing whatever the caller happened to export. A step grants
# `ATF_PROBE_x` to show that a secret it declared arrived.
PROBE_PREFIX = "ATF_PROBE_"


def instance_for(schema: Any) -> Any:
    """The smallest value satisfying a schema, which is what an output_schema asks for.

    An enum takes its first value. That is the reason `!json` exists: a switch needs to be
    pointed at a case, and "first" is only ever one of them.
    """
    if not isinstance(schema, dict):
        return None
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties") or {}
        required = schema.get("required") or list(properties)
        return {name: instance_for(properties.get(name, {})) for name in required}
    if kind == "array":
        return []
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    return "text"


def _answer(payload: dict[str, Any], env: dict[str, str]) -> str:
    prompt = payload["prompt"]
    directive, _, detail = prompt.split("\n", 1)[0].partition(" ")

    if directive == FAIL:
        raise AdapterRunFailed(detail.strip() or "refused")

    if directive == JSON:
        # Checked rather than passed through: an unparseable answer would surface three
        # steps later as a template failing on `this.json`, which does not point back here.
        try:
            json.loads(detail)
        except json.JSONDecodeError as exc:
            raise AdapterRunFailed(f"{JSON} wants one line of JSON, got {detail!r}: {exc}") from exc
        return detail

    if directive == INVOCATION:
        return json.dumps(
            {
                "payload": payload,
                "env": {k: v for k, v in env.items() if k.startswith(PROBE_PREFIX)},
            }
        )

    if payload.get("json_schema"):
        return json.dumps(instance_for(payload["json_schema"]))

    return prompt


def run(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """One turn. Returns the normalised envelope; raises AdapterError on failure."""
    answer = _answer(payload, env)
    words = len(payload["prompt"].split())

    return {
        "ok": True,
        "text": answer,
        "stop_reason": "end_turn",
        "session_id": "echo-session",
        "requested_model": payload.get("model"),
        "num_turns": 1,
        "usage": {
            "input_tokens": words,
            "output_tokens": len(answer.split()),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "cost_usd": COST_PER_TURN,
        # Zero rather than a measurement: a test asserting on a duration would be asserting
        # on the machine it ran on, and there is nothing here worth timing.
        "duration_ms": 0,
        "model_usage": {},
        "adapter": {"name": NAME, "cli_version": "n/a"},
    }
