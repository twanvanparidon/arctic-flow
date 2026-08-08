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

from paths.resolver import LookupError_, Paths, builtin_root, flat_name
from support import components as make


class TestBuiltinRoot:
    def test_points_at_the_components_that_ship_with_the_engine(self) -> None:
        assert (builtin_root() / "tools" / "common" / "read_file" / "spec.json").is_file()

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


class TestNamespaces:
    """A name may carry a namespace, so `common/read_file` is `tools/common/read_file`."""

    def test_finds_a_tool_inside_a_namespace(self, paths: Paths, workspace: Path) -> None:
        base = make.write_tool(workspace, "common/greet")
        assert paths.find("tool", "common/greet") == base

    def test_the_namespace_may_be_any_depth(self, paths: Paths, workspace: Path) -> None:
        base = make.write_tool(workspace, "git/worktree/add")
        assert paths.find("tool", "git/worktree/add") == base

    def test_an_agent_may_be_namespaced_too(self, paths: Paths, workspace: Path) -> None:
        base = make.write_agent(workspace, "review/summarizer")
        assert paths.find("agent", "review/summarizer") == base

    def test_a_flow_is_namespaced_by_the_directory_holding_it(
        self, paths: Paths, workspace: Path
    ) -> None:
        path = make.write_text_flow(workspace, "release/sign", "flow: sign\n")
        assert paths.find("flow", "release/sign") == path

    def test_a_namespaced_name_does_not_fall_back_to_the_bare_one(
        self, paths: Paths, workspace: Path
    ) -> None:
        """Two names, not one name with a search path. Overriding stays per whole name."""
        make.write_tool(workspace, "greet")
        with pytest.raises(LookupError_, match="unknown tool 'common/greet'"):
            paths.find("tool", "common/greet")

    def test_a_bare_name_does_not_reach_into_a_namespace(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "common/greet")
        with pytest.raises(LookupError_, match="unknown tool 'greet'"):
            paths.find("tool", "greet")

    def test_precedence_applies_to_the_whole_name(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        make.write_tool(home / ".arctic", "common/greet")
        project = make.write_tool(workspace, "common/greet")
        assert paths.find("tool", "common/greet") == project

    def test_a_namespace_directory_is_not_itself_a_component(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "common/greet")
        with pytest.raises(LookupError_, match="unknown tool 'common'"):
            paths.find("tool", "common")

    def test_the_message_names_the_namespaced_candidate(self, paths: Paths) -> None:
        message = str(pytest.raises(LookupError_, paths.find, "tool", "common/absent").value)
        assert "./tools/common/absent" in message


class TestNameChecking:
    """`root / subdir / name` resolves whatever it is handed, so the name is checked first."""

    @pytest.mark.parametrize(
        "name",
        [
            "../../../etc/passwd",
            "common/../../../etc",
            "/etc/passwd",
            "git//commit",
            "git/./commit",
            "git/commit/",
        ],
    )
    def test_a_name_that_would_leave_the_root_is_refused(self, paths: Paths, name: str) -> None:
        with pytest.raises(LookupError_, match="is not a component name"):
            paths.find("tool", name)

    def test_refused_before_the_filesystem_is_touched(self, paths: Paths, tmp_path: Path) -> None:
        """A real spec.json outside every root must not be reachable by naming its way there."""
        outside = tmp_path / "elsewhere"
        make.write_tool(outside, "greet")
        reach = os.path.relpath(outside / "tools" / "greet", paths.workspace / "tools")
        with pytest.raises(LookupError_, match="is not a component name"):
            paths.find("tool", reach)

    @pytest.mark.parametrize("name", ["", "   "])
    def test_an_empty_name_is_refused(self, paths: Paths, name: str) -> None:
        with pytest.raises(LookupError_, match="cannot be empty"):
            paths.find("tool", name)

    def test_find_all_refuses_it_too_rather_than_answering_nothing(self, paths: Paths) -> None:
        """It is the same lookup, so a bad name is a bad name and not an absence."""
        with pytest.raises(LookupError_, match="is not a component name"):
            paths.find_all("tool", "../escape")


class TestFlatName:
    """How a name is spelled where a slash cannot go: an MCP tool name, via `mcp__atf__`."""

    @pytest.mark.parametrize(
        ("name", "flat"),
        [
            ("read_file", "read_file"),
            ("common/read_file", "common__read_file"),
            ("git/worktree/add", "git__worktree__add"),
        ],
    )
    def test_the_separator_becomes_a_double_underscore(self, name: str, flat: str) -> None:
        assert flat_name(name) == flat

    def test_two_names_can_flatten_onto_one(self) -> None:
        """Which is why nothing reverses it by string surgery. `validate` refuses the pair."""
        assert flat_name("git/commit") == flat_name("git__commit")


class TestListing:
    def test_lists_every_name_of_a_kind_sorted(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "zebra")
        make.write_tool(workspace, "alpha")
        # Derived from what ships rather than written out here, so adding a built-in tool
        # does not fail a test about ordering. By spec.json rather than by directory, so a
        # built-in landing in a new namespace is picked up at whatever depth it sits.
        shipped = builtin_root() / "tools"
        builtin = [str(spec.parent.relative_to(shipped)) for spec in shipped.rglob("spec.json")]
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

    def test_a_file_that_is_not_a_flow_file_is_not_listed_as_a_flow(
        self, paths: Paths, workspace: Path
    ) -> None:
        """The suffix decides. `find` only ever builds one, and the walk has to agree."""
        (workspace / "flows").mkdir()
        (workspace / "flows" / "notes.md").write_text("stray file")
        assert paths.list("flow") == {}

    def test_a_namespaced_name_is_listed_qualified(self, paths: Paths, workspace: Path) -> None:
        """The name a flow would write, not the leaf in a directory the listing cannot show."""
        base = make.write_tool(workspace, "common/greet")
        assert paths.list("tool")["common/greet"] == base

    def test_a_namespace_of_any_depth_is_listed(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "git/worktree/add")
        assert "git/worktree/add" in paths.list("tool")

    def test_a_namespaced_flow_is_listed_by_its_directory_and_stem(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_text_flow(workspace, "release/sign", "flow: sign\n")
        assert list(paths.list("flow")) == ["release/sign"]

    def test_a_namespace_holding_no_component_contributes_nothing(
        self, paths: Paths, workspace: Path
    ) -> None:
        (workspace / "tools" / "common" / "notes").mkdir(parents=True)
        listed = paths.list("tool")
        assert "common" not in listed
        assert "common/notes" not in listed

    def test_a_dotted_directory_is_not_walked(self, paths: Paths, workspace: Path) -> None:
        """A .git or an editor's cache is not a namespace. Descending into one would list
        components nobody put there."""
        make.write_tool(workspace, ".cache/greet")
        assert ".cache/greet" not in paths.list("tool")

    def test_a_component_nested_inside_another_is_still_listed(
        self, paths: Paths, workspace: Path
    ) -> None:
        """`find` would resolve it, since it just joins the name onto a root. A listing that
        hid it would be a listing that does not show everything the engine can be asked for."""
        make.write_tool(workspace, "greet")
        nested = make.write_tool(workspace, "greet/inner")
        assert paths.list("tool")["greet/inner"] == nested
        assert paths.find("tool", "greet/inner") == nested

    def test_precedence_is_per_namespaced_name(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        make.write_tool(home / ".arctic", "common/greet")
        project = make.write_tool(workspace, "common/greet")
        assert paths.list("tool")["common/greet"] == project


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
