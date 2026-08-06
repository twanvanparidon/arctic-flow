"""A flow from YAML on disk to text on stdout, through the CLI.

Every layer takes part: argv, the lookup, validation, the thread pool, real tool processes,
the adapter and its subprocess, the output template. The shapes here are the ones the
shipped examples do not have, so this file and `test_examples.py` divide the graph between
them rather than testing it twice.

The agent steps reach the fake `claude`, which answers with the prompt it was given. That
makes the model's part of a flow deterministic without removing it: the process, the pipe
and the envelope are all still real.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from support import components as make

from .conftest import Runner


def flow(project: Path, name: str, **definition: object) -> None:
    make.write_flow(project, name, definition)


class TestALinearFlow:
    def test_a_tool_hands_its_result_to_the_next_step(self, project: Path, atf: Runner) -> None:
        (project / "note.txt").write_text("quiet words\n")
        flow(
            project,
            "pipeline",
            flow="pipeline",
            start="read",
            inputs={"path": {"required": True}},
            steps=[
                {
                    "id": "read",
                    "tool": "read_file",
                    "input": {"path": "{{ inputs.path }}"},
                    "push": ["loud"],
                },
                {"id": "loud", "tool": "shout", "input": {"text": "{{ steps.read.text }}"}},
            ],
            output={"template": "{{ steps.loud.text }}"},
        )
        result = atf("--workspace", str(project), "run", "pipeline", "--input", "path=note.txt")
        assert result.code == 0
        assert result.out == "QUIET WORDS\n"

    def test_the_flows_output_is_the_only_thing_on_stdout(self, project: Path, atf: Runner) -> None:
        """Progress, the frame and the trace are stderr, so `run … > file` is the result."""
        flow(
            project,
            "one",
            flow="one",
            start="a",
            steps=[{"id": "a", "tool": "shout", "input": {"text": "hello"}}],
            output={"template": "{{ steps.a.text }}"},
        )
        result = atf("--workspace", str(project), "run", "one")
        assert result.out == "HELLO\n"
        assert "→ a" in result.err

    def test_a_flow_with_no_output_template_returns_every_result(
        self, project: Path, atf: Runner
    ) -> None:
        flow(
            project,
            "bare",
            flow="bare",
            start="a",
            steps=[{"id": "a", "tool": "shout", "input": {"text": "hi"}}],
        )
        result = atf("--workspace", str(project), "run", "bare")
        assert '"text": "HI"' in result.out


class TestInputsFromTheEnvironment:
    """`ATF_VAR_NAME` supplies the input `name`, through every layer that reads it."""

    @pytest.fixture(autouse=True)
    def flow_reading_an_input(self, project: Path) -> None:
        flow(
            project,
            "greet",
            flow="greet",
            start="a",
            inputs={"whom": {"required": True}},
            steps=[{"id": "a", "tool": "shout", "input": {"text": "{{ inputs.whom }}"}}],
            output={"template": "{{ steps.a.text }}"},
        )

    def test_a_variable_supplies_the_input(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ATF_VAR_WHOM", "the environment")
        result = atf("--workspace", str(project), "run", "greet")
        assert result.code == 0
        assert result.out == "THE ENVIRONMENT\n"

    def test_an_input_flag_beats_a_variable(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ATF_VAR_WHOM", "the environment")
        result = atf("--workspace", str(project), "run", "greet", "--input", "whom=the flag")
        assert result.out == "THE FLAG\n"

    def test_the_search_root_variable_is_not_read_as_an_input(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """$ATF_PATH stays a search root. The prefix exists so the two cannot collide."""
        monkeypatch.setenv("ATF_PATH", str(project))
        monkeypatch.setenv("ATF_VAR_WHOM", "still the input")
        result = atf("--workspace", str(project), "run", "greet")
        assert result.out == "STILL THE INPUT\n"

    def test_a_variable_for_an_undeclared_input_is_ignored(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One left exported in a shell must not refuse every flow run from it."""
        monkeypatch.setenv("ATF_VAR_SOMETHING_ELSE", "x")
        monkeypatch.setenv("ATF_VAR_WHOM", "you")
        result = atf("--workspace", str(project), "run", "greet")
        assert result.code == 0
        assert result.out == "YOU\n"

    def test_a_run_in_a_real_process_reads_its_own_environment(
        self, project: Path, atf_process: Runner
    ) -> None:
        """Through the real entry point, which is how a shell exporting one reaches it."""
        result = atf_process(
            "--workspace", str(project), "run", "greet", env={"ATF_VAR_WHOM": "a subprocess"}
        )
        assert result.code == 0
        assert result.out == "A SUBPROCESS\n"


