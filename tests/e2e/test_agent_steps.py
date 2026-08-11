"""Agent steps in the frozen build, answered by the adapter that needs nothing.

`ADAPTERS` is a dict of static imports, baked in at freeze time, so a suite driving the
binary cannot register an adapter from outside. The one that answers without a runtime has
to ship, and this is where that pays off: a check sending an answer back, a switch on an
agent's structured answer, and a secret reaching a runtime through the environment, all in
the artefact, with no model, no network and no account.

The flows are written here rather than taken from `examples/`, because the shipped examples
name `claude_code` and these are about the engine rather than about them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from support import components as make
from support.outcome import Runner

PROBE = "ATF_PROBE_token"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A workspace with one agent and the tool a check needs."""
    root = tmp_path / "project"
    root.mkdir()
    make.write_agent(root, "writer", model="sonnet")
    make.write_tool(
        root,
        "marker",
        # Answers whether the text carries the marker, and exits 0 either way: the verdict is
        # what the flow switches on, so saying "no" is this tool working rather than failing.
        script=make.python(
            "carries = 'REVISED' in payload.get('text', '')\n"
            "json.dump({'verdict': 'approved' if carries else 'rejected',\n"
            "           'reason': None if carries else 'needs the word REVISED'}, sys.stdout)\n"
        ),
    )
    return root


@pytest.fixture
def nothing_installed(tmp_path: Path) -> dict[str, str]:
    """A PATH with nothing on it, for the case where no runtime exists at all."""
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    return {"PATH": str(empty)}


class TestWithNoRuntimeInstalled:
    def test_an_agent_step_answers_anyway(
        self, project: Path, atf: Runner, nothing_installed: dict[str, str]
    ) -> None:
        make.write_flow(
            project,
            "solo",
            {
                "flow": "solo",
                "start": "draft",
                "steps": [{"id": "draft", "agent": "writer", "prompt": "hello from a bundle"}],
                "output": {"template": "{{ steps.draft.text }}"},
            },
        )
        result = atf("--workspace", str(project), "run", "solo", env=nothing_installed)
        assert result.code == 0, result.err
        assert result.out == "hello from a bundle\n"

    def test_the_adapter_that_needs_one_says_so_instead(
        self, project: Path, atf: Runner, nothing_installed: dict[str, str]
    ) -> None:
        """The other half of the claim: an empty PATH really is empty, so the step above
        answered because `echo` needs nothing rather than because something was reachable."""
        make.write_agent(project, "caller", adapter="claude_code", model="sonnet")
        make.write_flow(
            project,
            "needs_one",
            {
                "flow": "needs_one",
                "start": "draft",
                "steps": [{"id": "draft", "agent": "caller", "prompt": "hello"}],
            },
        )
        result = atf("--workspace", str(project), "run", "needs_one", env=nothing_installed)
        assert result.code == 1
        assert "not on PATH" in result.err

    def test_the_turn_is_paid_for(self, project: Path, atf: Runner) -> None:
        """A flat rate per turn, so what the run reports is a number and not a range."""
        make.write_flow(
            project,
            "solo",
            {
                "flow": "solo",
                "start": "draft",
                "steps": [{"id": "draft", "agent": "writer", "prompt": "hello"}],
                "output": {"template": "{{ steps.draft.text }}"},
            },
        )
        result = atf("--workspace", str(project), "run", "solo", "--trace")
        trace = json.loads(result.err[result.err.index("{") :])
        assert trace["cost_usd"] == 0.01


class TestACheckSendingWorkBack:
    @pytest.fixture(autouse=True)
    def flow(self, project: Path) -> None:
        make.write_flow(
            project,
            "checked",
            {
                "flow": "checked",
                "start": "draft",
                "steps": [
                    {
                        "id": "draft",
                        "agent": "writer",
                        "prompt": "write it{% if steps.check %}. REVISED: "
                        "{{ steps.check.json.reason }}{% endif %}",
                        "push": ["check"],
                    },
                    {
                        "id": "check",
                        "tool": "marker",
                        "input": {"text": "{{ steps.draft.text }}"},
                        "switch": "{{ this.json.verdict }}",
                        "max_loops": 2,
                        "cases": {"approved": [], "rejected": ["draft"]},
                    },
                ],
                "output": {"template": "{{ steps.draft.text }}"},
            },
        )

    def test_a_rejected_answer_goes_back_with_what_the_check_said(
        self, project: Path, atf: Runner
    ) -> None:
        """Every turn is a fresh session, so the prompt is the only place history can live.
        The next pass reads the verdict out of `steps` and the adapter answering with the
        prompt is what makes the second turn observably different from the first."""
        result = atf("--workspace", str(project), "run", "checked")
        assert result.code == 0, result.err
        assert "REVISED: needs the word REVISED" in result.out

    def test_the_trip_back_is_reported_as_it_happens(self, project: Path, atf: Runner) -> None:
        """A rejection is a verdict, not a failure, so it is narrated and the flow goes on to
        succeed."""
        result = atf("--workspace", str(project), "run", "checked")
        assert "back to draft, loop 1/2" in result.err
        assert result.err.count("✓ draft") == 2

    def test_every_pass_is_paid_for(self, project: Path, atf: Runner) -> None:
        """Two turns, and the trace carries one row per pass rather than one for the step."""
        result = atf("--workspace", str(project), "run", "checked", "--trace")
        trace = json.loads(result.err[result.err.index("{") :])
        drafts = [step for step in trace["steps"] if step["step"] == "draft"]
        assert [step["cost_usd"] for step in drafts] == [0.01, 0.01]
        assert trace["cost_usd"] == 0.02


