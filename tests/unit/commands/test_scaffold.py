"""Scaffolding a component into a project.

Two things are worth testing here and one of them is not obvious. The first is where a
component lands, since that decides whether the lookup then finds it. The second is that
what lands is something the engine will actually run: the scaffolds are data files, so
nothing but a test notices when a schema in `engine/specs.py` gains a requirement the
templates were never told about.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import commands
from commands.scaffold import PLACEHOLDER
from engine.specs import check_agent_spec, check_tool_spec
from paths.resolver import LookupError_, Paths

KINDS = ("flow", "agent", "tool")


def spec(created: commands.ComponentCreated) -> dict:
    return json.loads((created.path / "spec.json").read_text())


class TestWhereItLands:
    def test_a_project_with_no_dot_directory_gets_it_at_the_top(
        self, paths: Paths, workspace: Path
    ) -> None:
        created = commands.create("agent", "reviewer", paths)
        assert created.path == workspace / "agents" / "reviewer"
        assert created.display == "./agents/reviewer"

    def test_a_project_that_keeps_a_dot_directory_gets_it_in_there(
        self, paths: Paths, workspace: Path
    ) -> None:
        """`./.arctic` is the higher of the two project roots, so a project that has one
        would otherwise be handed a component its own lookup shadows."""
        (workspace / ".arctic").mkdir()
        created = commands.create("agent", "reviewer", paths)
        assert created.path == workspace / ".arctic" / "agents" / "reviewer"

    def test_a_flow_is_one_yaml_file_named_after_it(self, paths: Paths, workspace: Path) -> None:
        created = commands.create("flow", "review", paths)
        assert created.path == workspace / "flows" / "review.yaml"
        assert created.files == ()

    def test_a_namespace_is_the_directory_it_sits_in(self, paths: Paths, workspace: Path) -> None:
        created = commands.create("tool", "git/commit", paths)
        assert created.path == workspace / "tools" / "git" / "commit"

    @pytest.mark.parametrize("kind", KINDS)
    def test_what_was_created_is_what_the_lookup_finds(self, kind: str, paths: Paths) -> None:
        """The whole point of choosing a root rather than a path: `run` has to resolve it."""
        created = commands.create(kind, "thing", paths)
        assert paths.find(kind, "thing") == created.path


class TestWhatItWrites:
    def test_a_tool_is_a_spec_a_doc_and_a_script(self, paths: Paths) -> None:
        created = commands.create("tool", "greet", paths)
        assert created.files == ("run.sh", "spec.json", "tool.md")

    def test_an_agent_is_a_spec_and_the_prompt_beside_it(self, paths: Paths) -> None:
        created = commands.create("agent", "reviewer", paths)
        assert created.files == ("agent.md", "spec.json")

    def test_a_tools_script_is_executable(self, paths: Paths) -> None:
        """`check_tool_spec` refuses a tool whose command lost its executable bit, and a
        scaffold that arrived through a wheel or a frozen bundle may not have kept one."""
        created = commands.create("tool", "greet", paths)
        assert os.access(created.path / "run.sh", os.X_OK)

    @pytest.mark.parametrize("kind", KINDS)
    def test_nothing_arrives_still_holding_the_placeholder(self, kind: str, paths: Paths) -> None:
        created = commands.create(kind, "thing", paths)
        written = [created.path] if kind == "flow" else sorted(created.path.iterdir())
        assert written
        for file in written:
            assert PLACEHOLDER not in file.read_text()

    @pytest.mark.parametrize("kind", ["agent", "tool"])
    def test_a_spec_is_named_for_what_was_asked_for(self, kind: str, paths: Paths) -> None:
        assert spec(commands.create(kind, "thing", paths))["name"] == "thing"

    def test_a_namespaced_spec_carries_only_the_leaf(self, paths: Paths) -> None:
        """The namespace is which directory the component sits in, which a spec.json has
        no way of knowing. `git/commit` in there would be a second, disagreeing name."""
        assert spec(commands.create("tool", "git/commit", paths))["name"] == "commit"

    def test_a_flow_declares_the_whole_name(self, paths: Paths) -> None:
        """A flow's name is what `atf run` is handed, and that includes the namespace."""
        created = commands.create("flow", "release/sign", paths)
        assert "flow: release/sign" in created.path.read_text()


