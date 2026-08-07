"""An adapter that answers from the request instead of from a model.

A real adapter, not a stand-in for one: it declares `NAME`, `DESCRIPTION`, `INPUT_SCHEMA`
and `run(payload, env)`, and it is registered in `ADAPTERS` the way the docs say an adapter
is registered. The engine cannot tell it apart from `claude_code`, which is the point. The
only shipped adapter needs the Claude Code CLI, an account and a network, so a unit test
that used it would be testing none of those things reliably.

Its answer is the prompt it was given. That makes a gate loop testable without pretending
anything: the engine appends the gate's feedback to the prompt for the next attempt, so the
second turn genuinely produces different text from the first, and a gate keyed on that text
genuinely changes its verdict.

Two documented behaviours a test can ask for, both by writing them in the prompt:

    !fail <message>     raise AdapterRunFailed(<message>), the way a runtime refusal arrives
    anything else       succeed, with the prompt as the turn's text

The envelope carries `payload` and `environment` so a test can see what the engine sent
without anything having to watch it happen.
"""

from __future__ import annotations

from typing import Any

from adapters.errors import AdapterRunFailed

NAME = "echo"
DESCRIPTION = "Answer with the prompt. For tests: no runtime, no network, no cost."

# A flat rate per turn, so a gated step that took two attempts reports twice this and the
# accumulation is checkable against a number rather than a range.
COST_PER_TURN = 0.01

FAIL_PREFIX = "!fail "

# Shaped like the real one: the same required field, the same closed object, and an enum on
# `effort`, so `check_agent_spec` has something to reject when a spec asks for nonsense.
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "minLength": 1},
        "system": {"type": "string"},
        "model": {"type": "string"},
        "effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh", "max"]},
        "json_schema": {"type": "object"},
        "max_budget_usd": {"type": "number", "exclusiveMinimum": 0},
        "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
        # Accepted rather than dispatched: this adapter has no runtime to serve them to.
        # They are here because the schema is closed and `check_agent_spec` probes with
        # both, so leaving them out would refuse every agent that declares a tool.
        "tools": {"type": "array", "items": {"type": "string"}},
        "tool_server": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
    "required": ["prompt"],
    "additionalProperties": False,
}


def run(payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    prompt = payload["prompt"]
    if prompt.startswith(FAIL_PREFIX):
        raise AdapterRunFailed(prompt[len(FAIL_PREFIX) :].strip() or "refused")

    return {
        "ok": True,
        "text": prompt,
        "stop_reason": "end_turn",
        "session_id": "echo-session",
        "requested_model": payload.get("model"),
        "num_turns": 1,
        "usage": {
            "input_tokens": len(prompt.split()),
            "output_tokens": len(prompt.split()),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "cost_usd": COST_PER_TURN,
        "duration_ms": 0,
        "model_usage": {},
        "adapter": {"name": NAME, "cli_version": "n/a"},
        # Not part of the adapter contract, and nothing in the engine reads them. They ride
        # along in the envelope so a test can assert on the request without watching for it.
        "payload": dict(payload),
        "environment": dict(env),
    }
