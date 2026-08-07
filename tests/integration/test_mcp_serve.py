"""`atf mcp-serve` as a real process, which is the only way its claims can be made.

Two of these cannot be made in the unit suite at all. That stdout carries the protocol and
nothing else is a claim about a file descriptor, and a `print` added below the server would
pass every in-process test while corrupting the framing here. And the argv the engine
builds reaching the CLI that has to parse it is two processes agreeing, which is the whole
point of building it in one place and parsing it in another.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from engine.executor import tool_server_command
from paths.resolver import Paths

from .conftest import ENTRY_POINT, Runner, requires

HANDSHAKE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def frames(*messages: dict[str, Any]) -> str:
    return "".join(json.dumps(message) + "\n" for message in messages)


def parsed(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line]


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


class TestTheStream:
    def test_stdout_carries_the_protocol_and_nothing_else(
        self, atf_process: Runner, project: Path
    ) -> None:
        """One stray print below the server corrupts the framing, and the symptom is a
        model reporting that a tool does not work."""
        result = atf_process(
            "--workspace",
            str(project),
            "mcp-serve",
            "--tool",
            "shout",
            stdin=frames(HANDSHAKE, LIST, call("shout", text="hi")),
        )
        assert result.code == 0
        for line in result.out.splitlines():
            json.loads(line)

    def test_a_notification_is_not_answered(self, atf_process: Runner, project: Path) -> None:
        """Three frames in, two out. Replying to one wedges the handshake."""
        result = atf_process(
            "--workspace",
            str(project),
            "mcp-serve",
            stdin=frames(
                HANDSHAKE, {"jsonrpc": "2.0", "method": "notifications/initialized"}, LIST
            ),
        )
        assert len(parsed(result.out)) == 2

    def test_it_exits_zero_when_stdin_closes(self, atf_process: Runner, project: Path) -> None:
        assert atf_process("--workspace", str(project), "mcp-serve", stdin="").code == 0


class TestRunningATool:
    def test_a_tool_really_runs_in_the_workspace(self, atf_process: Runner, project: Path) -> None:
        result = atf_process(
            "--workspace",
            str(project),
            "mcp-serve",
            "--tool",
            "shout",
            stdin=frames(HANDSHAKE, call("shout", text="quiet")),
        )
        [_, answer] = parsed(result.out)
        assert answer["result"]["content"][0]["text"] == "QUIET"
        assert answer["result"]["isError"] is False

    def test_the_workspace_flag_decides_which_tool_is_found(
        self, atf_process: Runner, tmp_path: Path
    ) -> None:
        """Not the cwd: the runtime starts the server wherever it likes."""
        result = atf_process(
            "--workspace",
            str(tmp_path),
            "mcp-serve",
            "--tool",
            "shout",
            stdin=frames(LIST),
        )
        assert parsed(result.out)[0]["error"] is not None


class TestTheArgvTheEngineBuilds:
    """`sys.argv[0]` names the running program, and under pytest that is pytest. It is
    set to the real entry point here, which is what it is in every shipped mode."""

    @pytest.fixture(autouse=True)
    def entry_point(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", [str(ENTRY_POINT)])

    def test_it_is_the_argv_the_cli_takes(self, project: Path, tmp_path: Path) -> None:
        """Built in engine/executor.py and parsed in cli/app.py. Nothing else checks that
        those two agree, and if they stop agreeing a turn silently has no tools."""
        events = tmp_path / "calls.ndjson"
        command = tool_server_command(Paths(project), ["shout"], events)
        completed = subprocess.run(
            command,
            input=frames(HANDSHAKE, call("shout", text="ok")),
            capture_output=True,
            text=True,
            # Deliberately elsewhere: the runtime picks where it starts the server, and a
            # relative sys.argv[0] would not resolve from here.
            cwd=tmp_path,
            timeout=60,
        )
        assert completed.returncode == 0
        [_, answer] = parsed(completed.stdout)
        assert answer["result"]["content"][0]["text"] == "OK"

    def test_the_calls_it_made_are_reported_where_the_engine_is_reading(
        self, project: Path, tmp_path: Path
    ) -> None:
        events = tmp_path / "calls.ndjson"
        command = tool_server_command(Paths(project), ["shout"], events)
        subprocess.run(
            command,
            input=frames(HANDSHAKE, call("shout", text="ok")),
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=60,
        )
        [reported] = [json.loads(line) for line in events.read_text().splitlines()]
        assert reported["tool"] == "shout"
        assert reported["ok"] is True
        assert reported["ms"] >= 0


class TestTheShippedTools:
    def test_read_file_refuses_to_leave_the_workspace(
        self, atf_process: Runner, project: Path
    ) -> None:
        """Containment survives the MCP path, because it is the same tool either way."""
        requires("jq", "awk", "realpath")
        result = atf_process(
            "--workspace",
            str(project),
            "mcp-serve",
            "--tool",
            "read_file",
            stdin=frames(HANDSHAKE, call("read_file", path="/etc/passwd")),
        )
        [_, answer] = parsed(result.out)
        assert answer["result"]["isError"] is True
        assert "outside the workspace root" in answer["result"]["content"][0]["text"]
