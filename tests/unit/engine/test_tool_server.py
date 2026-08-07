"""The argv that re-invokes this engine as a tool server, and what it reports back.

`tool_server_command` is pure, so these read it directly. `sys.frozen` and `sys.argv` are
set with monkeypatch, which is environment control rather than a double: they are real
values that real code reads, and the frozen branch cannot be reached any other way.

The reporter is exercised against a real file, because that is what it reads. The server
writing that file is a different process, so a test that handed it a list would be testing
a design nobody ships.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from cli.app import build_parser
from engine.executor import ToolCallReporter, tool_calls_reported, tool_server_command
from paths.resolver import Paths

EVENTS = Path("/tmp/events.ndjson")


class TestToolServerCommand:
    def test_a_checkout_is_the_interpreter_and_the_entry_point(self, paths: Paths) -> None:
        command = tool_server_command(paths, ["reader"], EVENTS)
        assert command[0] == sys.executable
        assert command[1] == str(Path(sys.argv[0]).resolve())

    def test_a_frozen_build_is_the_executable_itself(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A frozen build *is* the executable, so there is no script to name after it."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        command = tool_server_command(paths, ["reader"], EVENTS)
        assert command[0] == sys.executable
        assert command[1] == "--workspace"

    def test_a_relative_entry_point_is_made_absolute(
        self, paths: Paths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`python3 src/main.py` leaves it relative, and the runtime starts the server from
        a directory of its own choosing, where it would not resolve."""
        monkeypatch.setattr(sys, "argv", ["src/main.py"])
        assert Path(tool_server_command(paths, [], EVENTS)[1]).is_absolute()

    def test_the_workspace_is_passed_rather_than_inherited(self, paths: Paths) -> None:
        """A global flag, so it precedes the subcommand. Passing it keeps the tool lookup
        identical to this process's wherever the runtime starts the server."""
        command = tool_server_command(paths, [], EVENTS)
        assert command[command.index("--workspace") + 1] == str(paths.workspace)
        assert command.index("--workspace") < command.index("mcp-serve")

    def test_each_tool_is_named_by_its_own_flag(self, paths: Paths) -> None:
        command = tool_server_command(paths, ["alpha", "zebra"], EVENTS)
        named = [command[i + 1] for i, token in enumerate(command) if token == "--tool"]
        assert named == ["alpha", "zebra"]

    def test_granting_nothing_names_no_tool(self, paths: Paths) -> None:
        assert "--tool" not in tool_server_command(paths, [], EVENTS)

    def test_where_to_report_rides_along(self, paths: Paths) -> None:
        command = tool_server_command(paths, [], EVENTS)
        assert command[command.index("--events") + 1] == str(EVENTS)

    def test_the_argv_it_builds_is_the_argv_the_parser_takes(self, paths: Paths) -> None:
        """Two real halves of one convention, and nothing else checks that they agree."""
        command = tool_server_command(paths, ["alpha", "zebra"], EVENTS)
        # Everything after the launcher is what the CLI itself would be handed.
        launcher = 1 if getattr(sys, "frozen", False) else 2
        args = build_parser().parse_args(command[launcher:])
        assert args.tools == ["alpha", "zebra"]
        assert args.workspace == paths.workspace
        assert args.events == EVENTS


class TestToolCallReporter:
    def forwarded(self, path: Path, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: list[dict[str, Any]] = []
        reporter = ToolCallReporter(path, seen.append, "draft")
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        reporter.drain()
        return seen

    def test_a_call_arrives_as_an_event_naming_its_step(self, tmp_path: Path) -> None:
        """The server knows nothing about the flow, so the step id is attached here."""
        [event] = self.forwarded(tmp_path / "e", [{"tool": "reader", "ok": True, "ms": 4}])
        assert event == {
            "kind": "tool_call",
            "step": "draft",
            "tool": "reader",
            "ok": True,
            "ms": 4,
        }

    def test_nothing_written_is_nothing_reported(self, tmp_path: Path) -> None:
        assert self.forwarded(tmp_path / "e", []) == []

    def test_a_file_that_is_not_there_is_not_an_error(self, tmp_path: Path) -> None:
        """The server may never have been spawned, which is not this reporter's problem."""
        seen: list[dict[str, Any]] = []
        ToolCallReporter(tmp_path / "absent", seen.append, "draft").drain()
        assert seen == []

    def test_draining_twice_does_not_report_a_call_twice(self, tmp_path: Path) -> None:
        path = tmp_path / "e"
        seen: list[dict[str, Any]] = []
        reporter = ToolCallReporter(path, seen.append, "draft")
        path.write_text(json.dumps({"tool": "a", "ok": True, "ms": 1}) + "\n")
        reporter.drain()
        reporter.drain()
        assert len(seen) == 1

    def test_a_call_appended_after_a_drain_is_picked_up_by_the_next(self, tmp_path: Path) -> None:
        path = tmp_path / "e"
        seen: list[dict[str, Any]] = []
        reporter = ToolCallReporter(path, seen.append, "draft")
        path.write_text(json.dumps({"tool": "a", "ok": True, "ms": 1}) + "\n")
        reporter.drain()
        with path.open("a") as stream:
            stream.write(json.dumps({"tool": "b", "ok": False, "ms": 2}) + "\n")
        reporter.drain()
        assert [event["tool"] for event in seen] == ["a", "b"]

    def test_a_half_written_line_is_held_until_it_is_whole(self, tmp_path: Path) -> None:
        """The server appends and flushes per call, so the rest arrives on the next drain."""
        path = tmp_path / "e"
        seen: list[dict[str, Any]] = []
        reporter = ToolCallReporter(path, seen.append, "draft")
        path.write_text('{"tool": "a", "ok": tr')
        reporter.drain()
        assert seen == []
        path.write_text(json.dumps({"tool": "a", "ok": True, "ms": 1}) + "\n")
        reporter.drain()
        assert [event["tool"] for event in seen] == ["a"]


class TestToolCallsReported:
    def test_it_yields_a_command_naming_the_file_it_watches(self, paths: Paths) -> None:
        with tool_calls_reported(paths, ["reader"], lambda _event: None, "draft") as command:
            events = Path(command[command.index("--events") + 1])
            assert events.is_file()

    def test_what_the_server_wrote_is_reported_before_it_gives_up_the_file(
        self, paths: Paths
    ) -> None:
        """The last call usually lands between two polls, so the final drain is the one
        that matters."""
        seen: list[dict[str, Any]] = []
        with tool_calls_reported(paths, ["reader"], seen.append, "draft") as command:
            events = Path(command[command.index("--events") + 1])
            with events.open("a") as stream:
                stream.write(json.dumps({"tool": "reader", "ok": True, "ms": 3}) + "\n")
        assert [event["tool"] for event in seen] == ["reader"]

    def test_the_file_does_not_outlive_the_turn(self, paths: Paths) -> None:
        with tool_calls_reported(paths, [], lambda _event: None, "draft") as command:
            events = Path(command[command.index("--events") + 1])
        assert not events.exists()