class TestWhatItWritesRuns:
    """The drift guard. A scaffold is data, so a schema that gains a requirement breaks
    nothing until someone creates a component and is told it cannot be run."""

    def test_a_scaffolded_tool_is_a_runnable_tool_spec(self, paths: Paths) -> None:
        created = commands.create("tool", "greet", paths)
        check_tool_spec(spec(created), created.path, created.display)

    def test_a_scaffolded_agent_is_a_runnable_agent_spec(self, paths: Paths) -> None:
        created = commands.create("agent", "reviewer", paths)
        check_agent_spec(spec(created), created.display)

    def test_a_scaffolded_flow_validates(self, paths: Paths) -> None:
        """It names a built-in tool rather than an agent, so this passes with no runtime
        installed, which is also what makes a new flow runnable the moment it is written."""
        commands.create("flow", "review", paths)
        assert commands.lint("review", paths).flow == "review"

    def test_a_scaffolded_agent_has_a_system_prompt(self, paths: Paths) -> None:
        """`load_agent` refuses an empty one, and agent.md is the whole of what an agent is."""
        commands.create("agent", "reviewer", paths)
        assert commands.agent_detail("reviewer", paths).prompt.strip() != ""


class TestRefusals:
    def test_it_refuses_to_overwrite_and_leaves_what_was_there(self, paths: Paths) -> None:
        created = commands.create("agent", "reviewer", paths)
        (created.path / "agent.md").write_text("mine")
        with pytest.raises(FileExistsError, match="agents/reviewer"):
            commands.create("agent", "reviewer", paths)
        assert (created.path / "agent.md").read_text() == "mine"

    def test_a_flow_named_as_a_path_is_refused(self, paths: Paths) -> None:
        """`create flow review.yaml` would otherwise write flows/review.yaml.yaml."""
        with pytest.raises(LookupError_, match="create flow review"):
            commands.create("flow", "review.yaml", paths)

    def test_a_workspace_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        """Every other command answers a mistyped --workspace by finding nothing. This one
        would answer it by creating a project tree there."""
        absent = Paths(tmp_path / "typo", env={}, home=tmp_path)
        with pytest.raises(NotADirectoryError, match="typo"):
            commands.create("flow", "review", absent)
        assert not (tmp_path / "typo").exists()

    def test_a_name_that_would_leave_the_root_is_refused(self, paths: Paths) -> None:
        with pytest.raises(LookupError_, match="not a component name"):
            commands.create("tool", "../escape", paths)

    def test_an_unknown_kind_is_refused_the_way_a_lookup_refuses_one(self, paths: Paths) -> None:
        with pytest.raises(LookupError_, match="not a component kind"):
            commands.create("adapter", "claude_code", paths)

    @pytest.mark.parametrize("kind", KINDS)
    def test_the_engines_own_namespace_is_refused(self, kind: str, paths: Paths) -> None:
        """Refused here as well as in the resolver, so the answer comes before the directory
        exists rather than the first time something tries to run it."""
        with pytest.raises(LookupError_, match="belongs to the engine"):
            commands.create(kind, "common/mine", paths)

    def test_refusing_it_suggests_a_namespace_of_your_own(self, paths: Paths) -> None:
        with pytest.raises(LookupError_, match="tool <yours>/read_file"):
            commands.create("tool", "common/read_file", paths)

    def test_a_name_beside_the_reserved_one_is_fine(self, paths: Paths, workspace: Path) -> None:
        """The migration for anyone who was overriding a built-in: rename and say so."""
        created = commands.create("tool", "mine/read_file", paths)
        assert created.path == workspace / "tools" / "mine" / "read_file"

    @pytest.mark.parametrize("name", ["../escape", "", "common/mine"])
    def test_a_refused_name_writes_nothing_at_all(
        self, name: str, paths: Paths, workspace: Path
    ) -> None:
        with pytest.raises(LookupError_):
            commands.create("tool", name, paths)
        assert list(workspace.iterdir()) == []