class TestBranching:
    @pytest.fixture(autouse=True)
    def branchy(self, project: Path) -> None:
        """triage decides; one branch runs; the join reports on both."""
        flow(
            project,
            "branchy",
            flow="branchy",
            start="triage",
            steps=[
                {
                    "id": "triage",
                    "agent": "classifier",
                    "prompt": "classify it",
                    "switch": "{{ this.json.verdict }}",
                    "cases": {"risky": ["scan"], "clean": ["report"]},
                },
                {"id": "scan", "tool": "shout", "input": {"text": "scanned"}, "push": ["report"]},
                {
                    "id": "report",
                    "tool": "echo_input",
                    "input": {
                        "verdict": "{{ steps.triage.json.verdict }}",
                        "scan": "{{ steps.scan.text }}",
                    },
                },
            ],
            output={"template": "{{ steps.report.text }}"},
        )

    def test_the_branch_the_model_chose_is_the_one_that_runs(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_PREFER", "risky")
        result = atf("--workspace", str(project), "run", "branchy")
        assert '"scan": "SCANNED"' in result.out
        assert "⤼" not in result.err

    def test_the_other_branch_is_skipped_and_the_join_still_runs(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without skip propagation the join waits forever on a branch never entered."""
        monkeypatch.setenv("FAKE_CLAUDE_PREFER", "clean")
        result = atf("--workspace", str(project), "run", "branchy")
        assert result.code == 0
        assert '"scan": "(not run)"' in result.out
        assert "⤼ scan" in result.err

    def test_a_value_matching_no_case_fails_the_run(self, project: Path, atf: Runner) -> None:
        flow(
            project,
            "sideways",
            flow="sideways",
            start="pick",
            steps=[
                {
                    "id": "pick",
                    "tool": "shout",
                    "input": {"text": "elsewhere"},
                    "switch": "{{ this.text }}",
                    "cases": {"HERE": ["end"]},
                },
                {"id": "end", "tool": "shout", "input": {"text": "done"}},
            ],
        )
        result = atf("--workspace", str(project), "run", "sideways")
        assert result.code == 1
        assert "switched on 'ELSEWHERE'" in result.err


class TestGates:
    def test_a_gate_that_accepts_lets_the_step_push(self, project: Path, atf: Runner) -> None:
        flow(
            project,
            "gated",
            flow="gated",
            start="draft",
            steps=[
                {
                    "id": "draft",
                    "agent": "writer",
                    "prompt": "three words only",
                    "gate": {
                        "tool": "word_limit",
                        "input": {"text": "{{ this.text }}", "max_words": 10},
                        "feedback": "Too long. {{ gate.text }}",
                    },
                }
            ],
            output={"template": "{{ steps.draft.text }}"},
        )
        result = atf("--workspace", str(project), "run", "gated")
        assert result.code == 0
        assert result.out == "three words only\n"

    def test_a_gate_that_never_accepts_fails_the_step(self, project: Path, atf: Runner) -> None:
        """A gate is not a suggestion. The fake echoes the prompt, so it can never comply."""
        flow(
            project,
            "impossible",
            flow="impossible",
            start="draft",
            steps=[
                {
                    "id": "draft",
                    "agent": "writer",
                    "prompt": "one two three four five",
                    "gate": {
                        "tool": "word_limit",
                        "input": {"text": "{{ this.text }}", "max_words": 2},
                        "feedback": "Too long. {{ gate.text }}",
                        "max_attempts": 2,
                    },
                }
            ],
        )
        result = atf("--workspace", str(project), "run", "impossible")
        assert result.code == 1
        assert "did not pass gate 'word_limit'" in result.err

    def test_a_rejected_attempt_is_reported_as_it_happens(self, project: Path, atf: Runner) -> None:
        """The attempt number says whether the step is converging or running out of turns."""
        flow(
            project,
            "impossible",
            flow="impossible",
            start="draft",
            steps=[
                {
                    "id": "draft",
                    "agent": "writer",
                    "prompt": "one two three four five",
                    "gate": {
                        "tool": "word_limit",
                        "input": {"text": "{{ this.text }}", "max_words": 2},
                        "feedback": "Too long. {{ gate.text }}",
                        "max_attempts": 2,
                    },
                }
            ],
        )
        result = atf("--workspace", str(project), "run", "impossible")
        assert "1/2" in result.err
        assert "2/2" in result.err


class TestSecrets:
    @pytest.fixture
    def sealed(self, project: Path, atf_process: Runner) -> Path:
        vault = project / "secrets.vault"
        atf_process(
            "--workspace",
            str(project),
            "vault",
            "create",
            str(vault),
            stdin="ATF_PROBE_token: s3cret-value\n",
            env={"ATF_VAULT_PASSWORD": "demo"},
        )
        return vault

    def test_a_declared_secret_reaches_the_step_that_declared_it(
        self, project: Path, sealed: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        flow(
            project,
            "signing",
            flow="signing",
            vault="secrets.vault",
            start="sign",
            steps=[
                {
                    "id": "sign",
                    "tool": "reveal",
                    "secrets": ["ATF_PROBE_token"],
                    "input": {"name": "ATF_PROBE_token"},
                }
            ],
            output={"template": "{{ steps.sign.text }}"},
        )
        assert atf("--workspace", str(project), "run", "signing").out == "s3cret-value\n"

    def test_a_step_that_did_not_declare_it_cannot_see_it(
        self, project: Path, sealed: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        flow(
            project,
            "leaky",
            flow="leaky",
            vault="secrets.vault",
            start="peek",
            steps=[{"id": "peek", "tool": "reveal", "input": {"name": "ATF_PROBE_token"}}],
            output={"template": "[{{ steps.peek.text }}]"},
        )
        assert atf("--workspace", str(project), "run", "leaky").out == "[]\n"

    def test_a_secret_an_agent_step_declared_reaches_its_adapter(
        self, project: Path, sealed: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Through the environment, never through the prompt, which lint refuses."""
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        flow(
            project,
            "credentialled",
            flow="credentialled",
            vault="secrets.vault",
            start="ask",
            steps=[
                {
                    "id": "ask",
                    "agent": "writer",
                    "secrets": ["ATF_PROBE_token"],
                    "prompt": "!invocation",
                }
            ],
            output={"template": "{{ steps.ask.text }}"},
        )
        assert (
            '"ATF_PROBE_token": "s3cret-value"'
            in atf("--workspace", str(project), "run", "credentialled").out
        )

    def test_a_secret_a_failing_tool_echoed_is_scrubbed_from_the_error(
        self, project: Path, sealed: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message reaches a terminal and a CI log, so it is scrubbed before it travels."""
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        flow(
            project,
            "leaking",
            flow="leaking",
            vault="secrets.vault",
            start="boom",
            steps=[
                {
                    "id": "boom",
                    "tool": "fail_with",
                    "secrets": ["ATF_PROBE_token"],
                    "input": {"code": 3, "message": "{{ secrets.ATF_PROBE_token }}"},
                }
            ],
        )
        result = atf("--workspace", str(project), "run", "leaking")
        assert result.code == 1
        assert "s3cret-value" not in result.err
        assert "***" in result.err


class TestConcurrency:
    def test_two_steps_off_one_push_run_at_the_same_time(
        self, project: Path, atf: Runner, tmp_path: Path
    ) -> None:
        """Each waits for the other's signal, so the run only finishes if both are running."""
        make.write_tool(
            project,
            "meet_left",
            script=make.rendezvous(tmp_path / "left.flag", tmp_path / "right.flag"),
        )
        make.write_tool(
            project,
            "meet_right",
            script=make.rendezvous(tmp_path / "right.flag", tmp_path / "left.flag"),
        )
        flow(
            project,
            "parallel",
            flow="parallel",
            start="fan",
            steps=[
                {"id": "fan", "tool": "shout", "input": {"text": "go"}, "push": ["l", "r"]},
                {"id": "l", "tool": "meet_left"},
                {"id": "r", "tool": "meet_right"},
            ],
            output={"template": "{{ steps.l.text }} {{ steps.r.text }}"},
        )
        assert atf("--workspace", str(project), "run", "parallel").out == "met met\n"


class TestTheTrace:
    def test_it_is_json_on_stderr_with_one_entry_per_step(self, project: Path, atf: Runner) -> None:
        flow(
            project,
            "traced",
            flow="traced",
            start="a",
            steps=[
                {"id": "a", "tool": "shout", "input": {"text": "one"}, "push": ["b"]},
                {"id": "b", "agent": "writer", "prompt": "two"},
            ],
            output={"template": "{{ steps.b.text }}"},
        )
        result = atf("--workspace", str(project), "run", "traced", "--trace")
        traced = yaml.safe_load(result.err[result.err.index("{") :])
        assert [entry["step"] for entry in traced["steps"]] == ["a", "b"]
        assert result.out == "two\n"

    def test_it_totals_what_the_agent_steps_cost(self, project: Path, atf: Runner) -> None:
        flow(
            project,
            "priced",
            flow="priced",
            start="a",
            steps=[{"id": "a", "agent": "writer", "prompt": "hello"}],
        )
        result = atf("--workspace", str(project), "run", "priced", "--trace")
        traced = yaml.safe_load(result.err[result.err.index("{") :])
        assert traced["cost_usd"] == 0.01

    def test_quiet_leaves_only_the_flows_own_output(self, project: Path, atf: Runner) -> None:
        flow(
            project,
            "hushed",
            flow="hushed",
            start="a",
            steps=[{"id": "a", "tool": "shout", "input": {"text": "hi"}}],
            output={"template": "{{ steps.a.text }}"},
        )
        result = atf("--workspace", str(project), "run", "hushed", "--quiet")
        assert result.out == "HI\n"
        assert result.err == ""
