"""Speaking MCP to `atf mcp-serve`, which is how an agent's tools reach its runtime.

Line-delimited JSON over stdin and stdout, so a test needs no client library: it writes
frames in and reads them out. Shared because two suites ask the same questions of different
things. The integration suite asks them of the checkout; the end-to-end suite asks them of
the built binary, which is the only place the server is reached the way an adapter reaches
it.

**Read replies by id, not by position.** `by_id` and `answered` exist because a server may
answer calls in whatever order they finish, so an index into the output is a race rather
than an assertion. Position is still right for a conversation carrying one request, where
the claim is that exactly one reply came back.
"""

from __future__ import annotations

import json
from typing import Any

HANDSHAKE: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
LIST: dict[str, Any] = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
PING: dict[str, Any] = {"jsonrpc": "2.0", "id": 4, "method": "ping"}


def frames(*messages: dict[str, Any]) -> str:
    """Messages as the server reads them: one JSON document per line."""
    return "".join(json.dumps(message) + "\n" for message in messages)


def parsed(out: str) -> list[dict[str, Any]]:
    """What came back. Blank lines are dropped; anything else has to parse."""
    return [json.loads(line) for line in out.splitlines() if line]


def by_id(replies: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    """Replies keyed by the request each one answers.

    Takes what `parsed` returns rather than the raw text, so a caller that already has the
    replies does not parse them twice. Which order they arrived in is the server's
    business: calls that run at the same time finish when they finish. A parse error
    carries no id and lands under None.
    """
    return {reply.get("id"): reply for reply in replies}


def answered(replies: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    """The one reply to `request_id`, or a failure naming what did come back."""
    keyed = by_id(replies)
    assert request_id in keyed, f"no reply for {request_id}; answered {sorted(map(str, keyed))}"
    return keyed[request_id]


def call(name: str, *, request_id: int = 3, **arguments: Any) -> dict[str, Any]:
    """One `tools/call`. `request_id` is keyword-only so a tool argument cannot collide."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def cancelled(request_id: int) -> dict[str, Any]:
    """The client withdrawing a request. A notification, so it is never answered."""
    return {
        "jsonrpc": "2.0",
        "method": "notifications/cancelled",
        "params": {"requestId": request_id, "reason": "test"},
    }
