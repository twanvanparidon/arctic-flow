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

import pytest

from adapters import claude_code
from cli import mcp_server
from engine.executor import tool_server_command
from paths.resolver import Paths
from support import components as make
from support.mcp import HANDSHAKE, LIST, PING, answered, by_id, call, cancelled, frames, parsed

from .conftest import ENTRY_POINT, Runner, requires


class TestConcurrentReplies:
    """Two workers writing one real file descriptor.

    The unit suite cannot make this claim: `capsys` writes into a BytesIO, so a reply split
    around another's would go unnoticed there. Here stdout is a pipe with a buffer, and a
    reply bigger than it is flushed in pieces, which is what the write lock exists for.
    """

    def test_replies_bigger_than_the_buffer_do_not_interleave(
        self, atf_process: Runner, project: Path
    ) -> None:
        # Well past the 8K a pipe buffers, so each reply is several flushes.
        make.write_tool(
            project,
            "bulky",
            script=make.python("sys.stdout.write('x' * 60000)\n"),
            run={"command": ["./run.sh"], "timeout_seconds": 20},
        )
        wanted = [call("bulky", request_id=200 + n) for n in range(6)]
        result = atf_process(
            "--workspace",
            str(project),
            "mcp-serve",
            "--tool",
            "bulky",
            stdin=frames(HANDSHAKE, *wanted),
        )
        assert result.code == 0
        # Every line whole, and every call answered exactly once.
        replies = parsed(result.out)
        assert sorted(reply["id"] for reply in replies) == [1, *(200 + n for n in range(6))]
        for reply in replies[1:]:
            assert len(reply["result"]["content"][0]["text"]) == 60000

    def test_a_ping_is_answered_while_tools_are_running(
        self, atf_process: Runner, project: Path
    ) -> None:
        """Answered on the read loop, so it does not queue behind the calls."""
        make.write_tool(
            project,
            "dawdle",
            script=make.sleeps(0.4),
            run={"command": ["./run.sh"], "timeout_seconds": 20},
        )
        result = atf_process(
            "--workspace",
            str(project),
            "mcp-serve",
            "--tool",
            "dawdle",
            stdin=frames(HANDSHAKE, call("dawdle", request_id=7), PING),
        )
        assert [reply["id"] for reply in parsed(result.out)] == [1, PING["id"], 7]


class TestCancellation:
    def test_a_cancelled_call_is_never_answered(
        self, atf_process: Runner, project: Path, tmp_path: Path
    ) -> None:
        """The whole path through a real process. Which of the two stops it, the check
        before the fork or the signal after, depends on scheduling and is not what this
        pins; `TestCancellingASpawn` pins the signal."""
        finished = tmp_path / "finished"
        make.write_tool(
            project,
            "blocker",
            script=make.finishes_later(tmp_path / "started", finished, seconds=3),
            run={"command": ["./run.sh"], "timeout_seconds": 30},
        )
        result = atf_process(
            "--workspace",
            str(project),
            "mcp-serve",
            "--tool",
            "blocker",
            stdin=frames(HANDSHAKE, call("blocker", request_id=3), cancelled(3)),
        )
        assert result.code == 0
        assert 3 not in by_id(parsed(result.out))
        # The server waits for its pool before exiting, so a tool that was only unanswered
        # would have reached its last line by the time this process ended.
        assert not finished.exists()


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
        answer = answered(parsed(result.out), 3)
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
        answer = answered(parsed(completed.stdout), 3)
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


class TestANamespacedGrant:
    """The other pair that has to agree, and nothing else checks it.

    A namespaced tool is offered without its slash, because a client builds
    `mcp__atf__<tool>` out of the name. `cli.mcp_server` decides that spelling and
    `adapters.claude_code` writes it into `--allowedTools`. Drift and the turn runs with a
    server whose every tool is unpermitted, which reads as a model saying they do not work.
    """

    def test_what_the_adapter_allows_is_what_the_server_offers(
        self, atf_process: Runner, project: Path
    ) -> None:
        make.write_tool(project, "common/shout", script=make.python("print('OK')\n"))
        result = atf_process(
            "--workspace",
            str(project),
            "mcp-serve",
            "--tool",
            "common/shout",
            stdin=frames(HANDSHAKE, LIST),
        )
        offered = [tool["name"] for tool in answered(parsed(result.out), 2)["result"]["tools"]]

        args = claude_code.build_args(
            {"prompt": "p", "tools": ["common/shout"], "tool_server": ["atf"]}
        )
        allowed = args[args.index("--allowedTools") + 1].split(",")
        assert allowed == [f"mcp__{mcp_server.SERVER_NAME}__{name}" for name in offered]

    def test_the_flat_name_reaches_the_tool_in_its_namespace(
        self, atf_process: Runner, project: Path
    ) -> None:
        make.write_tool(project, "common/shout", script=make.python("print('OK')\n"))
        result = atf_process(
            "--workspace",
            str(project),
            "mcp-serve",
            "--tool",
            "common/shout",
            stdin=frames(HANDSHAKE, call("common__shout")),
        )
        assert answered(parsed(result.out), 3)["result"]["content"][0]["text"].strip() == "OK"


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
        answer = answered(parsed(result.out), 3)
        assert answer["result"]["isError"] is True
        assert "outside the workspace root" in answer["result"]["content"][0]["text"]
