"""Layered lookup: which definition of a name wins, and where the engine looked.

The precedence list is the whole feature. A project overrides what it inherits by putting a
directory of the same name higher up the list, with no config file anywhere, so getting the
order or the de-duplication wrong changes which code runs without changing any flow.

Every fixture pins both the workspace and the home directory inside tmp_path. `~/.arctic`
is a real search root, and a developer's own tools must not be able to shadow a test's.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from paths.resolver import LookupError_, Paths, builtin_root
from support import components as make


class TestBuiltinRoot:
    def test_points_at_the_components_that_ship_with_the_engine(self) -> None:
        assert (builtin_root() / "tools" / "read_file" / "spec.json").is_file()

    def test_sits_beside_the_paths_package(self) -> None:
        """One expression for all three ways the engine runs, so there is no frozen branch."""
        assert builtin_root().name == "builtin"


class TestConstruction:
    def test_the_workspace_is_resolved_to_an_absolute_path(self, tmp_path: Path) -> None:
        nested = tmp_path / "project" / "sub"
        nested.mkdir(parents=True)
        assert Paths(nested / ".." / "sub", env={}, home=tmp_path).workspace == nested

    def test_a_workspace_given_as_a_string_is_accepted(self, workspace: Path) -> None:
        assert Paths(str(workspace), env={}).workspace == workspace

    def test_home_defaults_to_the_one_the_process_has(self, workspace: Path, home: Path) -> None:
        """`~/.arctic` is a search root, so leaving it out has to mean the user's own."""
        assert Paths(workspace, env={}).home == Path.home() == home