class TestASwitchOnTheAnswer:
    @pytest.fixture(autouse=True)
    def flow(self, project: Path) -> None:
        make.write_flow(
            project,
            "branching",
            {
                "flow": "branching",
                "start": "triage",
                "inputs": {"verdict": {"type": "string", "required": True}},
                "steps": [
                    {
                        "id": "triage",
                        "agent": "writer",
                        "prompt": '!json {"verdict": "{{ inputs.verdict }}"}',
                        "switch": "{{ this.json.verdict }}",
                        "cases": {"risky": ["scan"], "clean": ["note"]},
                    },
                    {"id": "scan", "agent": "writer", "prompt": "scanning", "push": ["report"]},
                    {
                        "id": "note",
                        "agent": "writer",
                        "prompt": "nothing to see",
                        "push": ["report"],
                    },
                    {"id": "report", "agent": "writer", "prompt": "scan={{ steps.scan.text }}"},
                ],
                "output": {"template": "{{ steps.report.text }}"},
            },
        )

    @pytest.mark.parametrize(
        ("verdict", "taken", "skipped"),
        [("risky", "scan", "note"), ("clean", "note", "scan")],
    )
    def test_the_named_case_runs_and_the_other_is_skipped(
        self, project: Path, atf: Runner, verdict: str, taken: str, skipped: str
    ) -> None:
        result = atf(
            "--workspace", str(project), "run", "branching", "--input", f"verdict={verdict}"
        )
        assert result.code == 0, result.err
        assert f"✓ {taken}" in result.err
        assert f"⤼ {skipped}" in result.err

    def test_the_join_runs_on_either_path(self, project: Path, atf: Runner) -> None:
        """A step whose every inbound edge is skipped is skipped too, and that cascades. The
        join has one delivered edge either way, so it runs."""
        result = atf("--workspace", str(project), "run", "branching", "--input", "verdict=clean")
        assert result.out == "scan=(not run)\n"

    def test_a_value_matching_no_case_is_refused(self, project: Path, atf: Runner) -> None:
        result = atf("--workspace", str(project), "run", "branching", "--input", "verdict=maybe")
        assert result.code == 1
        assert "maybe" in result.err


class TestASecretReachingTheAdapter:
    def test_it_arrives_in_the_environment_and_not_in_the_prompt(
        self, project: Path, atf: Runner, tmp_path: Path
    ) -> None:
        """A secret in an agent prompt is refused outright, because it would be sent to the
        model and persist in the session. The environment is the way in, and `!invocation`
        is how the adapter reports what it actually received."""
        vault = tmp_path / "secrets.vault"
        password = tmp_path / "vault.pw"
        password.write_text("correct horse\n")
        created = atf(
            "vault",
            "create",
            str(vault),
            "--vault-password-file",
            str(password),
            stdin=f"{PROBE}: from-the-vault\n",
        )
        assert created.code == 0, created.err

        make.write_flow(
            project,
            "probing",
            {
                "flow": "probing",
                "vault": str(vault),
                "start": "probe",
                "steps": [
                    {
                        "id": "probe",
                        "agent": "writer",
                        "secrets": [PROBE],
                        "prompt": "!invocation",
                    }
                ],
                "output": {"template": "{{ steps.probe.text }}"},
            },
        )
        result = atf(
            "--workspace",
            str(project),
            "run",
            "probing",
            "--vault-password-file",
            str(password),
        )
        assert result.code == 0, result.err
        reported = json.loads(result.out)
        assert reported["env"][PROBE] == "from-the-vault"
        assert PROBE not in reported["payload"]["prompt"]
