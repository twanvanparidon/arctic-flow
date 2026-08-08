"""Scaffolding a project the way someone starting one does it.

The claim worth an integration test is not that files appear. It is that what `create`
writes, the other commands accept: a scaffolded flow lints and runs, a scaffolded tool is
one a flow can name and the engine can spawn, and a scaffolded agent is one a flow can run
a turn against. Each of those crosses `create`, the lookup, `lint` and the executor, and
each is a place the four could stop agreeing.

The run reaches the fake `claude` for the agent half. What is being asked is whether the
scaffold produced something runnable, not what a model would say to it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from support.outcome import Runner

from .conftest import requires


@pytest.fixture
def empty(tmp_path: Path) -> Path:
    """A project with nothing in it, which is where someone reaching for `create` is.

    Its own directory rather than `tmp_path`, which the suite's other fixtures also write
    into: `bin/` is the fake `claude` and `home/` is `$HOME`.
    """
    root = tmp_path / "new-project"
    root.mkdir()
    return root


def create(atf: Runner, project: Path, kind: str, name: str) -> None:
    result = atf("--workspace", str(project), "create", kind, name)
    assert result.code == 0, result.err


class TestAScaffoldedFlow:
    def test_it_lints_the_moment_it_is_written(self, atf: Runner, empty: Path) -> None:
        create(atf, empty, "flow", "review")
        result = atf("--workspace", str(empty), "lint", "review")
        assert result.code == 0
        assert "no issues found" in result.out

    def test_it_runs_with_no_model_and_no_key(self, atf: Runner, empty: Path) -> None:
        """It names a built-in tool, so a new project produces output before anything is
        installed and before an agent exists to name."""
        requires("bash", "jq", "awk", "realpath")
        create(atf, empty, "flow", "review")
        (empty / "notes.md").write_text("the file it reads\n")

        result = atf("--workspace", str(empty), "run", "review", "--input", "path=notes.md")
        assert result.code == 0
        assert result.out == "the file it reads\n"

    def test_its_graph_can_be_printed(self, atf: Runner, empty: Path) -> None:
        create(atf, empty, "flow", "review")
        result = atf("--workspace", str(empty), "inspect", "flow", "review")
        assert result.code == 0
        assert "read_target" in result.out


class TestAScaffoldedTool:
    def test_a_flow_can_name_it_and_the_engine_can_run_it(self, atf: Runner, empty: Path) -> None:
        """The scaffolded run.sh is spawned as a real process, which is what catches a
        script that arrived without its executable bit."""
        requires("bash", "jq")
        create(atf, empty, "tool", "shout")
        _write_flow(
            empty,
            {
                "flow": "uses_it",
                "start": "call",
                "steps": [{"id": "call", "tool": "shout", "input": {"text": "spoken"}}],
                "output": {"template": "{{ steps.call.text }}"},
            },
        )
        result = atf("--workspace", str(empty), "run", "uses_it")
        assert result.code == 0
        # The one newline is the printer's, added so a redirect produces a well-formed
        # file. The scaffold itself returns a single value with nothing on the end of it.
        assert result.out == "spoken\n"

    def test_it_is_inspectable_as_its_own_contract(self, atf: Runner, empty: Path) -> None:
        create(atf, empty, "tool", "git/commit")
        result = atf("--workspace", str(empty), "inspect", "tool", "git/commit")
        assert result.code == 0
        # The name it was looked up by, and the leaf its spec carries, both appear.
        assert result.out.startswith("git/commit")
        assert "./tools/git/commit" in result.out


class TestAScaffoldedAgent:
    def test_a_flow_can_run_a_turn_against_it(self, atf: Runner, empty: Path) -> None:
        create(atf, empty, "agent", "reviewer")
        _write_flow(
            empty,
            {
                "flow": "asks_it",
                "start": "ask",
                "steps": [{"id": "ask", "agent": "reviewer", "prompt": "say something"}],
                "output": {"template": "{{ steps.ask.text }}"},
            },
        )
        result = atf("--workspace", str(empty), "run", "asks_it")
        assert result.code == 0
        assert "say something" in result.out

    def test_its_prompt_is_what_inspect_shows(self, atf: Runner, empty: Path) -> None:
        """agent.md is the system prompt, read verbatim, so the scaffold has to be one."""
        create(atf, empty, "agent", "reviewer")
        result = atf("--workspace", str(empty), "inspect", "agent", "reviewer")
        assert result.code == 0
        assert (empty / "agents" / "reviewer" / "agent.md").read_text().strip() in result.out


class TestWhereItLands:
    def test_a_project_keeping_a_dot_directory_is_written_into_it(
        self, atf: Runner, empty: Path
    ) -> None:
        (empty / ".arctic").mkdir()
        create(atf, empty, "flow", "review")
        assert (empty / ".arctic" / "flows" / "review.yaml").is_file()
        # And the lookup reads it back, which is the reason for choosing that root.
        assert atf("--workspace", str(empty), "lint", "review").code == 0

    def test_it_is_listed_beside_everything_else_that_resolves(
        self, atf: Runner, empty: Path
    ) -> None:
        create(atf, empty, "agent", "reviewer")
        result = atf("--workspace", str(empty), "list")
        assert "reviewer" in result.out
        assert "./agents/reviewer" in result.out


class TestRefusals:
    def test_it_will_not_overwrite(self, atf: Runner, empty: Path) -> None:
        create(atf, empty, "agent", "reviewer")
        result = atf("--workspace", str(empty), "create", "agent", "reviewer")
        assert result.code == 1
        assert "already exists" in result.err

    @pytest.mark.parametrize(
        ("kind", "name"), [("flow", "review.yaml"), ("tool", "../escape"), ("agent", "")]
    )
    def test_a_name_that_is_not_one_is_reported_rather_than_written(
        self, kind: str, name: str, atf: Runner, empty: Path
    ) -> None:
        result = atf("--workspace", str(empty), "create", kind, name)
        assert result.code == 1
        assert list(empty.iterdir()) == []

    def test_a_kind_that_is_not_a_component_is_refused_by_the_parser(
        self, atf: Runner, empty: Path
    ) -> None:
        """Adapters are registered in code, so there is no directory to write one into."""
        result = atf("--workspace", str(empty), "create", "adapter", "claude_code")
        assert result.code == 2


def _write_flow(project: Path, definition: dict) -> None:
    flows = project / "flows"
    flows.mkdir(parents=True, exist_ok=True)
    (flows / f"{definition['flow']}.yaml").write_text(yaml.safe_dump(definition))
