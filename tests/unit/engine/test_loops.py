"""A loop: an edge back to a step that already ran, and the bound that stops it.

The only cycle a flow may have. Nothing declares it; `validate` finds it from the graph,
so what makes an edge a back-edge is where its target already sits. The rules that go with
one are in test_validation.py. This file is what the scheduler does once there is one:
which steps run again, which do not, and what a step that has not run yet resolves to.

Every step here is a tool step, on purpose. A loop is scheduling, and a tool that appends a
character converges after a fixed number of passes, so the count a test asserts on is the
engine's rather than a model's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from engine.executor import SKIPPED_RESULT, FlowError, execute
from paths.resolver import Paths
from support import components as make

NOT_RUN = SKIPPED_RESULT["text"]


def write_loop_tools(workspace: Path) -> None:
    make.write_tool(workspace, "grow", script=make.grows("a", NOT_RUN))
    make.write_tool(workspace, "say", script=make.echoes_input("text"))


def loop_flow(max_loops: int = 5, leave_on: str = "aaa", **overrides: Any) -> dict[str, Any]:
    """`grow` appends a character and `check` decides whether that is enough yet.

    Three passes to reach "aaa", so a test can watch a count rather than assert that
    something happened at all.
    """
    built: dict[str, Any] = {
        "flow": "demo",
        "start": "grow",
        "steps": [
            {
                "id": "grow",
                "tool": "grow",
                "input": {"previous": "{{ steps.check.text }}"},
                "push": ["check"],
            },
            {
                "id": "check",
                "tool": "say",
                "input": {"text": "{{ steps.grow.text }}"},
                "switch": "{{ this.text }}",
                "max_loops": max_loops,
                "cases": {leave_on: ["report"]},
                "default": ["grow"],
            },
            {"id": "report", "tool": "say", "input": {"text": "done {{ steps.grow.text }}"}},
        ],
    }
    built.update(overrides)
    return built


def ran(trace: list[dict[str, Any]]) -> list[str]:
    return [entry["step"] for entry in trace]


class TestALoopRuns:
    def test_the_body_runs_once_per_pass(self, paths: Paths, workspace: Path) -> None:
        write_loop_tools(workspace)
        definition = loop_flow()
        _, trace = execute(definition, definition["steps"], {}, paths)
        assert ran(trace) == ["grow", "check"] * 3 + ["report"]

    def test_the_step_after_the_loop_runs_once_the_loop_leaves(
        self, paths: Paths, workspace: Path
    ) -> None:
        """The exit branch has to stay pending while the loop goes round. Marked skipped
        the way an untaken branch is, the skip propagates and everything after the loop is
        skipped with it, so the run ends with no output."""
        write_loop_tools(workspace)
        definition = loop_flow()
        results, _ = execute(definition, definition["steps"], {}, paths)
        assert results["report"]["text"] == "done aaa"
        assert "skipped" not in results["report"]

    def test_what_a_looping_step_leaves_behind_is_its_last_pass(
        self, paths: Paths, workspace: Path
    ) -> None:
        write_loop_tools(workspace)
        definition = loop_flow()
        results, _ = execute(definition, definition["steps"], {}, paths)
        assert results["grow"]["text"] == "aaa"

    def test_the_first_pass_reads_not_run_from_a_step_that_has_not_run(
        self, paths: Paths, workspace: Path
    ) -> None:
        """`grow` reads `check`, which is downstream of it and has not run yet. A loop's
        steps are mutually upstream, so the reference is legal and something has to
        resolve. Without the seeded value the template is simply unresolvable."""
        make.write_tool(workspace, "grow", script=make.echoes_input("previous"))
        make.write_tool(workspace, "say", script=make.echoes_input("text"))
        definition = loop_flow(leave_on=NOT_RUN)
        results, trace = execute(definition, definition["steps"], {}, paths)
        assert results["grow"]["text"] == NOT_RUN
        assert ran(trace) == ["grow", "check", "report"]

    def test_a_step_can_read_its_own_previous_result(self, paths: Paths, workspace: Path) -> None:
        """A loop makes a step its own ancestor, so `{{ steps.grow.text }}` inside `grow`
        is what `grow` produced last pass. Converging is the proof: reading the seeded
        placeholder every time would append to `(not run)` forever and hit the bound.

        This is what lets a pass edit the last answer rather than start over, which is the
        difference between a loop that converges and one that runs out."""
        write_loop_tools(workspace)
        definition = loop_flow()
        definition["steps"][0]["input"] = {"previous": "{{ steps.grow.text }}"}
        results, trace = execute(definition, definition["steps"], {}, paths)
        assert results["grow"]["text"] == "aaa"
        assert ran(trace).count("grow") == 3

    def test_a_join_inside_the_loop_runs_on_every_pass(self, paths: Paths, workspace: Path) -> None:
        write_loop_tools(workspace)
        definition = loop_flow()
        definition["steps"][0]["push"] = ["check", "note"]
        definition["steps"].append(
            {"id": "note", "tool": "say", "input": {"text": "seen"}, "push": ["check"]}
        )
        _, trace = execute(definition, definition["steps"], {}, paths)
        assert ran(trace).count("note") == 3

    def test_a_step_beside_the_loop_runs_once(self, paths: Paths, workspace: Path) -> None:
        """Only the loop's own steps go back to waiting. A step the loop head never
        reaches is not part of the pass and must not be re-run by one."""
        write_loop_tools(workspace)
        definition = loop_flow(start="seed")
        definition["steps"].append(
            {"id": "seed", "tool": "say", "input": {"text": "go"}, "push": ["grow", "aside"]}
        )
        definition["steps"].append({"id": "aside", "tool": "say", "input": {"text": "once"}})
        _, trace = execute(definition, definition["steps"], {}, paths)
        assert ran(trace).count("aside") == 1
        assert ran(trace).count("grow") == 3


class TestTheBound:
    def test_a_loop_that_does_not_converge_fails(self, paths: Paths, workspace: Path) -> None:
        write_loop_tools(workspace)
        definition = loop_flow(max_loops=1)
        with pytest.raises(FlowError, match="did not converge"):
            execute(definition, definition["steps"], {}, paths)

    def test_the_failure_names_the_step_it_kept_going_back_to(
        self, paths: Paths, workspace: Path
    ) -> None:
        write_loop_tools(workspace)
        definition = loop_flow(max_loops=1)
        with pytest.raises(FlowError, match="back to 'grow'"):
            execute(definition, definition["steps"], {}, paths)

    def test_converging_on_the_last_allowed_pass_is_not_a_failure(
        self, paths: Paths, workspace: Path
    ) -> None:
        """Reaching "aaa" takes two trips back, so a bound of two is exactly enough."""
        write_loop_tools(workspace)
        definition = loop_flow(max_loops=2)
        results, _ = execute(definition, definition["steps"], {}, paths)
        assert results["report"]["text"] == "done aaa"

    def test_a_failed_loop_is_reported_as_a_failed_step(
        self, paths: Paths, workspace: Path
    ) -> None:
        write_loop_tools(workspace)
        definition = loop_flow(max_loops=1)
        events: list[dict[str, Any]] = []
        with pytest.raises(FlowError):
            execute(definition, definition["steps"], {}, paths, on_event=events.append)
        assert [event["step"] for event in events if event["kind"] == "failed"] == ["check"]


def nested_flow(inner_loops: int = 5, outer_loops: int = 5) -> dict[str, Any]:
    """A cheap check inside an expensive one, both sending the work back to `grow`.

    `grow` appends a character to its own last result, so each pass is a value neither
    check has seen. `inner` sends "a" and "aaa" back and lets everything else through;
    `outer` ends on "aaaa" and sends everything else back. Four passes, and the third is
    an inner trip taken *after* the outer loop has already fired once, which is the pass
    that nothing else in this file reaches.
    """
    return {
        "flow": "demo",
        "start": "grow",
        "steps": [
            {
                "id": "grow",
                "tool": "grow",
                "input": {"previous": "{{ steps.grow.text }}"},
                "push": ["inner"],
            },
            {
                "id": "inner",
                "tool": "say",
                "input": {"text": "{{ steps.grow.text }}"},
                "switch": "{{ this.text }}",
                "max_loops": inner_loops,
                "cases": {"a": ["grow"], "aaa": ["grow"]},
                "default": ["outer"],
            },
            {
                "id": "outer",
                "tool": "say",
                "input": {"text": "{{ steps.grow.text }}"},
                "switch": "{{ this.text }}",
                "max_loops": outer_loops,
                "cases": {"aaaa": []},
                "default": ["grow"],
            },
        ],
    }


class TestALoopInsideAnother:
    """Two loops sharing a body, which `validate` allows only where one contains the other.

    The shape a deterministic check inside a review has: reject cheaply and often, review
    expensively and rarely, and both send the work back to whatever produced it.
    """

    def test_the_outer_pass_really_runs_the_inner_body_again(
        self, paths: Paths, workspace: Path
    ) -> None:
        """The inner back-edge has to go back to *skipped* when the outer loop re-enters.

        Left pending, the inner head waits on a step downstream of itself, nothing in the
        body ever becomes ready, and `execute` returns with the outer pass never run. That
        failure is silent: a run that reports the trip back, exits 0, and emits the results
        of the pass before it.
        """
        write_loop_tools(workspace)
        definition = nested_flow()
        results, trace = execute(definition, definition["steps"], {}, paths)
        assert ran(trace).count("grow") == 4
        assert results["grow"]["text"] == "aaaa"

    def test_each_loop_runs_its_own_body(self, paths: Paths, workspace: Path) -> None:
        """`outer` is in the outer body only, so an inner trip back does not re-run it."""
        write_loop_tools(workspace)
        definition = nested_flow()
        _, trace = execute(definition, definition["steps"], {}, paths)
        assert ran(trace) == ["grow", "inner"] * 2 + ["outer"] + ["grow", "inner"] * 2 + ["outer"]

    def test_a_bound_counts_over_the_run_and_not_over_one_outer_pass(
        self, paths: Paths, workspace: Path
    ) -> None:
        """`max_loops` is never reset, so two nested bounds of three are six passes and not
        sixteen. Here the inner loop takes one trip back before the outer fires and one
        after, so a bound of one is spent by the second and the step fails."""
        write_loop_tools(workspace)
        definition = nested_flow(inner_loops=1)
        with pytest.raises(FlowError, match="back to 'grow'"):
            execute(definition, definition["steps"], {}, paths)

    def test_both_counts_are_reported_separately(self, paths: Paths, workspace: Path) -> None:
        write_loop_tools(workspace)
        definition = nested_flow()
        events: list[dict[str, Any]] = []
        execute(definition, definition["steps"], {}, paths, on_event=events.append)
        looped = [event for event in events if event["kind"] == "looped"]
        assert [(event["step"], event["count"]) for event in looped] == [
            ("inner", 1),
            ("outer", 1),
            ("inner", 2),
        ]


class TestWhatIsReported:
    def test_every_trip_back_is_an_event(self, paths: Paths, workspace: Path) -> None:
        write_loop_tools(workspace)
        definition = loop_flow()
        events: list[dict[str, Any]] = []
        execute(definition, definition["steps"], {}, paths, on_event=events.append)
        looped = [event for event in events if event["kind"] == "looped"]
        assert [(event["step"], event["to"], event["count"], event["of"]) for event in looped] == [
            ("check", "grow", 1, 5),
            ("check", "grow", 2, 5),
        ]

    def test_the_trace_numbers_the_passes_after_the_first(
        self, paths: Paths, workspace: Path
    ) -> None:
        """Present only where it says something, the way `attempts` is. A flow without a
        loop would otherwise carry `iteration: 1` on every step of every run."""
        write_loop_tools(workspace)
        definition = loop_flow()
        _, trace = execute(definition, definition["steps"], {}, paths)
        grew = [entry for entry in trace if entry["step"] == "grow"]
        assert [entry.get("iteration") for entry in grew] == [None, 2, 3]
