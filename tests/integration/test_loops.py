"""A loop through the CLI: a flow with an edge back to a step that already ran.

The scheduler itself is covered in `tests/unit/engine/test_loops.py`. What only appears
once the command is assembled is here: `lint` refusing an unbounded loop before anything
runs, the progress lines a pass leaves on stderr, the trace numbering those passes, what
`inspect flow` draws, and an agent step being re-run rather than a tool one.

The agent test goes through `fake_claude`, which is autouse and answers with the prompt it
was given. That is what makes a loop over an agent converge without a model: the prompt
carries the previous pass, so each turn really does produce different text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from support import components as make
from support.outcome import Runner


def loop_flow(project: Path, name: str = "draft", **review: Any) -> None:
    """`write` grows its text and `review` decides whether that is enough yet.

    `write` reads what `review` said, which is the shape the feature is for: the writer is
    handed the last review. On the first pass there is none, and it reads `(not run)`.
    """
    make.write_flow(
        project,
        name,
        {
            "flow": name,
            "start": "write",
            "steps": [
                {
                    "id": "write",
                    "tool": "grow",
                    "input": {"previous": "{{ steps.review.text }}"},
                    "push": ["review"],
                },
                {
                    "id": "review",
                    "tool": "say",
                    "input": {"text": "{{ steps.write.text }}"},
                    "switch": "{{ this.text }}",
                    "cases": {"aaa": ["report"]},
                    "default": ["write"],
                    **review,
                },
                {"id": "report", "tool": "shout", "input": {"text": "{{ steps.write.text }}"}},
            ],
            "output": {"template": "{{ steps.report.text }}"},
        },
    )


def trace_of(result_err: str) -> list[dict[str, Any]]:
    """The --trace document, which shares stderr with the progress lines."""
    return json.loads(result_err[result_err.index("{") :])["steps"]


class TestRunningALoop:
    def test_it_goes_round_until_it_leaves(self, project: Path, atf: Runner) -> None:
        loop_flow(project, max_loops=5)
        result = atf("--workspace", str(project), "run", "draft")
        assert result.code == 0
        assert result.out.strip() == "AAA"

    def test_the_trace_carries_one_entry_per_pass(self, project: Path, atf: Runner) -> None:
        loop_flow(project, max_loops=5)
        result = atf("--workspace", str(project), "run", "draft", "--trace", "--quiet")
        entries = [entry for entry in trace_of(result.err) if entry["step"] == "write"]
        assert [entry.get("iteration") for entry in entries] == [None, 2, 3]

    def test_each_trip_back_shows_on_the_progress(self, project: Path, atf: Runner) -> None:
        loop_flow(project, max_loops=5)
        result = atf("--workspace", str(project), "run", "draft")
        assert result.err.count("⟲ review") == 2
        assert "back to write, loop 1/5" in result.err

    def test_the_step_after_the_loop_is_not_skipped(self, project: Path, atf: Runner) -> None:
        """The exit branch stays pending while the loop runs. Skipped, the skip propagates
        and the run ends with no output at all."""
        loop_flow(project, max_loops=5)
        result = atf("--workspace", str(project), "run", "draft")
        assert "⤼" not in result.err
        assert "report" in result.err


class TestWhenItDoesNotConverge:
    def test_lint_refuses_a_loop_with_no_bound(self, project: Path, atf: Runner) -> None:
        loop_flow(project)
        result = atf("--workspace", str(project), "lint", "draft")
        assert result.code == 1
        assert "add 'max_loops' to 'review'" in result.err

    def test_a_run_that_never_leaves_fails(self, project: Path, atf: Runner) -> None:
        loop_flow(project, max_loops=1)
        result = atf("--workspace", str(project), "run", "draft")
        assert result.code == 1
        assert "did not converge" in result.err
        assert result.out == ""


class TestInspectingALoop:
    def test_the_text_graph_marks_the_edge_that_goes_back(self, project: Path, atf: Runner) -> None:
        loop_flow(project, max_loops=5)
        result = atf("--workspace", str(project), "inspect", "flow", "draft")
        assert "loops back, max 5" in result.out

    def test_the_diagram_reports_the_loop(self, project: Path, atf: Runner) -> None:
        loop_flow(project, max_loops=5)
        result = atf("--workspace", str(project), "inspect", "flow", "draft", "-o", "md")
        assert "## Loops" in result.out
        assert "`review` may send its result back to `write` 5 times" in result.out


class TestAnAgentInsideALoop:
    def test_the_turn_is_taken_again_on_every_pass(self, project: Path, atf: Runner) -> None:
        """`fake_claude` answers with the prompt, which carries the growing text, so the
        switch sees a different value each pass and the loop really terminates."""
        make.write_flow(
            project,
            "reviewed",
            {
                "flow": "reviewed",
                "start": "write",
                "steps": [
                    {
                        "id": "write",
                        "tool": "grow",
                        "input": {"previous": "{{ steps.review.text }}"},
                        "push": ["review"],
                    },
                    {
                        "id": "review",
                        "agent": "writer",
                        "prompt": "{{ steps.write.text }}",
                        "switch": "{{ this.text }}",
                        "max_loops": 5,
                        "cases": {"aaa": ["report"]},
                        "default": ["write"],
                    },
                    {"id": "report", "tool": "shout", "input": {"text": "{{ steps.write.text }}"}},
                ],
                "output": {"template": "{{ steps.report.text }}"},
            },
        )
        result = atf("--workspace", str(project), "run", "reviewed", "--trace", "--quiet")
        assert result.code == 0, result.err
        assert result.out.strip() == "AAA"
        turns = [entry for entry in trace_of(result.err) if entry["step"] == "review"]
        assert len(turns) == 3

    def test_every_pass_is_paid_for(self, project: Path, atf: Runner) -> None:
        """Each pass is a fresh turn, so the run costs three of them and not one."""
        make.write_flow(
            project,
            "reviewed",
            {
                "flow": "reviewed",
                "start": "write",
                "steps": [
                    {
                        "id": "write",
                        "tool": "grow",
                        "input": {"previous": "{{ steps.review.text }}"},
                        "push": ["review"],
                    },
                    {
                        "id": "review",
                        "agent": "writer",
                        "prompt": "{{ steps.write.text }}",
                        "switch": "{{ this.text }}",
                        "max_loops": 5,
                        "cases": {"aaa": ["report"]},
                        "default": ["write"],
                    },
                    {"id": "report", "tool": "shout", "input": {"text": "{{ steps.write.text }}"}},
                ],
                "output": {"template": "{{ steps.report.text }}"},
            },
        )
        result = atf("--workspace", str(project), "run", "reviewed", "--trace", "--quiet")
        spent = [entry["cost_usd"] for entry in trace_of(result.err) if entry["step"] == "review"]
        assert len(spent) == 3
        assert all(cost and cost > 0 for cost in spent)
