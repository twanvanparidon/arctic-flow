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

from paths.resolver import (
    ENGINE_SYMBOL,
    HOME_SYMBOL,
    LookupError_,
    Paths,
    builtin_root,
    engine_root,
    flat_name,
    reserved,
)
from support import components as make


class TestBuiltinRoot:
    def test_points_at_the_components_that_ship_with_the_engine(self) -> None:
        assert (builtin_root() / "tools" / "arctic" / "read_file" / "spec.json").is_file()

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


class TestSources:
    """Extra roots named by `~/.arctic/config.yaml`. See `paths/config.py`."""

    @staticmethod
    def configured(home: Path, *sources: Path) -> None:
        (home / ".arctic").mkdir(exist_ok=True)
        listed = "\n".join(f"  - {source}" for source in sources)
        (home / ".arctic" / "config.yaml").write_text(f"sources:\n{listed}\n")

    def test_a_source_sits_below_home_and_above_the_builtins(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        """A library you opted into may replace what shipped with the engine. It may not
        replace what you or the project defined, or a flow would stop saying what it runs."""
        shared = tmp_path / "shared"
        shared.mkdir()
        self.configured(home, shared)
        assert Paths(workspace, env={}, home=home).roots == [
            workspace,
            home / ".arctic",
            shared,
            builtin_root(),
        ]

    def test_several_sources_keep_their_listed_order(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        first, second = tmp_path / "one", tmp_path / "two"
        first.mkdir()
        second.mkdir()
        self.configured(home, first, second)
        roots = Paths(workspace, env={}, home=home).roots
        assert roots.index(first) < roots.index(second)

    def test_a_source_that_is_not_there_is_dropped(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        """Same rule every other root follows. A library on a drive that is not mounted
        costs a missing component, not a command that refuses to start."""
        self.configured(home, tmp_path / "absent")
        assert Paths(workspace, env={}, home=home).roots == [
            workspace,
            home / ".arctic",
            builtin_root(),
        ]

    def test_a_tool_resolves_out_of_a_source(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        shared = tmp_path / "shared"
        base = make.write_tool(shared, "greet")
        self.configured(home, shared)
        assert Paths(workspace, env={}, home=home).find("tool", "greet") == base

    def test_the_home_directory_wins_over_a_source_of_the_same_name(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        shared = tmp_path / "shared"
        make.write_tool(shared, "greet")
        mine = make.write_tool(home / ".arctic", "greet")
        self.configured(home, shared)
        assert Paths(workspace, env={}, home=home).find("tool", "greet") == mine

    def test_a_source_inside_the_project_is_not_displayed_as_the_project(
        self, workspace: Path, home: Path
    ) -> None:
        """`list` exists to say which layer won, and `./x` is a claim about the project.
        A source commonly sits under the workspace or under home, so shortening it against
        either would name the wrong layer."""
        shared = workspace / "vendor"
        base = make.write_tool(shared, "greet")
        self.configured(home, shared)
        assert Paths(workspace, env={}, home=home).display(base) == str(base)

    def test_a_source_under_home_is_not_displayed_as_home(
        self, workspace: Path, home: Path
    ) -> None:
        shared = home / "work" / "components"
        base = make.write_tool(shared, "greet")
        self.configured(home, shared)
        assert Paths(workspace, env={}, home=home).display(base) == str(base)

    def test_the_project_is_still_shortened_when_a_source_exists(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        self.configured(home, shared)
        base = make.write_tool(workspace, "greet")
        assert Paths(workspace, env={}, home=home).display(base) == "./tools/greet"


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


class TestTheEngineNamespace:
    """`arctic/` belongs to the engine, and nothing else may define a name inside it.

    A security property rather than a convenience, so these tests are about what is
    *refused*. Without it a flow reading `tool: arctic/read_file` says nothing about what
    runs: any higher root, including a repository somebody cloned, could put anything there
    under a name that reads as the contained, no-network tool that ships.
    """

    @pytest.mark.parametrize(
        ("name", "is_reserved"),
        [
            ("arctic/read_file", True),
            ("arctic/anything", True),
            # A near miss is reserved too. It reads as shipped, which is the whole risk.
            ("arctic/read_files", True),
            ("arctic/deep/nested", True),
            ("arctic", True),
            ("read_file", False),
            ("mine/read_file", False),
            # The segment has to *be* the namespace, not contain it.
            ("antarctic/read_file", False),
        ],
    )
    def test_what_counts_as_the_engines_own(self, name: str, is_reserved: bool) -> None:
        assert reserved(name) is is_reserved

    @pytest.mark.parametrize("root", ["project", "dot", "home"])
    def test_a_definition_of_a_shipped_name_is_refused(
        self, workspace: Path, home: Path, root: str
    ) -> None:
        """The threat itself: the flow says arctic/read_file and something else runs."""
        where = {"project": workspace, "dot": workspace / ".arctic", "home": home / ".arctic"}
        make.write_tool(where[root], "arctic/read_file", script=make.prints("not the real one"))
        paths = Paths(workspace, env={}, home=home)
        with pytest.raises(LookupError_, match="belongs to the engine"):
            paths.find("tool", "arctic/read_file")

    def test_even_atf_path_cannot_define_one(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        """The highest root there is, and it does not help. Otherwise the reservation
        would be advice rather than a rule."""
        override = tmp_path / "override"
        make.write_tool(override, "arctic/read_file")
        paths = Paths(workspace, env={"ATF_PATH": str(override)}, home=home)
        with pytest.raises(LookupError_, match="belongs to the engine"):
            paths.find("tool", "arctic/read_file")

    def test_a_source_cannot_define_one_either(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        """The case nobody would think to check: a shared library you pulled, whose
        contents you did not write and cannot see from the flow."""
        shared = tmp_path / "shared"
        make.write_tool(shared, "arctic/read_file")
        (home / ".arctic").mkdir(exist_ok=True)
        (home / ".arctic" / "config.yaml").write_text(f"sources:\n  - {shared}\n")
        with pytest.raises(LookupError_, match="belongs to the engine"):
            Paths(workspace, env={}, home=home).find("tool", "arctic/read_file")

    def test_a_name_the_engine_does_not_ship_is_reserved_all_the_same(
        self, workspace: Path, home: Path
    ) -> None:
        """Reserving the namespace rather than the five names means a new built-in can
        never collide with something somebody already had."""
        make.write_tool(workspace, "arctic/mine")
        with pytest.raises(LookupError_, match="belongs to the engine"):
            Paths(workspace, env={}, home=home).find("tool", "arctic/mine")

    def test_the_refusal_names_the_directory_to_rename(self, workspace: Path, home: Path) -> None:
        make.write_tool(home / ".arctic", "arctic/read_file")
        paths = Paths(workspace, env={}, home=home)
        with pytest.raises(LookupError_, match=r"\$HOME/\.arctic/tools/arctic/read_file"):
            paths.find("tool", "arctic/read_file")

    def test_it_refuses_rather_than_quietly_preferring_the_built_in(
        self, workspace: Path, home: Path
    ) -> None:
        """The built-in exists and would have won. Silence would leave someone editing a
        directory that does nothing, which is the whole reason this is loud."""
        make.write_tool(workspace, "arctic/read_file")
        with pytest.raises(LookupError_):
            Paths(workspace, env={}, home=home).find("tool", "arctic/read_file")

    @pytest.mark.parametrize("kind", ["tool", "agent", "flow"])
    def test_every_kind_is_covered(self, workspace: Path, home: Path, kind: str) -> None:
        """One rule for all three, so a built-in agent later needs no second decision."""
        writer = {
            "tool": lambda: make.write_tool(workspace, "arctic/thing"),
            "agent": lambda: make.write_agent(workspace, "arctic/thing"),
            "flow": lambda: make.write_text_flow(workspace, "arctic/thing", "flow: thing\n"),
        }
        writer[kind]()
        with pytest.raises(LookupError_, match="belongs to the engine"):
            Paths(workspace, env={}, home=home).find(kind, "arctic/thing")

    def test_a_shipped_tool_still_resolves_when_nobody_interferes(self, paths: Paths) -> None:
        assert paths.find("tool", "arctic/read_file").is_relative_to(builtin_root())

    def test_your_own_name_is_unaffected(self, paths: Paths, workspace: Path) -> None:
        """The migration for anyone who was overriding: rename it and say so in the flow."""
        base = make.write_tool(workspace, "mine/read_file")
        assert paths.find("tool", "mine/read_file") == base

    def test_find_all_leaves_an_intruder_out_rather_than_shadowing_with_it(
        self, workspace: Path, home: Path
    ) -> None:
        """`commands.inventory` calls this for every listed name, so it must not raise:
        a listing has to survive the thing it exists to report."""
        make.write_tool(workspace, "arctic/read_file")
        matches = Paths(workspace, env={}, home=home).find_all("tool", "arctic/read_file")
        assert [m for m in matches if not m.is_relative_to(builtin_root())] == []

    def test_the_intruder_is_reported_by_path(self, workspace: Path, home: Path) -> None:
        base = make.write_tool(workspace, "arctic/read_file")
        paths = Paths(workspace, env={}, home=home)
        assert paths.intruders("tool", "arctic/read_file") == [base]
        assert paths.all_intruders("tool") == {"arctic/read_file": [base]}

    def test_nothing_is_reported_for_an_ordinary_name(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "greet")
        assert paths.intruders("tool", "greet") == []
        assert paths.all_intruders("tool") == {}

    def test_a_contested_name_is_not_listed_as_available(self, workspace: Path, home: Path) -> None:
        """It resolves to nothing until the directory is renamed, so a listing saying it is
        available would be wrong about what the name does."""
        make.write_tool(workspace, "arctic/read_file")
        listed = Paths(workspace, env={}, home=home).list("tool")
        assert "arctic/read_file" not in listed
        # The others are untouched: one bad directory is not the whole namespace.
        assert "arctic/write_file" in listed

    def test_an_uncontested_shipped_name_is_still_listed(self, paths: Paths) -> None:
        assert "arctic/read_file" in paths.list("tool")

    def test_a_missing_reserved_name_is_only_looked_for_in_the_engines_root(
        self, workspace: Path, home: Path
    ) -> None:
        """Naming roots it would never have been taken from would send someone to put the
        component in one of them."""
        paths = Paths(workspace, env={}, home=home)
        message = str(pytest.raises(LookupError_, paths.find, "tool", "arctic/absent").value)
        assert "$ATF_ROOT" in message
        assert "./tools" not in message and HOME_SYMBOL not in message


class TestNamespaces:
    """A name may carry a namespace, so `group/read_file` is `tools/group/read_file`."""

    def test_finds_a_tool_inside_a_namespace(self, paths: Paths, workspace: Path) -> None:
        base = make.write_tool(workspace, "group/greet")
        assert paths.find("tool", "group/greet") == base

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
        with pytest.raises(LookupError_, match="unknown tool 'group/greet'"):
            paths.find("tool", "group/greet")

    def test_a_bare_name_does_not_reach_into_a_namespace(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "group/greet")
        with pytest.raises(LookupError_, match="unknown tool 'greet'"):
            paths.find("tool", "greet")

    def test_precedence_applies_to_the_whole_name(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        make.write_tool(home / ".arctic", "group/greet")
        project = make.write_tool(workspace, "group/greet")
        assert paths.find("tool", "group/greet") == project

    def test_a_namespace_directory_is_not_itself_a_component(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "group/greet")
        with pytest.raises(LookupError_, match="unknown tool 'arctic'"):
            paths.find("tool", "arctic")

    def test_the_message_names_the_namespaced_candidate(self, paths: Paths) -> None:
        message = str(pytest.raises(LookupError_, paths.find, "tool", "group/absent").value)
        assert "./tools/group/absent" in message


class TestFlowBundles:
    """`flows/review/review.yaml` is the flow `review`, so its prompts have a home.

    What makes the directory a bundle is that it holds a flow of its own name. Everything
    here is about the two ways that could collide with what already resolved: a namespace,
    and the flat spelling of the same name.
    """

    def test_a_directory_holding_a_flow_of_its_own_name_is_that_flow(
        self, paths: Paths, workspace: Path
    ) -> None:
        path = make.write_text_flow(workspace, "review", "flow: review\n", bundle=True)
        assert paths.find("flow", "review") == path

    def test_either_suffix_works_inside_a_bundle(self, paths: Paths, workspace: Path) -> None:
        path = make.write_text_flow(workspace, "review", "flow: review\n", ".yml", bundle=True)
        assert paths.find("flow", "review") == path

    def test_a_bundle_may_sit_inside_a_namespace(self, paths: Paths, workspace: Path) -> None:
        """The file carries the leaf, the way a namespaced spec.json does, so
        `release/sign` is `flows/release/sign/sign.yaml`."""
        path = make.write_text_flow(workspace, "release/sign", "flow: sign\n", bundle=True)
        assert paths.find("flow", "release/sign") == path

    def test_a_bundle_is_listed_once_under_its_own_name(
        self, paths: Paths, workspace: Path
    ) -> None:
        """Not also as `review/review`, which is a name nobody wrote."""
        path = make.write_text_flow(workspace, "review", "flow: review\n", bundle=True)
        assert paths.list("flow") == {"review": path}

    def test_a_bundle_is_still_a_namespace(self, paths: Paths, workspace: Path) -> None:
        """So nothing that resolved before the bundle layout existed stops resolving."""
        bundle = make.write_text_flow(workspace, "review", "flow: review\n", bundle=True)
        helper = bundle.parent / "helper.yaml"
        helper.write_text("flow: helper\n")
        assert paths.find("flow", "review/helper") == helper
        assert paths.list("flow") == {"review": bundle, "review/helper": helper}

    def test_a_directory_without_a_flow_of_its_own_name_is_only_a_namespace(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_text_flow(workspace, "release/sign", "flow: sign\n")
        with pytest.raises(LookupError_, match="unknown flow 'release'"):
            paths.find("flow", "release")

    def test_the_prompts_directory_is_not_a_namespace_of_flows(
        self, paths: Paths, workspace: Path
    ) -> None:
        bundle = make.write_text_flow(workspace, "review", "flow: review\n", bundle=True)
        make.write_prompt_file(bundle, "report", "Report.\n")
        assert paths.list("flow") == {"review": bundle}

    def test_the_flat_spelling_wins_when_a_name_is_written_both_ways(
        self, paths: Paths, workspace: Path
    ) -> None:
        """A half-made bundle must not take a name off the flow that already answered to it."""
        flat = make.write_text_flow(workspace, "review", "flow: review\n")
        make.write_text_flow(workspace, "review", "flow: review\n", bundle=True)
        assert paths.find("flow", "review") == flat

    def test_the_other_spelling_is_reported_as_shadowing(
        self, paths: Paths, workspace: Path
    ) -> None:
        """The same answer two suffixes in one directory get: both are listed, so an edit
        that appears to do nothing has somewhere to be seen."""
        flat = make.write_text_flow(workspace, "review", "flow: review\n")
        bundle = make.write_text_flow(workspace, "review", "flow: review\n", bundle=True)
        assert paths.find_all("flow", "review") == [flat, bundle]

    def test_the_listing_agrees_with_what_find_returns(self, paths: Paths, workspace: Path) -> None:
        """These are walked in opposite orders, so agreeing is a decision rather than a
        coincidence: `list` reads files before directories to match the candidate order."""
        flat = make.write_text_flow(workspace, "review", "flow: review\n")
        make.write_text_flow(workspace, "review", "flow: review\n", bundle=True)
        assert paths.list("flow")["review"] == flat == paths.find("flow", "review")

    def test_a_bundle_in_a_nearer_root_still_wins(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        make.write_text_flow(home / ".arctic", "review", "flow: review\n")
        project = make.write_text_flow(workspace, "review", "flow: review\n", bundle=True)
        assert paths.find("flow", "review") == project


class TestNameChecking:
    """`root / subdir / name` resolves whatever it is handed, so the name is checked first."""

    @pytest.mark.parametrize(
        "name",
        [
            "../../../etc/passwd",
            "group/../../../etc",
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
            ("arctic/read_file", "arctic__read_file"),
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
        base = make.write_tool(workspace, "group/greet")
        assert paths.list("tool")["group/greet"] == base

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
        assert "group/notes" not in listed

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
        make.write_tool(home / ".arctic", "group/greet")
        project = make.write_tool(workspace, "group/greet")
        assert paths.list("tool")["group/greet"] == project


class TestDisplay:
    def test_the_workspace_itself_is_a_dot(self, paths: Paths, workspace: Path) -> None:
        assert paths.display(workspace) == "."

    def test_a_path_inside_the_workspace_is_relative_to_it(
        self, paths: Paths, workspace: Path
    ) -> None:
        assert paths.display(workspace / "tools" / "greet") == "./tools/greet"

    def test_a_path_inside_home_is_named_for_the_variable(self, paths: Paths, home: Path) -> None:
        """`$HOME` is a real variable, so what is printed pastes into a shell and resolves."""
        assert paths.display(home / ".arctic" / "tools") == f"{HOME_SYMBOL}/.arctic/tools"

    def test_a_built_in_is_named_as_the_engines_own(self, paths: Paths) -> None:
        """`$ATF_ROOT` is not a variable anything reads. It stands for "this shipped with
        the engine", which in a release build is a path inside a PyInstaller bundle."""
        assert paths.display(builtin_root() / "tools") == f"{ENGINE_SYMBOL}/tools"

    def test_an_adapter_module_is_named_the_same_way(self, paths: Paths) -> None:
        """Adapters ship with the engine too, from a directory beside the built-ins, so
        the label covers both: `$ATF_ROOT/tools` and `$ATF_ROOT/adapters`."""
        module = engine_root() / "adapters" / "echo.py"
        assert paths.display(module) == f"{ENGINE_SYMBOL}/adapters/echo.py"

    def test_the_built_in_root_wins_over_the_directory_it_sits_in(
        self, workspace: Path, home: Path
    ) -> None:
        """From a checkout it is under the workspace, and from install.sh it is under home.
        Matched by either it would read as an ordinary file of yours, which it is not."""
        paths = Paths(builtin_root().parent.parent, env={}, home=home)
        assert paths.display(builtin_root() / "tools").startswith(ENGINE_SYMBOL)

    def test_anything_else_is_shown_in_full(self, paths: Paths) -> None:
        assert paths.display(Path("/usr/share/atf")) == "/usr/share/atf"
