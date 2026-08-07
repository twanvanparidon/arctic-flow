"""Speaking MCP to `atf mcp-serve`, which is how an agent's tools reach its runtime.

Line-delimited JSON over stdin and stdout, and three methods, so a test needs no client
library: it writes frames in and reads them out. Shared because two suites ask the same
questions of different things. The integration suite asks them of the checkout; the
end-to-end suite asks them of the built binary, which is the only place the server is
reached the way an adapter reaches it.
"""

from __future__ import annotations

import json
from typing import Any

HANDSHAKE: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
LIST: dict[str, Any] = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def frames(*messages: dict[str, Any]) -> str:
    """Messages as the server reads them: one JSON document per line."""
    return "".join(json.dumps(message) + "\n" for message in messages)


def parsed(out: str) -> list[dict[str, Any]]:
    """What came back. Blank lines are dropped; anything else has to parse."""
    return [json.loads(line) for line in out.splitlines() if line]


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
