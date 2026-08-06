"""What is installed, and where the engine looked for it.

Both answer the question that comes up when a name does not resolve to what you expected,
so the part worth testing is the part that answers it: shadowing, and a root that exists
but has nothing in it.
"""

from __future__ import annotations

from pathlib import Path

import adapters
import commands
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
        assert commands.inventory(paths).adapters == adapters.describe()

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
