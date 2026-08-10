"""A flow that is a directory, with its prompts in files and a conditional in one.

Three parts that only meet here. The resolver has to find `flows/report/report.yaml` by the
name `report`, `load_flow` has to read `prompts/report.md` from beside whatever it found,
and the renderer has to drop the branch the run did not take. What proves the last one is
that the fake `claude` answers with the prompt it was given, so the branch that reached the
model is on stdout.

The malformed-tag test is the one that could not be a unit test: it asks whether `lint`
refuses a bad tag in a prompt *file*, which means the parse has to happen on the way in
rather than when the step runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from support import components as make

from .conftest import Runner

REPORT = """\
Verdict: {{ steps.triage.json.verdict }}
{% if steps.scan %}
Scan: {{ steps.scan.text }}
{% else %}
No scan was run.
{% endif %}
"""


@pytest.fixture
def bundle(project: Path) -> Path:
    """A branchy flow in bundle form, whose join reads its prompt from a file."""
    flow = make.write_flow(
        project,
        "report",
        {
            "flow": "report",
            "start": "triage",
            "steps": [
                {
                    "id": "triage",
                    "agent": "classifier",
                    "prompt": "classify it",
                    "switch": "{{ this.json.verdict }}",
                    "cases": {"risky": ["scan"], "clean": ["report"]},
                },
                {"id": "scan", "tool": "shout", "input": {"text": "scanned"}, "push": ["report"]},
                {"id": "report", "agent": "writer", "prompt_file": "report"},
            ],
            "output": {"template": "{{ steps.report.text }}"},
        },
        bundle=True,
    )
    make.write_prompt_file(flow, "report", REPORT)
    return flow


class TestRunningABundle:
    def test_the_flow_is_found_by_its_own_name(
        self, project: Path, bundle: Path, atf: Runner
    ) -> None:
        """`flows/report/report.yaml`, run as `report`. The directory is not part of the name."""
        result = atf("--workspace", str(project), "lint", "report")
        assert result.code == 0, result.err
        assert "flows/report/report.yaml" in result.out

    def test_the_prompt_file_reaches_the_model(
        self, project: Path, bundle: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_PREFER", "risky")
        result = atf("--workspace", str(project), "run", "report")
        assert result.code == 0, result.err
        assert "Verdict: risky" in result.out

    def test_the_taken_branch_is_the_one_the_model_is_handed(
        self, project: Path, bundle: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_PREFER", "risky")
        result = atf("--workspace", str(project), "run", "report")
        assert "Scan: SCANNED" in result.out
        assert "No scan was run" not in result.out

    def test_a_skipped_step_takes_the_other_branch(
        self, project: Path, bundle: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The alternative to templating "(not run)" and explaining it in a system prompt:
        the prompt says what happened and the reference is never rendered."""
        monkeypatch.setenv("FAKE_CLAUDE_PREFER", "clean")
        result = atf("--workspace", str(project), "run", "report")
        assert result.code == 0, result.err
        assert "⤼ scan" in result.err
        assert "No scan was run" in result.out
        assert "Scan:" not in result.out
        assert "(not run)" not in result.out

    def test_the_bundle_is_listed_under_its_own_name(
        self, project: Path, bundle: Path, atf: Runner
    ) -> None:
        """Once, as `report`. Not also as `report/report`, which is a name nobody wrote.

        The names rather than the whole listing, because the path column really does read
        `./flows/report/report.yaml` and a substring check would pass on that."""
        result = atf("--workspace", str(project), "list")
        listed = [parts[0] for line in result.out.splitlines() if (parts := line.split())]
        assert listed.count("report") == 1
        assert "report/report" not in listed

    def test_its_graph_can_still_be_printed(self, project: Path, bundle: Path, atf: Runner) -> None:
        result = atf("--workspace", str(project), "inspect", "flow", "report")
        assert result.code == 0, result.err
        assert result.out.startswith("report")


class TestWhatLintCatches:
    def test_a_missing_prompt_file_is_refused_before_anything_runs(
        self, project: Path, bundle: Path, atf: Runner
    ) -> None:
        (bundle.parent / "prompts" / "report.md").unlink()
        result = atf("--workspace", str(project), "lint", "report")
        assert result.code == 1
        assert "prompts/report.md" in result.err

    def test_a_malformed_tag_in_a_prompt_file_is_refused(
        self, project: Path, bundle: Path, atf: Runner
    ) -> None:
        """`template_refs` parses, so `lint` reads the tag rather than the step waiting to
        fail on it after the turn before it has been paid for."""
        make.write_prompt_file(bundle, "report", "{% if steps.scan %}\nno end\n")
        result = atf("--workspace", str(project), "lint", "report")
        assert result.code == 1
        assert "{% endif %}" in result.err

    def test_a_guarded_reference_is_still_checked(
        self, project: Path, bundle: Path, atf: Runner
    ) -> None:
        """A branch that would not run is not a way past validation."""
        guarded = "{% if steps.scan %}{{ steps.ghost.text }}{% endif %}"
        make.write_prompt_file(bundle, "report", guarded)
        result = atf("--workspace", str(project), "lint", "report")
        assert result.code == 1
        assert "unknown step 'ghost'" in result.err

    def test_a_condition_on_a_step_that_is_not_upstream_is_refused(
        self, project: Path, bundle: Path, atf: Runner
    ) -> None:
        make.write_prompt_file(bundle, "report", "{% if steps.report %}x{% endif %}")
        result = atf("--workspace", str(project), "lint", "report")
        assert result.code == 1
        assert "not upstream" in result.err
