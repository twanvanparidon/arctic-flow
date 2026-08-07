"""Serving the engine's tools to an agent's turn, over MCP on stdio.

An adapter cannot hand its runtime a Python function, so a tool an agent uses mid-turn has
to arrive over a protocol. The adapter spawns this; `engine.executor.invoke` still does the
running, so an in-turn call is checked and executed exactly as a step's would be.

**stdout carries the protocol and nothing else.** The usual rule with a sharper edge: one
stray `print` below this module corrupts the framing, and the symptom is a model reporting
that a tool does not work. Diagnostics go to stderr.

A front end rather than a command, because it reads a stream and writes one. It is spawned,
not typed: `atf mcp-serve` exists so an adapter has something to launch.

Three methods, which is all a tool server needs. Two behaviours here are not obvious and
both were confirmed against a live client:

  A notification carries no `id` and must never be answered, not even to refuse it.
  `notifications/initialized` is the one that arrives, and replying to it wedges the
  handshake.

  A bad tool call is a *result* with `isError`, not a JSON-RPC error. The model chose the
  arguments, so it is the party that can fix them; a protocol error tells it nothing.

Two limits worth knowing before changing this. Calls are answered one at a time, so a
client that issues several at once has them serialised, and nothing here can be cancelled
mid-tool. And no secret is in reach: `validate()` refuses a step that both declares
`secrets` and runs an agent granted tools, so the environment this inherits carries none.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import commands
from cli import branding
from paths.resolver import Paths

# What this speaks when a client states no preference. A client's own revision is echoed
# back instead of negotiated down: these three methods are unchanged across every revision
# that exists, so refusing one we would have satisfied anyway buys nothing.
PROTOCOL_VERSION = "2025-06-18"

# The name the tools are exposed under. A client prefixes it onto every tool, so a model
# sees `mcp__atf__read_file`, and an adapter has to build the same string to allow it.
SERVER_NAME = "atf"

PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _result(message_id: Any, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": message_id, "result": result})


def _error(message_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}})


def _content(text: str, *, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    return {
        "protocolVersion": requested if isinstance(requested, str) else PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": branding.__version__},
    }


def _tools_list(names: list[str], paths: Paths) -> dict[str, Any]:
    return {
        "tools": [
            {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
            for tool in commands.describe_tools(names, paths)
        ]
    }


def _report(events: Path | None, call: commands.ToolCall) -> None:
    """Tell the engine a tool ran, so a call inside a turn is not invisible to it.

    Appended rather than written, because the engine is reading the same file while this
    goes on. A failure to report is swallowed: the turn is what the caller asked for, and
    losing a progress line is not worth ending it.
    """
    if events is None:
        return
    try:
        with events.open("a") as stream:
            stream.write(json.dumps({"tool": call.name, "ok": call.ok, "ms": call.ms}) + "\n")
    except OSError:
        pass


def _tools_call(
    params: dict[str, Any], names: list[str], paths: Paths, events: Path | None
) -> dict[str, Any]:
    name = params.get("name")
    if name not in names:
        # Reported to the model rather than raised: it picked the name, and picking another
        # is a recovery. Naming what it may call is what makes that possible.
        return _content(
            f"unknown tool '{name}'. Available: {', '.join(names) or 'none'}", is_error=True
        )

    call = commands.call_tool(str(name), params.get("arguments") or {}, paths)
    _report(events, call)
    if call.ok:
        return _content(call.text, is_error=False)
    return _content(call.error or f"{call.name} failed without saying why", is_error=True)


def serve(names: list[str], paths: Paths, events: Path | None = None) -> int:
    """Answer frames until stdin closes."""
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue

        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _error(None, PARSE_ERROR, f"not JSON: {exc}")
            continue

        message_id = message.get("id")
        if message_id is None:
            continue
        method = message.get("method")
        params = message.get("params") or {}

        try:
            if method == "initialize":
                _result(message_id, _initialize(params))
            elif method == "tools/list":
                _result(message_id, _tools_list(names, paths))
            elif method == "tools/call":
                _result(message_id, _tools_call(params, names, paths, events))
            else:
                _error(message_id, METHOD_NOT_FOUND, f"unsupported method '{method}'")
        except Exception as exc:
            # One bad frame must not end the session. Letting this escape would close
            # stdout mid-turn, and the client would report a transport failure that says
            # nothing about the cause.
            _error(message_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

    return 0
