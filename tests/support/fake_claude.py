#!/usr/bin/env python3
"""A stand-in for the Claude Code CLI that speaks its protocol instead of calling a model.

Copied onto `PATH` as `claude` by the `fake_claude` fixture. It is not a mock of the
adapter: the adapter really does spawn a process, write a prompt to its stdin, read JSON
off its stdout and act on its exit code. Only the model is absent, which is the one part a
test cannot afford, since the real CLI needs an account, a network and money, and answers
differently every time.

What it imitates is the documented contract of `--print --output-format json` in CLI
2.1.224: one JSON object of `type: result` on stdout, `is_error` when the turn failed, and
a response validating against `--json-schema` when one was given.

The prompt steers it, so a flow decides what happens without anything having to patch
anything. The first line may be a directive:

    !fail <detail>     a result object marked is_error, exit 1
    !crash <detail>    nothing on stdout, detail on stderr, exit 2
    !garbage           prose on stdout instead of JSON, exit 0
    !contradiction     a result claiming success, from a process that exits 1
    !invocation        the answer is a JSON description of how this was invoked

Anything else is answered with the prompt itself, which is what makes a gate loop
observable: the engine appends the gate's feedback to the next prompt, so the second turn
really does produce different text. With `--json-schema`, the answer is instead the
smallest object satisfying that schema, and `$FAKE_CLAUDE_PREFER` picks between the values
of an enum.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

VERSION = "2.1.224 (Claude Code)"

# Flags that take a value. Anything else is a switch.
VALUED = {
    "--system-prompt",
    "--append-system-prompt",
    "--model",
    "--effort",
    "--resume",
    "--max-budget-usd",
    "--json-schema",
    "--output-format",
    "--tools",
    "--mcp-config",
    "--allowedTools",
    "--setting-sources",
}


def parse(argv: list[str]) -> dict[str, Any]:
    options: dict[str, Any] = {"switches": []}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in VALUED:
            options[token] = argv[index + 1] if index + 1 < len(argv) else ""
            index += 2
        else:
            options["switches"].append(token)
            index += 1
    return options


def instance_for(schema: Any, prefer: str | None) -> Any:
    """The smallest value satisfying a schema, which is what --json-schema promises."""
    if not isinstance(schema, dict):
        return None
    if "enum" in schema:
        choices = schema["enum"]
        return prefer if prefer in choices else choices[0]
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties") or {}
        required = schema.get("required") or list(properties)
        return {name: instance_for(properties.get(name, {}), prefer) for name in required}
    if kind == "array":
        return []
    if kind == "integer" or kind == "number":
        return 0
    if kind == "boolean":
        return False
    return "text"


def envelope(answer: str, *, error: bool = False, detail: str = "") -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "error_during_execution" if error else "success",
        "is_error": error,
        "result": detail if error else answer,
        "session_id": "fake-session",
        "num_turns": 1,
        "duration_ms": 1,
        "total_cost_usd": 0.01,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "modelUsage": {"fake-model": {"inputTokens": 10, "outputTokens": 20}},
    }


def answer_for(prompt: str, options: dict[str, Any]) -> str:
    if "--json-schema" in options:
        prefer = os.environ.get("FAKE_CLAUDE_PREFER")
        return json.dumps(instance_for(json.loads(options["--json-schema"]), prefer))
    return prompt


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print(VERSION)
        return 0

    options = parse(argv)
    prompt = sys.stdin.read()
    directive, _, detail = prompt.split("\n", 1)[0].partition(" ")

    if directive == "!fail":
        print(json.dumps(envelope("", error=True, detail=detail or "refused")))
        return 1
    if directive == "!crash":
        print(detail or "something went wrong", file=sys.stderr)
        return 2
    if directive == "!garbage":
        print("I am not JSON.")
        return 0
    if directive == "!contradiction":
        print(json.dumps(envelope("looks fine")))
        return 1
    if directive == "!invocation":
        described = {
            "switches": options["switches"],
            # How isolation is spelled depends on the turn: --safe-mode disables MCP
            # servers, so a turn with tools is isolated the other way instead.
            "isolated": "--safe-mode" in options["switches"],
            "isolated_without_safe_mode": (
                options.get("--setting-sources") == ""
                and "--disable-slash-commands" in options["switches"]
            ),
            "mcp_config": options.get("--mcp-config"),
            "strict_mcp_config": "--strict-mcp-config" in options["switches"],
            "allowed_tools": options.get("--allowedTools"),
            "model": options.get("--model"),
            "effort": options.get("--effort"),
            "tools": options.get("--tools"),
            "system": options.get("--system-prompt"),
            "prompt": prompt,
            # Only the probe prefix, so a report of an invocation is not a dump of the
            # developer's environment. A step grants `ATF_PROBE_x` to show a secret arrived.
            "env": {k: v for k, v in os.environ.items() if k.startswith("ATF_PROBE_")},
        }
        print(json.dumps(envelope(json.dumps(described))))
        return 0

    print(json.dumps(envelope(answer_for(prompt, options))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
