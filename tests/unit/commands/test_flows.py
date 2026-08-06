"""The command layer for flows: resolve, prepare, run, and the three read-only views.

Two rules are load-bearing here and both get their own test. Nothing in `commands/` prints
or prompts, so `atf run > file` produces the flow's output alone. And `prepare` checks the
inputs before it touches the vault, so a mistyped input is answered with the mistake rather
than with a password prompt.

The password arrives as a callable for exactly that reason, which makes the ordering
testable: a provider that raises if it is called says whether anything asked too early.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import commands
from engine.executor import FlowError
from paths.resolver import LookupError_, Paths
from support import components as make
from vault.vault import Vault, VaultError

PASSWORD = "demo"


def never_asked() -> str:
    raise AssertionError("the password was resolved before it was needed")


@pytest.fixture
def project(workspace: Path) -> Path:
    make.write_tool(workspace, "emit", script=make.prints("the answer"))
    make.write_flow(
        workspace,
        "demo",
        {
            "flow": "demo_flow",
            "start": "a",
            "inputs": {"who": {"required": False}},
            "steps": [{"id": "a", "tool": "emit"}],
            "output": {"template": "{{ steps.a.text }}"},
        },
    )
    return workspace


class TestResolveFlow:
    def test_a_name_goes_through_the_lookup(self, project: Path, paths: Paths) -> None:
        assert commands.resolve_flow("demo", paths) == project / "flows" / "demo.yaml"

    def test_a_path_is_read_as_a_path(self, project: Path, paths: Paths) -> None:
        """So an ad-hoc flow outside the search roots stays usable."""
        elsewhere = project.parent / "scratch.yaml"
        elsewhere.write_text("flow: scratch\n")
        assert commands.resolve_flow(str(elsewhere), paths) == elsewhere

    def test_a_name_is_tried_before_the_filesystem(self, project: Path, paths: Paths) -> None:
        assert commands.resolve_flow("demo", paths).parent.name == "flows"

    def test_a_yaml_path_that_is_not_there_says_so(self, paths: Paths, tmp_path: Path) -> None:
        with pytest.raises(FlowError, match="no such flow file"):
            commands.resolve_flow(str(tmp_path / "absent.yaml"), paths)

    def test_a_directory_is_not_a_flow_file(self, paths: Paths, tmp_path: Path) -> None:
        directory = tmp_path / "looks_like.yaml"
        directory.mkdir()
        with pytest.raises(FlowError, match="no such flow file"):
            commands.resolve_flow(str(directory), paths)

    def test_a_name_that_resolves_to_nothing_is_a_lookup_error(self, paths: Paths) -> None:
        with pytest.raises(LookupError_, match="unknown flow 'absent'"):
            commands.resolve_flow("absent", paths)


class TestPrepare:
    def test_returns_a_plan_carrying_everything_run_needs(
        self, project: Path, paths: Paths
    ) -> None:
        plan = commands.prepare("demo", paths, {"who": "you"})
        assert plan.name == "demo_flow"
        assert plan.display == "./flows/demo.yaml"
        assert plan.inputs == {"who": "you"}
        assert plan.paths is paths

    def test_an_unknown_input_is_refused(self, project: Path, paths: Paths) -> None:
        with pytest.raises(FlowError, match="unknown input 'whom'"):
            commands.prepare("demo", paths, {"whom": "you"})

    def test_a_flow_declaring_no_vault_never_asks_for_a_password(
        self, project: Path, paths: Paths
    ) -> None:
        assert commands.prepare("demo", paths, {}, password=never_asked).vault is None

    def test_the_inputs_are_checked_before_the_vault_is_opened(
        self, project: Path, paths: Paths
    ) -> None:
        """A mistyped input should be answered with the mistake, not with a prompt."""
        make.write_flow(
            project,
            "sealed",
            {
                "flow": "sealed",
                "vault": "secrets.vault",
                "start": "a",
                "steps": [{"id": "a", "tool": "emit"}],
            },
        )
        with pytest.raises(FlowError, match="unknown input 'nope'"):
            commands.prepare("sealed", paths, {"nope": "1"}, password=never_asked)

    def test_the_flows_own_vault_is_opened_relative_to_the_workspace(
        self, project: Path, paths: Paths
    ) -> None:
        Vault(path=project / "secrets.vault", values={"token": "abc"}).save(PASSWORD)
        make.write_flow(
            project,
            "sealed",
            {
                "flow": "sealed",
                "vault": "secrets.vault",
                "start": "a",
                "steps": [{"id": "a", "tool": "emit"}],
            },
        )
        plan = commands.prepare("sealed", paths, {}, password=PASSWORD)
        assert plan.vault is not None
        assert plan.vault.values == {"token": "abc"}

    def test_a_vault_named_by_the_caller_beats_the_one_in_the_flow(
        self, project: Path, paths: Paths
    ) -> None:
        Vault(path=project / "flow.vault", values={"from": "the flow"}).save(PASSWORD)
        Vault(path=project / "caller.vault", values={"from": "the caller"}).save(PASSWORD)
        make.write_flow(
            project,
            "sealed",
            {
                "flow": "sealed",
                "vault": "flow.vault",
                "start": "a",
                "steps": [{"id": "a", "tool": "emit"}],
            },
        )
        plan = commands.prepare("sealed", paths, {}, vault_ref="caller.vault", password=PASSWORD)
        assert plan.vault is not None
        assert plan.vault.values == {"from": "the caller"}

    def test_a_vault_that_will_not_open_fails_here_rather_than_mid_run(
        self, project: Path, paths: Paths
    ) -> None:
        make.write_flow(
            project,
            "sealed",
            {
                "flow": "sealed",
                "vault": "absent.vault",
                "start": "a",
                "steps": [{"id": "a", "tool": "emit"}],
            },
        )
        with pytest.raises(VaultError, match="cannot read vault"):
            commands.prepare("sealed", paths, {}, password=PASSWORD)


class TestRun:
    def test_returns_the_flows_output_rather_than_printing_it(
        self, project: Path, paths: Paths, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Where the output goes is the caller's business, and stdout is load-bearing."""
        result = commands.run(commands.prepare("demo", paths, {}))
        assert result.output == "the answer"
        assert capsys.readouterr() == ("", "")

    def test_the_result_names_the_flow_and_where_it_came_from(
        self, project: Path, paths: Paths
    ) -> None:
        result = commands.run(commands.prepare("demo", paths, {}))
        assert result.flow == "demo_flow"
        assert result.display == "./flows/demo.yaml"

    def test_the_trace_has_one_entry_per_step(self, project: Path, paths: Paths) -> None:
        result = commands.run(commands.prepare("demo", paths, {}))
        assert [entry["step"] for entry in result.trace] == ["a"]

    def test_the_observer_is_passed_straight_to_the_engine(
        self, project: Path, paths: Paths
    ) -> None:
        events: list[dict[str, Any]] = []
        commands.run(commands.prepare("demo", paths, {}), on_event=events.append)
        assert [event["kind"] for event in events] == ["started", "finished"]


