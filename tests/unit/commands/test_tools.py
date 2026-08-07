"""Describing and calling a tool outside a flow, which is what an agent's turn needs.

No doubles: every tool here is a directory on disk that the engine spawns as a process, so
what is checked is the same dispatch a step gets. That is the claim these two functions
make, and the one worth failing on.

The split worth keeping in mind: `describe_tools` raises and `call_tool` does not.
Describing happens during the handshake, where an unresolvable name is a flow that should
never have started. Calling happens mid-turn, where the model picked the arguments and can
pick different ones.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from commands import call_tool, describe_tools
from engine.executor import FlowError
from paths.resolver import Paths
from support import components as make


class TestDescribeTools:
    def test_the_order_asked_for_is_the_order_returned(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "alpha")
        make.write_tool(workspace, "zebra")
        assert [tool.name for tool in describe_tools(["zebra", "alpha"], paths)] == [
            "zebra",
            "alpha",
        ]

    def test_the_schema_comes_off_the_spec_unchanged(self, paths: Paths, workspace: Path) -> None:
        """`input_schema` is a plain JSON Schema, so it is handed over as it is written."""
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        make.write_tool(workspace, "reader", input_schema=schema)
        [tool] = describe_tools(["reader"], paths)
        assert tool.input_schema == schema

    def test_the_doc_is_appended_to_the_specs_description(
        self, paths: Paths, workspace: Path
    ) -> None:
        base = make.write_tool(workspace, "reader", description="Reads a file.", doc="tool.md")
        (base / "tool.md").write_text("Not for directories.")
        [tool] = describe_tools(["reader"], paths)
        assert tool.description == "Reads a file.\n\nNot for directories."

    def test_a_doc_that_is_not_there_is_not_an_error(self, paths: Paths, workspace: Path) -> None:
        base = make.write_tool(workspace, "reader", description="Reads a file.", doc="absent.md")
        (base / "tool.md").unlink()
        [tool] = describe_tools(["reader"], paths)
        assert tool.description == "Reads a file."

    def test_a_name_that_does_not_resolve_raises(self, paths: Paths) -> None:
        """It runs during the handshake, so this is a flow that should not have started."""
        with pytest.raises(FlowError, match="ghost"):
            describe_tools(["ghost"], paths)


class TestCallTool:
    def test_stdout_comes_back_as_the_result(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "greet", script=make.prints("hello"))
        call = call_tool("greet", {}, paths)
        assert call.ok is True
        assert call.text == "hello"

    def test_a_non_zero_exit_is_reported_rather_than_raised(
        self, paths: Paths, workspace: Path
    ) -> None:
        """Raising would end the turn over something the model can recover from."""
        make.write_tool(workspace, "reader", script=make.fails(3), exit_codes={"3": "no such file"})
        call = call_tool("reader", {}, paths)
        assert call.ok is False
        assert call.error is not None
        assert "no such file" in call.error

    def test_arguments_the_schema_rejects_are_reported_the_same_way(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(
            workspace,
            "reader",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )
        call = call_tool("reader", {"bogus": 1}, paths)
        assert call.ok is False
        assert call.error is not None
        assert "bogus" in call.error

    def test_a_name_that_does_not_resolve_is_reported_not_raised(self, paths: Paths) -> None:
        call = call_tool("ghost", {}, paths)
        assert call.ok is False
        assert call.error is not None
        assert "ghost" in call.error

    def test_a_tool_called_in_a_turn_runs_in_the_workspace(
        self, paths: Paths, workspace: Path
    ) -> None:
        """The same cwd a tool step gets. There is no second way to run a tool."""
        make.write_tool(workspace, "pwd", script=make.python("sys.stdout.write(os.getcwd())\n"))
        assert call_tool("pwd", {}, paths).text == str(workspace)

    def test_the_payload_reaches_the_tool_as_a_step_input_would(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "echo", script=make.ECHO_STDIN)
        assert call_tool("echo", {"path": "a.md"}, paths).text == '{"path": "a.md"}'

    def test_a_timeout_is_the_tools_own(self, paths: Paths, workspace: Path) -> None:
        """A step's timeout and an in-turn call's are the same number from the same spec."""
        make.write_tool(
            workspace,
            "slow",
            script=make.sleeps(5),
            run={"command": ["./run.sh"], "timeout_seconds": 0.2},
        )
        call = call_tool("slow", {}, paths)
        assert call.ok is False
        assert call.error is not None
        assert "timeout" in call.error

    def test_how_long_it_took_is_recorded(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "greet", script=make.prints("hi"))
        assert call_tool("greet", {}, paths).ms >= 0

    def test_a_spec_that_stopped_parsing_is_reported_rather_than_raised(
        self, paths: Paths, workspace: Path
    ) -> None:
        """Not something the model can act on, but ending a paid-for turn is worse."""
        base = make.write_tool(workspace, "broken")
        (base / "spec.json").write_text("{not json")
        call = call_tool("broken", {}, paths)
        assert call.ok is False
        assert call.error is not None
        assert "not valid JSON" in call.error

    def test_no_vault_secret_is_in_reach(
        self, paths: Paths, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An in-turn call gets no grant of its own. `validate()` refuses the combination
        that would have put one in this process's environment to inherit."""
        monkeypatch.delenv("ATF_PROBE_TOKEN", raising=False)
        make.write_tool(workspace, "peek", script=make.echoes_env("ATF_PROBE_TOKEN"))
        assert call_tool("peek", {}, paths).text == ""

    def test_it_reads_the_environment_this_process_was_given(
        self, paths: Paths, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Which is the ordinary environment, exactly as a tool step's would be."""
        monkeypatch.setenv("ATF_PROBE_TOKEN", "visible")
        make.write_tool(workspace, "peek", script=make.echoes_env("ATF_PROBE_TOKEN"))
        assert call_tool("peek", {}, paths).text == "visible"
        assert os.environ["ATF_PROBE_TOKEN"] == "visible"
