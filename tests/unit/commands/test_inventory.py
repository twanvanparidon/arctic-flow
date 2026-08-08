"""What is installed, and where the engine looked for it.

Both answer the question that comes up when a name does not resolve to what you expected,
so the part worth testing is the part that answers it: shadowing, and a root that exists
but has nothing in it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import adapters
import commands
from adapters import AdapterError
from engine.executor import FlowError
from paths.resolver import Paths, builtin_root
from support import components as make


class TestInventory:
    def test_flows_are_listed_first(self, paths: Paths) -> None:
        """A listing reads top-down from what you run to what it is built from."""
        assert [listing.kind for listing in commands.inventory(paths).kinds] == [
            "flow",
            "tool",
            "agent",
        ]

    def test_adapters_are_reported_separately(self, paths: Paths) -> None:
        """They are registered in code, so they have no roots and nothing can shadow them."""
        listed = commands.inventory(paths).adapters
        assert [entry.name for entry in listed] == adapters.names()
        assert all(entry.shadows == () for entry in listed)

    def test_an_adapter_reports_the_module_it_is(self, paths: Paths) -> None:
        entry = next(e for e in commands.inventory(paths).adapters if e.name == "echo")
        assert entry.path == adapters.locate(adapters.get("echo"))

    def test_a_kind_with_nothing_installed_is_empty_rather_than_absent(self, paths: Paths) -> None:
        agents = next(k for k in commands.inventory(paths).kinds if k.kind == "agent")
        assert agents.entries == ()

    def test_an_entry_carries_the_definition_that_wins(self, paths: Paths, workspace: Path) -> None:
        base = make.write_tool(workspace, "greet")
        entry = _entry(paths, "tool", "greet")
        assert entry.path == base
        assert entry.display == "./tools/greet"

    def test_an_entry_names_what_it_is_hiding(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        make.write_tool(home / ".arctic", "greet")
        make.write_tool(workspace, "greet")
        assert _entry(paths, "tool", "greet").shadows == ("~/.arctic/tools/greet",)

    def test_something_shadowing_nothing_says_nothing(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "greet")
        assert _entry(paths, "tool", "greet").shadows == ()

    def test_the_built_in_tools_are_in_the_listing(self, paths: Paths) -> None:
        assert _entry(paths, "tool", "read_file").path == builtin_root() / "tools" / "read_file"


def _entry(paths: Paths, kind: str, name: str) -> commands.ComponentEntry:
    listing = next(k for k in commands.inventory(paths).kinds if k.kind == kind)
    return next(entry for entry in listing.entries if entry.name == name)


class TestSearchPaths:
    def test_the_roots_come_back_in_precedence_order(self, paths: Paths) -> None:
        report = commands.search_paths(paths)
        assert [root.path for root in report.roots] == paths.roots

    def test_it_reports_the_working_directory_components_run_in(
        self, paths: Paths, workspace: Path
    ) -> None:
        assert commands.search_paths(paths).workspace == workspace

    def test_a_root_lists_what_it_actually_contains(self, paths: Paths, workspace: Path) -> None:
        """Not what it could contain. A root listed with nothing in it is the answer to
        "why is my tool not found"."""
        make.write_tool(workspace, "greet")
        make.write_flow(workspace, "demo", {"flow": "demo"})
        report = commands.search_paths(paths)
        project = next(root for root in report.roots if root.path == workspace)
        assert project.subdirs == ("flows", "tools")

    def test_an_empty_root_reports_no_subdirectories(self, paths: Paths, workspace: Path) -> None:
        report = commands.search_paths(paths)
        project = next(root for root in report.roots if root.path == workspace)
        assert project.subdirs == ()


class TestAgentDetail:
    def test_it_carries_the_prompt_a_turn_would_be_handed(
        self, paths: Paths, workspace: Path
    ) -> None:
        """Read through the same loader `run` uses, so the two cannot show different text."""
        make.write_agent(workspace, "writer", prompt="Answer in haiku.")
        assert commands.agent_detail("writer", paths).prompt == "Answer in haiku."

    def test_it_carries_the_spec_as_written(self, paths: Paths, workspace: Path) -> None:
        make.write_agent(workspace, "writer", model="sonnet")
        detail = commands.agent_detail("writer", paths)
        assert detail.spec["adapter"] == "echo"
        assert detail.spec["model"] == "sonnet"

    def test_it_reports_the_definition_that_won(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        """The reason the command exists: a name can resolve to a prompt from another root."""
        make.write_agent(home / ".arctic", "writer", prompt="from home")
        make.write_agent(workspace, "writer", prompt="from the project")
        detail = commands.agent_detail("writer", paths)
        assert detail.prompt == "from the project"
        assert detail.display == "./agents/writer"

    def test_an_agent_that_is_not_there_says_where_it_looked(self, paths: Paths) -> None:
        with pytest.raises(FlowError, match="unknown agent 'absent'"):
            commands.agent_detail("absent", paths)

    def test_a_missing_prompt_file_says_which_file(self, paths: Paths, workspace: Path) -> None:
        make.write_agent(workspace, "writer", write_prompt=False)
        with pytest.raises(FlowError, match="agent.md"):
            commands.agent_detail("writer", paths)


class TestAdapterDetail:
    def test_it_carries_the_schema_an_agent_spec_is_checked_against(self, paths: Paths) -> None:
        """The same `INPUT_SCHEMA` `engine.specs` validates a spec's settings against, so
        the answer to "may I set this" and the check that enforces it are one thing."""
        detail = commands.adapter_detail("echo", paths)
        assert detail.input_schema is adapters.get("echo").INPUT_SCHEMA

    def test_it_comes_from_the_registry_rather_than_the_lookup(self, paths: Paths) -> None:
        assert commands.adapter_detail("echo", paths).path.name == "echo.py"

    def test_an_unknown_adapter_names_the_ones_there_are(self, paths: Paths) -> None:
        """There is no `~/.arctic/adapters/`, so "install it" is not the advice to give."""
        with pytest.raises(AdapterError, match="Available: claude_code, echo"):
            commands.adapter_detail("gpt", paths)


class TestToolDetail:
    def test_it_carries_the_spec_as_written(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "greet", secrets=["token"])
        detail = commands.tool_detail("greet", paths)
        assert detail.spec["permissions"] == {"filesystem": "none"}
        assert detail.spec["secrets"] == ["token"]

    def test_it_carries_the_doc_the_spec_names(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "greet", doc="tool.md")
        assert commands.tool_detail("greet", paths).doc == "# greet"

    def test_a_tool_with_no_doc_is_not_a_failure(self, paths: Paths, workspace: Path) -> None:
        """`TOOL_SPEC_SCHEMA` does not require one, so refusing here would reject a tool
        the engine is otherwise happy to run."""
        make.write_tool(workspace, "greet")
        assert commands.tool_detail("greet", paths).doc == ""

    def test_the_name_is_the_one_it_was_looked_up_by(self, paths: Paths, workspace: Path) -> None:
        """A namespaced tool's spec carries only the leaf, and a caller asked for the path."""
        make.write_tool(workspace, "common/greet")
        detail = commands.tool_detail("common/greet", paths)
        assert detail.name == "common/greet"
        assert detail.spec["name"] == "greet"

    def test_a_tool_that_is_not_there_says_where_it_looked(self, paths: Paths) -> None:
        with pytest.raises(FlowError, match="unknown tool 'absent'"):
            commands.tool_detail("absent", paths)