class TestRoots:
    def test_the_order_is_project_then_home_then_builtin(self, workspace: Path, home: Path) -> None:
        (workspace / ".arctic").mkdir()
        (home / ".arctic").mkdir()
        assert Paths(workspace, env={}, home=home).roots == [
            workspace / ".arctic",
            workspace,
            home / ".arctic",
            builtin_root(),
        ]

    def test_a_root_that_does_not_exist_is_dropped(self, workspace: Path, home: Path) -> None:
        """Listing a directory nobody created would make `paths` read as a promise."""
        assert (workspace / ".arctic") not in Paths(workspace, env={}, home=home).roots

    def test_atf_path_comes_first(self, workspace: Path, home: Path, tmp_path: Path) -> None:
        override = tmp_path / "override"
        override.mkdir()
        paths = Paths(workspace, env={"ATF_PATH": str(override)}, home=home)
        assert paths.roots[0] == override

    def test_atf_path_takes_several_entries_in_order(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        first, second = tmp_path / "one", tmp_path / "two"
        first.mkdir()
        second.mkdir()
        env = {"ATF_PATH": os.pathsep.join([str(first), str(second)])}
        assert Paths(workspace, env=env, home=home).roots[:2] == [first, second]

    @pytest.mark.parametrize("value", ["", "   ", "::"])
    def test_an_empty_atf_path_contributes_nothing(
        self, workspace: Path, home: Path, value: str
    ) -> None:
        assert Paths(workspace, env={"ATF_PATH": value}, home=home).roots == [
            workspace,
            builtin_root(),
        ]

    def test_a_tilde_in_atf_path_is_expanded(self, workspace: Path, home: Path) -> None:
        # expanduser() answers to $HOME rather than to this object's `home`. The two are the
        # same directory here because conftest points $HOME at the temporary one.
        (home / "shared").mkdir()
        paths = Paths(workspace, env={"ATF_PATH": "~/shared"}, home=home)
        assert paths.roots[0] == home / "shared"

    def test_a_duplicate_root_is_searched_once(self, workspace: Path, home: Path) -> None:
        """Running from the engine's own checkout collapses several of these onto one."""
        paths = Paths(workspace, env={"ATF_PATH": str(workspace)}, home=home)
        assert paths.roots.count(workspace) == 1


class TestFinding:
    def test_finds_a_tool_by_name(self, paths: Paths, workspace: Path) -> None:
        base = make.write_tool(workspace, "greet")
        assert paths.find("tool", "greet") == base

    def test_finds_a_flow_by_either_suffix(self, paths: Paths, workspace: Path) -> None:
        path = make.write_text_flow(workspace, "later", "flow: later\n", suffix=".yml")
        assert paths.find("flow", "later") == path

    def test_the_first_root_wins(self, paths: Paths, workspace: Path, home: Path) -> None:
        make.write_tool(home / ".arctic", "greet")
        project = make.write_tool(workspace, "greet")
        assert paths.find("tool", "greet") == project

    def test_find_all_reports_what_is_being_shadowed(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        """More than one match is the usual reason an edit appears to do nothing."""
        inherited = make.write_tool(home / ".arctic", "greet")
        project = make.write_tool(workspace, "greet")
        assert paths.find_all("tool", "greet") == [project, inherited]

    def test_both_flow_suffixes_in_one_directory_shadow_each_other(
        self, paths: Paths, workspace: Path
    ) -> None:
        yaml_flow = make.write_text_flow(workspace, "twice", "flow: twice\n")
        yml_flow = make.write_text_flow(workspace, "twice", "flow: twice\n", suffix=".yml")
        assert paths.find_all("flow", "twice") == [yaml_flow, yml_flow]

    def test_a_directory_without_a_spec_is_not_a_component(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        """A stray folder must not shadow a real definition further down the list."""
        (workspace / "tools" / "greet").mkdir(parents=True)
        inherited = make.write_tool(home / ".arctic", "greet")
        assert paths.find("tool", "greet") == inherited

    def test_a_name_that_resolves_to_nothing_says_where_it_looked(
        self, paths: Paths, workspace: Path
    ) -> None:
        with pytest.raises(LookupError_, match=r"unknown tool 'absent'.*\./tools/absent"):
            paths.find("tool", "absent")

    def test_the_message_lists_every_candidate(self, paths: Paths) -> None:
        message = str(pytest.raises(LookupError_, paths.find, "flow", "absent").value)
        assert "./flows/absent.yaml" in message
        assert "./flows/absent.yml" in message

    def test_a_kind_that_is_not_a_component_kind_is_refused(self, paths: Paths) -> None:
        """Adapters are registered in code, so there is nothing on disk to look for."""
        with pytest.raises(LookupError_, match="'adapter' is not a component kind"):
            paths.find("adapter", "claude_code")

    def test_find_all_returns_nothing_rather_than_raising(self, paths: Paths) -> None:
        assert paths.find_all("tool", "absent") == []


class TestListing:
    def test_lists_every_name_of_a_kind_sorted(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "zebra")
        make.write_tool(workspace, "alpha")
        # Derived from what ships rather than written out here, so adding a built-in tool
        # does not fail a test about ordering.
        builtin = [tool.name for tool in (builtin_root() / "tools").iterdir()]
        assert list(paths.list("tool")) == sorted(["alpha", "zebra", *builtin])

    def test_a_name_maps_to_the_definition_that_wins(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        make.write_tool(home / ".arctic", "greet")
        project = make.write_tool(workspace, "greet")
        assert paths.list("tool")["greet"] == project

    def test_a_flow_is_listed_by_its_stem(self, paths: Paths, workspace: Path) -> None:
        make.write_text_flow(workspace, "review_file", "flow: review_file\n")
        assert list(paths.list("flow")) == ["review_file"]

    def test_a_root_without_that_subdirectory_is_skipped(self, paths: Paths) -> None:
        assert paths.list("agent") == {}

    def test_something_that_is_not_a_component_is_not_listed(
        self, paths: Paths, workspace: Path
    ) -> None:
        (workspace / "tools").mkdir()
        (workspace / "tools" / "notes.md").write_text("stray file")
        (workspace / "tools" / "empty").mkdir()
        listed = paths.list("tool")
        assert "notes.md" not in listed
        assert "empty" not in listed


class TestDisplay:
    def test_the_workspace_itself_is_a_dot(self, paths: Paths, workspace: Path) -> None:
        assert paths.display(workspace) == "."

    def test_a_path_inside_the_workspace_is_relative_to_it(
        self, paths: Paths, workspace: Path
    ) -> None:
        assert paths.display(workspace / "tools" / "greet") == "./tools/greet"

    def test_a_path_inside_home_wears_a_tilde(self, paths: Paths, home: Path) -> None:
        assert paths.display(home / ".arctic" / "tools") == "~/.arctic/tools"

    def test_anything_else_is_shown_in_full(self, paths: Paths) -> None:
        assert paths.display(Path("/usr/share/atf")) == "/usr/share/atf"
