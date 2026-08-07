"""The MCP server: the frames it answers, and the two it deliberately does not.

Every collaborator here is real. The tools are directories written to disk and spawned as
processes, `commands.describe_tools` and `commands.call_tool` are the real ones, and the
only thing substituted is the stream, which `monkeypatch.setattr(sys, "stdin", ...)` sets
to a real file object. That is environment control rather than a double.

Two behaviours carry the weight, and both are reasons rather than shapes: a notification
must never be answered, and a bad *call* is a result the model can act on while a bad
*method* is a protocol error the model cannot.

Reading `capsys` after `serve()` returns is deterministic even though calls run on a pool,
because the pool is left through a `with` and its shutdown joins every worker. Replies are
keyed by id rather than indexed, since a call answers when it finishes and not when it was
asked for.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from cli import branding, mcp_server
from paths.resolver import Paths
from support import components as make
from support.mcp import by_id, cancelled


def answers(
    frames: list[dict[str, Any]],
    names: list[str],
    paths: Paths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    events: Path | None = None,
) -> list[dict[str, Any]]:
    """Feed newline-delimited JSON in, take the parsed lines out."""
    written = "".join(json.dumps(frame) + "\n" for frame in frames)
    monkeypatch.setattr("sys.stdin", io.StringIO(written))
    assert mcp_server.serve(names, paths, events) == 0
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line]


HANDSHAKE = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}


def call(name: str, *, request_id: int = 2, **arguments: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


class TestInitialize:
    def test_the_handshake_names_the_server_and_the_build(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        [reply] = answers([HANDSHAKE], [], paths, monkeypatch, capsys)
        assert reply["result"]["serverInfo"] == {
            "name": mcp_server.SERVER_NAME,
            "version": branding.__version__,
        }

    def test_a_clients_own_revision_is_echoed_back(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """These three methods are unchanged across every revision, so refusing one we
        would have satisfied anyway buys nothing."""
        frame = {**HANDSHAKE, "params": {"protocolVersion": "2024-11-05"}}
        [reply] = answers([frame], [], paths, monkeypatch, capsys)
        assert reply["result"]["protocolVersion"] == "2024-11-05"

    def test_stating_no_revision_gets_the_one_this_speaks(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        [reply] = answers([HANDSHAKE], [], paths, monkeypatch, capsys)
        assert reply["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION

    def test_it_advertises_tools_and_nothing_else(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        [reply] = answers([HANDSHAKE], [], paths, monkeypatch, capsys)
        assert reply["result"]["capabilities"] == {"tools": {}}


class TestToolsList:
    def test_each_granted_tool_carries_its_own_input_schema(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        make.write_tool(workspace, "reader", input_schema=schema)
        frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        [reply] = answers([frame], ["reader"], paths, monkeypatch, capsys)
        assert reply["result"]["tools"][0]["inputSchema"] == schema

    def test_the_description_carries_the_doc_a_model_reads(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A flow author picks a tool from the YAML; a model picks from this."""
        base = make.write_tool(workspace, "reader", doc="tool.md")
        (base / "tool.md").write_text("# reader\n\nDo not use this on a directory.")
        frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        [reply] = answers([frame], ["reader"], paths, monkeypatch, capsys)
        assert "Do not use this on a directory." in reply["result"]["tools"][0]["description"]

    def test_a_tool_with_no_doc_is_still_listed(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """check_tool_spec does not require one, so refusing here would reject a runnable tool."""
        make.write_tool(workspace, "reader")
        frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        [reply] = answers([frame], ["reader"], paths, monkeypatch, capsys)
        assert reply["result"]["tools"][0]["name"] == "reader"

    def test_nothing_but_the_granted_names_is_offered(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        make.write_tool(workspace, "granted")
        make.write_tool(workspace, "withheld")
        frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        [reply] = answers([frame], ["granted"], paths, monkeypatch, capsys)
        assert [tool["name"] for tool in reply["result"]["tools"]] == ["granted"]

    def test_a_tool_that_vanished_is_reported_and_the_session_survives(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        replies = by_id(
            answers([frame, {**HANDSHAKE, "id": 2}], ["ghost"], paths, monkeypatch, capsys)
        )
        assert replies[1]["error"]["code"] == mcp_server.INTERNAL_ERROR
        assert 2 in replies


class TestToolsCall:
    def test_a_tool_that_ran_returns_its_stdout(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        make.write_tool(workspace, "greet", script=make.prints("hello"))
        [reply] = answers([call("greet")], ["greet"], paths, monkeypatch, capsys)
        assert reply["result"]["isError"] is False
        assert reply["result"]["content"][0]["text"] == "hello"

    def test_a_tool_that_failed_is_a_result_the_model_can_act_on(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The model chose the arguments, so it is the party that can fix them. A JSON-RPC
        error would tell it nothing, and the exit code's own wording is what it can use."""
        make.write_tool(workspace, "reader", script=make.fails(3), exit_codes={"3": "no such file"})
        [reply] = answers([call("reader")], ["reader"], paths, monkeypatch, capsys)
        assert "error" not in reply
        assert reply["result"]["isError"] is True
        assert "no such file" in reply["result"]["content"][0]["text"]

    def test_arguments_the_tools_schema_rejects_come_back_the_same_way(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        make.write_tool(workspace, "reader", input_schema={**schema, "additionalProperties": False})
        [reply] = answers([call("reader", bogus=1)], ["reader"], paths, monkeypatch, capsys)
        assert reply["result"]["isError"] is True
        assert "bogus" in reply["result"]["content"][0]["text"]

    def test_a_name_that_was_not_granted_names_what_is(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Picking another name is a recovery, so what may be called is worth saying."""
        make.write_tool(workspace, "granted")
        [reply] = answers([call("withheld")], ["granted"], paths, monkeypatch, capsys)
        assert reply["result"]["isError"] is True
        assert "granted" in reply["result"]["content"][0]["text"]

    def test_nothing_granted_says_none(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        [reply] = answers([call("anything")], [], paths, monkeypatch, capsys)
        assert "none" in reply["result"]["content"][0]["text"]


class TestReportingCalls:
    def test_each_call_is_appended_for_the_engine_to_read(
        self,
        paths: Paths,
        workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without this an in-turn call is invisible: one step, and no record of the calls."""
        make.write_tool(workspace, "greet", script=make.prints("hi"))
        events = tmp_path / "calls.ndjson"
        answers([call("greet")], ["greet"], paths, monkeypatch, capsys, events=events)
        reported = [json.loads(line) for line in events.read_text().splitlines()]
        assert reported[0]["tool"] == "greet"
        assert reported[0]["ok"] is True

    def test_a_failed_call_is_reported_too(
        self,
        paths: Paths,
        workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        make.write_tool(workspace, "reader", script=make.fails(3))
        events = tmp_path / "calls.ndjson"
        answers([call("reader")], ["reader"], paths, monkeypatch, capsys, events=events)
        assert json.loads(events.read_text().splitlines()[0])["ok"] is False

    def test_a_name_that_was_never_granted_reports_nothing(
        self,
        paths: Paths,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No tool ran, so there is no call to report."""
        events = tmp_path / "calls.ndjson"
        events.touch()
        answers([call("withheld")], [], paths, monkeypatch, capsys, events=events)
        assert events.read_text() == ""

    def test_a_turn_with_nowhere_to_report_still_answers(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        make.write_tool(workspace, "greet", script=make.prints("hi"))
        [reply] = answers([call("greet")], ["greet"], paths, monkeypatch, capsys, events=None)
        assert reply["result"]["isError"] is False


class TestConcurrency:
    def test_two_calls_run_at_the_same_time(
        self,
        paths: Paths,
        workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Two mirrored rendezvous tools each finish only if the other was also running.

        No clock in the assertion: answered one at a time, the first exhausts its wait and
        exits 9, so the outcome differs rather than the duration. Its deadline is well
        under the tools' own timeout, or which of the two fires would be a coin flip.
        """
        left = make.rendezvous(tmp_path / "left.flag", tmp_path / "right.flag", timeout=5)
        right = make.rendezvous(tmp_path / "right.flag", tmp_path / "left.flag", timeout=5)
        run = {"command": ["./run.sh"], "timeout_seconds": 20}
        make.write_tool(workspace, "meet_left", script=left, run=run)
        make.write_tool(workspace, "meet_right", script=right, run=run)

        replies = by_id(
            answers(
                [call("meet_left", request_id=10), call("meet_right", request_id=11)],
                ["meet_left", "meet_right"],
                paths,
                monkeypatch,
                capsys,
            )
        )
        assert replies[10]["result"]["content"][0]["text"] == "met"
        assert replies[11]["result"]["content"][0]["text"] == "met"

    def test_every_call_is_answered_even_when_more_arrive_than_run_at_once(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Past the limit a call waits for a worker rather than being refused."""
        make.write_tool(workspace, "greet", script=make.prints("hi"))
        wanted = mcp_server.MAX_CONCURRENT_CALLS + 3
        frames = [call("greet", request_id=100 + n) for n in range(wanted)]
        replies = by_id(answers(frames, ["greet"], paths, monkeypatch, capsys))
        assert sorted(replies) == [100 + n for n in range(wanted)]

    def test_a_call_still_answered_when_stdin_closes_under_it(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Leaving the pool waits, so a client that sent a call and closed still gets it."""
        make.write_tool(
            workspace,
            "dawdle",
            script=make.sleeps(0.3),
            run={"command": ["./run.sh"], "timeout_seconds": 20},
        )
        [reply] = answers([call("dawdle")], ["dawdle"], paths, monkeypatch, capsys)
        assert reply["result"]["isError"] is False


class TestPing:
    def test_it_is_answered_with_an_empty_result(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The spec says a receiver answers one promptly, with no exemption for being busy."""
        frame = {"jsonrpc": "2.0", "id": 5, "method": "ping"}
        [reply] = answers([frame], [], paths, monkeypatch, capsys)
        assert reply["result"] == {}

    def test_it_is_answered_while_a_tool_is_still_running(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The observable proof the read loop is not blocked: the pong overtakes the call."""
        make.write_tool(
            workspace,
            "dawdle",
            script=make.sleeps(0.3),
            run={"command": ["./run.sh"], "timeout_seconds": 20},
        )
        frames = [call("dawdle", request_id=7), {"jsonrpc": "2.0", "id": 5, "method": "ping"}]
        replies = answers(frames, ["dawdle"], paths, monkeypatch, capsys)
        assert [reply["id"] for reply in replies] == [5, 7]


class TestCancellation:
    """A withdrawn request is answered with nothing, and its tool is really stopped.

    What is pinned here is the wiring: the reply is suppressed, the call is reported as
    cancelled rather than failed, and the other calls carry on. That the tool never reaches
    its last line is asserted too, but it does not separate a kill from a skip, because
    stdin is buffered whole and the reader reaches the cancel before a worker can fork. The
    stop itself, TERM to a whole process tree, is pinned where it lives:
    `tests/unit/engine/test_components.py::TestCancellingASpawn`, which uses a rendezvous to
    guarantee the process is running first.

    Deferred for the same reason: a cancel arriving *after* its call was answered. Reaching
    it needs a live pipe the test writes to mid-flight. It is the same pop-finds-nothing
    path as an unknown id, which is covered below.
    """

    def blocker(self, workspace: Path, tmp_path: Path) -> Path:
        """A tool that leaves a marker three seconds in, unless it is stopped first."""
        finished = tmp_path / "finished"
        make.write_tool(
            workspace,
            "blocker",
            script=make.finishes_later(tmp_path / "started", finished, seconds=3),
            run={"command": ["./run.sh"], "timeout_seconds": 20},
        )
        return finished

    def test_a_cancelled_call_is_never_answered(
        self,
        paths: Paths,
        workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        finished = self.blocker(workspace, tmp_path)
        replies = by_id(
            answers(
                [call("blocker", request_id=3), cancelled(3)],
                ["blocker"],
                paths,
                monkeypatch,
                capsys,
            )
        )
        assert 3 not in replies
        # And it did not run to completion: serve() waits for its pool, so a tool left
        # running would have reached its last line before this returned.
        assert not finished.exists()

    def test_a_cancelled_call_is_reported_as_cancelled_rather_than_failed(
        self,
        paths: Paths,
        workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The engine reads this file. A withdrawn call is not a tool that broke."""
        self.blocker(workspace, tmp_path)
        events = tmp_path / "calls.ndjson"
        answers(
            [call("blocker", request_id=3), cancelled(3)],
            ["blocker"],
            paths,
            monkeypatch,
            capsys,
            events=events,
        )
        [reported] = [json.loads(line) for line in events.read_text().splitlines()]
        assert reported["ok"] is False
        assert reported["cancelled"] is True

    def test_a_cancel_for_an_id_that_was_never_seen_is_a_no_op(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The spec permits ignoring one, and a notification is answered with nothing."""
        assert answers([cancelled(999)], [], paths, monkeypatch, capsys) == []

    def test_other_calls_are_untouched_by_one_cancel(
        self,
        paths: Paths,
        workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self.blocker(workspace, tmp_path)
        make.write_tool(workspace, "greet", script=make.prints("hi"))
        frames = [
            call("blocker", request_id=3),
            call("greet", request_id=4),
            cancelled(3),
        ]
        replies = by_id(answers(frames, ["blocker", "greet"], paths, monkeypatch, capsys))
        assert 3 not in replies
        assert replies[4]["result"]["content"][0]["text"] == "hi"


class TestNotifications:
    def test_a_frame_with_no_id_is_never_answered(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Replying to notifications/initialized wedges the handshake."""
        frame = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert answers([frame], [], paths, monkeypatch, capsys) == []

    def test_a_notification_between_two_requests_does_not_shift_the_answers(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        frames = [
            HANDSHAKE,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {**HANDSHAKE, "id": 7},
        ]
        replies = answers(frames, [], paths, monkeypatch, capsys)
        assert sorted(reply["id"] for reply in replies) == [1, 7]

    def test_an_explicit_null_id_is_a_notification_too(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        frame = {"jsonrpc": "2.0", "id": None, "method": "notifications/cancelled"}
        assert answers([frame], [], paths, monkeypatch, capsys) == []


class TestFraming:
    def test_a_blank_line_is_skipped(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("\n\n" + json.dumps(HANDSHAKE) + "\n"))
        assert mcp_server.serve([], paths, None) == 0
        assert len(capsys.readouterr().out.splitlines()) == 1

    def test_something_that_is_not_json_is_a_parse_error(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("not json at all\n"))
        assert mcp_server.serve([], paths, None) == 0
        reply = json.loads(capsys.readouterr().out)
        assert reply["error"]["code"] == mcp_server.PARSE_ERROR
        assert reply["id"] is None

    def test_an_unsupported_method_is_method_not_found(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A method is the client's choice, unlike a call's arguments, so this one is a
        protocol error where a failed tool call is not."""
        frame = {"jsonrpc": "2.0", "id": 1, "method": "resources/list"}
        [reply] = answers([frame], [], paths, monkeypatch, capsys)
        assert reply["error"]["code"] == mcp_server.METHOD_NOT_FOUND

    def test_one_bad_frame_does_not_end_the_session(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Letting it escape closes stdout mid-turn, and the client then reports a
        transport failure that says nothing about the cause."""
        broken = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": 5}
        replies = by_id(answers([broken, {**HANDSHAKE, "id": 2}], [], paths, monkeypatch, capsys))
        assert replies[1]["error"]["code"] == mcp_server.INTERNAL_ERROR
        assert 2 in replies

    def test_every_answer_is_exactly_one_line(
        self,
        paths: Paths,
        workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """One stray newline in a tool's output would corrupt the framing for the rest."""
        make.write_tool(workspace, "chatty", script=make.prints("one\ntwo\nthree"))
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call("chatty")) + "\n"))
        assert mcp_server.serve(["chatty"], paths, None) == 0
        assert len(capsys.readouterr().out.splitlines()) == 1

    def test_it_returns_zero_when_stdin_closes(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        assert mcp_server.serve([], paths, None) == 0
