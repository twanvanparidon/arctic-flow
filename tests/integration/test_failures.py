"""Every expected failure, as a person sees it.

`commands.EXPECTED_ERRORS` is a contract: a front end catches that set once, at its edge,
and everything in it is an ordinary failure rather than a bug. What that turns into is
fixed and worth pinning as a shape: **exit 1, and one `engine:` line on stderr, with
nothing at all on stdout.** Anything above that line is the progress display reporting a
step that had already started, which is why the shape is "the last line" and not "the only
line". Under `--quiet` there is nothing else, and one test says so.

Three of these are about *when* the failure lands. The engine refuses a flow rather than
running half of one, so a bad component, a bad reference and a bad input all have to be
reported before the first step, not after a step has spent time or money.

An unexpected exception deliberately keeps its traceback, so nothing here asserts that a
bug reads nicely.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from support import components as make

from .conftest import Runner


def flow_with(project: Path, **definition: object) -> None:
    make.write_flow(project, "broken", {"flow": "broken", **definition})


def failure(atf: Runner, project: Path, *argv: str) -> str:
    """Run something that must fail, and hand back the line the CLI closed with."""
    result = atf("--workspace", str(project), *argv)
    assert result.code == 1, f"expected a failure, got {result.code}"
    assert result.out == "", "a failure must not write to stdout"
    last = result.err.rstrip("\n").splitlines()[-1]
    assert last.startswith("engine: "), f"expected an engine: line, got {result.err!r}"
    return last


class TestBeforeAnythingRuns:
    def test_a_flow_that_does_not_exist(self, project: Path, atf: Runner) -> None:
        assert "unknown flow 'absent'" in failure(atf, project, "run", "absent")

    def test_a_flow_file_that_is_not_a_mapping(self, project: Path, atf: Runner) -> None:
        make.write_text_flow(project, "broken", "- a list, not a flow\n")
        assert "YAML mapping" in failure(atf, project, "run", "broken")

    def test_a_flow_missing_a_required_field(self, project: Path, atf: Runner) -> None:
        flow_with(project, start="a")
        assert "missing required field 'steps'" in failure(atf, project, "run", "broken")

    def test_an_input_the_flow_never_declared(self, project: Path, atf: Runner) -> None:
        flow_with(project, start="a", steps=[{"id": "a", "tool": "echo_input"}])
        assert "unknown input 'nope'" in failure(atf, project, "run", "broken", "--input", "nope=1")

    def test_a_required_input_left_out(self, project: Path, atf: Runner) -> None:
        flow_with(
            project,
            start="a",
            inputs={"path": {"required": True}},
            steps=[{"id": "a", "tool": "echo_input"}],
        )
        assert "missing required input 'path'" in failure(atf, project, "run", "broken")

    def test_an_input_that_is_not_a_key_value_pair(self, project: Path, atf: Runner) -> None:
        flow_with(project, start="a", steps=[{"id": "a", "tool": "echo_input"}])
        assert "KEY=VALUE" in failure(atf, project, "run", "broken", "--input", "just-a-word")

    def test_a_tool_the_lookup_cannot_place(self, project: Path, atf: Runner) -> None:
        flow_with(project, start="a", steps=[{"id": "a", "tool": "imaginary"}])
        assert "unknown tool 'imaginary'" in failure(atf, project, "run", "broken")

    def test_a_tool_whose_script_lost_its_executable_bit(self, project: Path, atf: Runner) -> None:
        """Caught before the run, because it is the usual way a tool fails on another machine."""
        make.make_unexecutable(project / "tools" / "shout" / "run.sh")
        flow_with(project, start="a", steps=[{"id": "a", "tool": "shout"}])
        assert "is not executable" in failure(atf, project, "run", "broken")

    def test_an_agent_naming_an_adapter_that_is_not_registered(
        self, project: Path, atf: Runner
    ) -> None:
        make.write_agent(project, "stray", adapter="imaginary")
        flow_with(project, start="a", steps=[{"id": "a", "agent": "stray", "prompt": "x"}])
        assert "unknown adapter 'imaginary'" in failure(atf, project, "run", "broken")

    def test_a_step_reading_from_one_that_is_not_upstream(self, project: Path, atf: Runner) -> None:
        flow_with(
            project,
            start="a",
            steps=[
                {"id": "a", "tool": "echo_input", "push": ["b", "c"]},
                {"id": "b", "tool": "echo_input"},
                {"id": "c", "tool": "shout", "input": {"text": "{{ steps.b.text }}"}},
            ],
        )
        assert "not upstream" in failure(atf, project, "run", "broken")

    def test_a_cycle(self, project: Path, atf: Runner) -> None:
        flow_with(
            project,
            start="a",
            steps=[
                {"id": "a", "tool": "echo_input", "push": ["b"]},
                {"id": "b", "tool": "echo_input", "push": ["a"]},
            ],
        )
        assert "cycle" in failure(atf, project, "run", "broken")

    def test_a_secret_templated_into_an_agent_prompt(self, project: Path, atf: Runner) -> None:
        flow_with(
            project,
            start="a",
            steps=[
                {
                    "id": "a",
                    "agent": "writer",
                    "secrets": ["token"],
                    "prompt": "sign with {{ secrets.token }}",
                }
            ],
        )
        assert "secrets.token" in failure(atf, project, "run", "broken")

    def test_quiet_leaves_the_failure_and_nothing_else(self, project: Path, atf: Runner) -> None:
        flow_with(project, start="a", steps=[{"id": "a", "tool": "imaginary"}])
        result = atf("--workspace", str(project), "run", "broken", "--quiet")
        assert result.code == 1
        assert result.err.count("\n") == 1

    def test_nothing_started_before_any_of_that_was_reported(
        self, project: Path, atf: Runner
    ) -> None:
        """A refusal arriving under a progress line reads as a step that failed."""
        flow_with(project, start="a", steps=[{"id": "a", "tool": "imaginary"}])
        result = atf("--workspace", str(project), "run", "broken")
        assert "→" not in result.err


class TestPartWayThrough:
    def test_a_tool_that_exits_non_zero(self, project: Path, atf: Runner) -> None:
        flow_with(
            project,
            start="a",
            steps=[{"id": "a", "tool": "fail_with", "input": {"code": 3, "message": "no such"}}],
        )
        message = failure(atf, project, "run", "broken")
        assert "step 'a'" in message
        # In the component's own words, from its spec, plus the line it printed.
        assert "not found" in message
        assert "no such" in message

    def test_the_step_that_failed_is_marked_as_it_happens(self, project: Path, atf: Runner) -> None:
        flow_with(
            project,
            start="a",
            steps=[{"id": "a", "tool": "fail_with", "input": {"code": 3, "message": "no such"}}],
        )
        assert "✗ a" in atf("--workspace", str(project), "run", "broken").err

    def test_a_step_after_the_failure_never_runs(self, project: Path, atf: Runner) -> None:
        flow_with(
            project,
            start="a",
            steps=[
                {
                    "id": "a",
                    "tool": "fail_with",
                    "input": {"code": 3, "message": "no"},
                    "push": ["b"],
                },
                {"id": "b", "tool": "shout", "input": {"text": "never"}},
            ],
        )
        assert "→ b" not in atf("--workspace", str(project), "run", "broken").err

    def test_an_agent_turn_the_runtime_refused(self, project: Path, atf: Runner) -> None:
        flow_with(
            project, start="a", steps=[{"id": "a", "agent": "writer", "prompt": "!fail busy"}]
        )
        assert "busy" in failure(atf, project, "run", "broken")


class TestTheVault:
    def test_a_vault_file_that_is_not_there(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        flow_with(
            project,
            vault="absent.vault",
            start="a",
            steps=[{"id": "a", "tool": "echo_input"}],
        )
        assert "cannot read vault" in failure(atf, project, "run", "broken")

    def test_a_password_file_with_nothing_in_it(
        self, project: Path, tmp_path: Path, atf: Runner
    ) -> None:
        empty = tmp_path / "empty.pw"
        empty.write_text("")
        flow_with(
            project, vault="absent.vault", start="a", steps=[{"id": "a", "tool": "echo_input"}]
        )
        assert "is empty" in failure(
            atf, project, "run", "broken", "--vault-password-file", str(empty)
        )

    def test_a_step_declaring_secrets_with_no_vault_open(self, project: Path, atf: Runner) -> None:
        flow_with(
            project,
            start="a",
            steps=[{"id": "a", "tool": "echo_input", "secrets": ["token"]}],
        )
        assert "no vault is open" in failure(atf, project, "run", "broken")

    def test_a_secret_the_vault_does_not_hold(
        self, project: Path, atf: Runner, atf_process: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        atf_process(
            "vault",
            "create",
            str(project / "secrets.vault"),
            stdin="other: x\n",
            env={"ATF_VAULT_PASSWORD": "demo"},
        )
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        flow_with(
            project,
            vault="secrets.vault",
            start="a",
            steps=[{"id": "a", "tool": "echo_input", "secrets": ["token"]}],
        )
        message = failure(atf, project, "run", "broken")
        assert "has no token" in message
        assert "it holds: other" in message


class TestLint:
    def test_it_reports_the_same_problems_the_run_would_have(
        self, project: Path, atf: Runner
    ) -> None:
        """Same checks either way, which is the only arrangement where a green lint means
        anything."""
        flow_with(project, start="a", steps=[{"id": "a", "tool": "imaginary"}])
        assert "unknown tool 'imaginary'" in failure(atf, project, "lint", "broken")

    def test_a_clean_flow_says_what_it_checked(self, project: Path, atf: Runner) -> None:
        flow_with(project, start="a", steps=[{"id": "a", "tool": "echo_input"}])
        result = atf("--workspace", str(project), "lint", "broken")
        assert result.code == 0
        assert "1 step" in result.out