class TestLint:
    def test_a_flow_that_validates_comes_back_with_its_steps(
        self, project: Path, paths: Paths
    ) -> None:
        """A returned result means "no issues"; the failure mode is an exception."""
        result = commands.lint("demo", paths)
        assert result.flow == "demo_flow"
        assert len(result.steps) == 1

    def test_a_broken_flow_raises(self, project: Path, paths: Paths) -> None:
        make.write_flow(project, "bad", {"flow": "bad", "start": "a", "steps": []})
        with pytest.raises(FlowError, match="non-empty list"):
            commands.lint("bad", paths)

    def test_a_flow_with_no_name_fails_as_a_flow_error(self, project: Path, paths: Paths) -> None:
        """Reading `definition["flow"]` first would report this as a KeyError traceback."""
        make.write_flow(project, "nameless", {"start": "a", "steps": [{"id": "a", "tool": "emit"}]})
        with pytest.raises(FlowError, match="missing required field 'flow'"):
            commands.lint("nameless", paths)


class TestGraph:
    def test_renders_the_push_edges(self, project: Path, paths: Paths) -> None:
        result = commands.graph("demo", paths)
        assert result.text.startswith("demo_flow: start -> a")

    def test_it_validates_first_so_the_edges_are_real(self, project: Path, paths: Paths) -> None:
        make.write_flow(
            project, "bad", {"flow": "bad", "start": "a", "steps": [{"id": "a", "tool": "ghost"}]}
        )
        with pytest.raises(FlowError, match="unknown tool 'ghost'"):
            commands.graph("bad", paths)


class TestDiagram:
    def test_returns_the_markdown(self, project: Path, paths: Paths) -> None:
        assert commands.diagram("demo", paths).markdown.startswith("# demo_flow")

    def test_writing_is_the_same_operation_from_any_front_end(
        self, project: Path, paths: Paths, tmp_path: Path
    ) -> None:
        out = tmp_path / "demo.md"
        result = commands.diagram("demo", paths, out)
        assert out.read_text() == result.markdown
        assert result.written_to == out

    def test_nothing_is_written_unless_a_destination_is_given(
        self, project: Path, paths: Paths
    ) -> None:
        assert commands.diagram("demo", paths).written_to is None
