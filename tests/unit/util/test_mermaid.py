"""The diagram, and the things it works out that the flow does not write down.

`guaranteed` is the one to test hardest. A switch guarantees a step when every case
*eventually* reaches it, however far downstream, and that transitivity is what a reader
tracing a branchy flow by eye most often gets wrong. A first cut of the module got it
wrong too, so each shape it has to handle gets its own test.

A loop is the second reading of it, and it goes the other way: a case that only sends work
back upstream is not a case that fails to reach the target, because it is not a way out of
the flow at all. Read as an ordinary case it would report everything after a loop as
skippable, so both answers are asserted against the same steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from engine.executor import build_graph, without_back_edges
from paths.resolver import Paths
from support import components as make
from util.mermaid import (
    always_reaches,
    describe_step,
    guaranteed_steps,
    node_ids,
    reachable_from,
    render,
    topological_order,
    waves,
)

BRANCHY = [
    {
        "id": "triage",
        "tool": "classify",
        "switch": "{{ this.text }}",
        "cases": {"deep": ["scan"], "quick": ["report"]},
    },
    {"id": "scan", "agent": "scanner", "prompt": "look", "push": ["report"]},
    {"id": "report", "agent": "reporter", "prompt": "write"},
]


def by_id(steps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {step["id"]: step for step in steps}


class TestNodeIds:
    def test_ids_are_positional(self) -> None:
        assert node_ids(["a", "b"]) == {"a": "n0", "b": "n1"}

    def test_two_names_that_sanitise_alike_stay_apart(self) -> None:
        """Deriving the node id from the name would collapse these into one node."""
        mapping = node_ids(["read-target", "read_target"])
        assert mapping["read-target"] != mapping["read_target"]


class TestTopologicalOrder:
    def test_a_step_comes_after_everything_it_waits_on(self) -> None:
        steps = [
            {"id": "a", "push": ["b"]},
            {"id": "b", "push": ["c"]},
            {"id": "c", "tool": "t"},
        ]
        _, inbound = build_graph(steps)
        assert topological_order(["c", "b", "a"], inbound) == ["a", "b", "c"]

    def test_steps_at_the_same_depth_come_out_in_a_fixed_order(self) -> None:
        """Sorted, so the diagram of an unchanged flow is byte-for-byte unchanged."""
        steps = [{"id": "a", "push": ["z", "b"]}, {"id": "b"}, {"id": "z"}]
        _, inbound = build_graph(steps)
        assert topological_order(["z", "b", "a"], inbound) == ["a", "b", "z"]

    def test_a_cycle_does_not_hang(self) -> None:
        """It is handed the graph with its loops already opened, so a cycle reaching here is
        one validate() refuses. This only has to terminate."""
        _, inbound = build_graph([{"id": "a", "push": ["b"]}, {"id": "b", "push": ["a"]}])
        assert sorted(topological_order(["a", "b"], inbound)) == ["a", "b"]


class TestWaves:
    def test_the_first_step_is_wave_one(self) -> None:
        _, inbound = build_graph([{"id": "a", "tool": "t"}])
        assert waves(["a"], inbound) == {"a": 1}

    def test_two_steps_off_one_push_share_a_wave(self) -> None:
        steps = [{"id": "a", "push": ["l", "r"]}, {"id": "l"}, {"id": "r"}]
        _, inbound = build_graph(steps)
        assert waves(["a", "l", "r"], inbound) == {"a": 1, "l": 2, "r": 2}

    def test_a_join_sits_after_everything_it_waits_on(self) -> None:
        steps = [
            {"id": "a", "push": ["l", "r"]},
            {"id": "l", "push": ["long"]},
            {"id": "long", "push": ["join"]},
            {"id": "r", "push": ["join"]},
            {"id": "join"},
        ]
        _, inbound = build_graph(steps)
        assert waves(list(by_id(steps)), inbound)["join"] == 4


class TestAlwaysReaches:
    @staticmethod
    def reaches(
        steps: list[dict[str, Any]], target: str, back: set[tuple[str, str]] | None = None
    ) -> dict[str, bool]:
        back = back or set()
        outbound, _ = build_graph(steps)
        _, forward_in = without_back_edges(outbound, back)
        order = topological_order(list(by_id(steps)), forward_in)
        return always_reaches(target, by_id(steps), order, back)

    def test_a_step_reaches_itself(self) -> None:
        assert self.reaches([{"id": "a"}], "a")["a"] is True

    def test_a_push_reaches_what_any_of_its_targets_reaches(self) -> None:
        """A push runs all of its targets, so one of them arriving is enough."""
        steps = [
            {"id": "a", "push": ["l", "r"]},
            {"id": "l", "push": ["end"]},
            {"id": "r"},
            {"id": "end"},
        ]
        assert self.reaches(steps, "end")["a"] is True

    def test_a_switch_reaches_only_what_every_case_reaches(self) -> None:
        """A switch runs exactly one case, so a step one case misses is not guaranteed."""
        assert self.reaches(BRANCHY, "scan")["triage"] is False

    def test_a_switch_whose_cases_converge_does_reach_it(self) -> None:
        assert self.reaches(BRANCHY, "report")["triage"] is True

    def test_reaching_is_transitive_rather_than_direct(self) -> None:
        """`triage` never names `report` in the deep case; it gets there through `scan`."""
        assert "report" not in (BRANCHY[0]["cases"]["deep"])

    def test_a_default_branch_counts_as_a_case(self) -> None:
        steps = [
            {"id": "a", "switch": "x", "cases": {"one": ["end"]}, "default": ["other"]},
            {"id": "other"},
            {"id": "end"},
        ]
        assert self.reaches(steps, "end")["a"] is False

    def test_a_terminal_step_reaches_nothing_but_itself(self) -> None:
        steps = [{"id": "a", "push": ["b"]}, {"id": "b"}]
        assert self.reaches(steps, "a")["b"] is False


class TestAlwaysReachesAcrossALoop:
    LOOP = [
        {"id": "write", "tool": "t", "push": ["check"]},
        {
            "id": "check",
            "tool": "t",
            "switch": "{{ this.text }}",
            "max_loops": 4,
            "cases": {"done": ["report"], "again": ["write"]},
        },
        {"id": "report", "tool": "t"},
    ]

    def test_a_step_after_a_loop_still_always_runs(self) -> None:
        """A case that only goes back upstream is not a way out of the flow: a loop either
        leaves through another case or runs out of passes and fails. Counted as a case that
        misses the target, it would report everything after a loop as skippable."""
        reaches = TestAlwaysReaches.reaches(self.LOOP, "report", back={("check", "write")})
        assert reaches["check"] is True
        assert reaches["write"] is True

    def test_without_that_the_answer_would_be_the_opposite(self) -> None:
        """The same flow read as if the back-edge were an ordinary case."""
        assert TestAlwaysReaches.reaches(self.LOOP, "report")["check"] is False


class TestGuaranteedSteps:
    def test_every_step_of_a_linear_flow_runs(self) -> None:
        steps = [{"id": "a", "push": ["b"]}, {"id": "b"}]
        _, inbound = build_graph(steps)
        flow = {"flow": "d", "start": "a"}
        assert guaranteed_steps(flow, by_id(steps), inbound, set()) == {"a", "b"}

    def test_a_branch_target_is_not_guaranteed_but_the_join_is(self) -> None:
        _, inbound = build_graph(BRANCHY)
        flow = {"flow": "d", "start": "triage"}
        assert guaranteed_steps(flow, by_id(BRANCHY), inbound, set()) == {"triage", "report"}


class TestReachableFrom:
    def test_walks_forward_from_the_seeds(self) -> None:
        outbound, _ = build_graph(
            [{"id": "a", "push": ["b"]}, {"id": "b", "push": ["c"]}, {"id": "c"}]
        )
        assert reachable_from(["a"], outbound) == {"a", "b", "c"}

    def test_no_seeds_reach_nothing(self) -> None:
        outbound, _ = build_graph([{"id": "a", "push": ["b"]}, {"id": "b"}])
        assert reachable_from([], outbound) == set()

    def test_a_cycle_does_not_hang(self) -> None:
        outbound, _ = build_graph([{"id": "a", "push": ["b"]}, {"id": "b", "push": ["a"]}])
        assert reachable_from(["a"], outbound) == {"a", "b"}


class TestDescribeStep:
    def test_a_tool_step_is_labelled_with_its_tool(self, paths: Paths) -> None:
        label, kind = describe_step({"id": "read", "tool": "read_file"}, paths)
        assert kind == "tool"
        assert "tool: read_file" in label

    def test_an_agent_step_carries_its_model_and_effort(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_agent(workspace, "writer", model="sonnet", effort="low")
        label, kind = describe_step({"id": "draft", "agent": "writer"}, paths)
        assert kind == "agent"
        assert "writer · sonnet/low" in label

    def test_a_setting_the_agent_left_out_shows_as_a_question_mark(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_agent(workspace, "writer")
        label, _ = describe_step({"id": "draft", "agent": "writer"}, paths)
        assert "writer · ?/?" in label

    def test_an_agent_that_cannot_be_read_still_gets_a_node(self, paths: Paths) -> None:
        """A diagram of a broken flow is still useful, which is why this one swallows."""
        label, kind = describe_step({"id": "draft", "agent": "absent"}, paths)
        assert kind == "agent"
        assert "<unreadable>" in label


class TestRender:
    @pytest.fixture
    def project(self, workspace: Path) -> Paths:
        make.write_tool(workspace, "classify")
        make.write_agent(workspace, "scanner")
        make.write_agent(workspace, "reporter")
        return Paths(workspace, env={}, home=workspace / "home")

    @pytest.fixture
    def markdown(self, project: Paths) -> str:
        flow = {"flow": "review", "start": "triage", "description": "Look at a file."}
        return render(flow, BRANCHY, project)

    def test_it_opens_with_the_flow_name_and_description(self, markdown: str) -> None:
        assert markdown.startswith("# review\n\nLook at a file.\n")

    def test_a_flow_with_no_description_says_so(self, project: Paths) -> None:
        flow = {"flow": "review", "start": "triage"}
        assert "_no description_" in render(flow, BRANCHY, project)

    def test_it_contains_a_mermaid_diagram(self, markdown: str) -> None:
        assert "```mermaid\nflowchart TD\n" in markdown

    def test_a_switch_edge_is_dotted_and_labelled_with_its_case(self, markdown: str) -> None:
        """Dotted because whether it is taken is decided at run time."""
        assert '-.->|"deep"|' in markdown
        assert '-.->|"quick"|' in markdown

    def test_a_step_that_may_be_skipped_is_marked(self, markdown: str) -> None:
        assert "classDef skippable stroke-dasharray:4 3;" in markdown
        assert " skippable;" in markdown

    def test_each_class_is_its_own_statement(self, markdown: str) -> None:
        """In `class a,b c;` the comma separates node ids, so one line would name a class
        that matches no rule at all."""
        assert "agent,skippable" not in markdown

    def test_it_reports_which_steps_run_concurrently(self, markdown: str) -> None:
        assert "| wave | runs concurrently |" in markdown
        assert "| 1 | `triage` |" in markdown

    def test_the_step_table_says_what_always_runs(self, markdown: str) -> None:
        assert "| step | kind | always runs | waits on | pushes to |" in markdown
        assert "| `report` | agent `reporter` | yes |" in markdown
        assert "| `scan` | agent `scanner` | no |" in markdown

    def test_a_terminal_step_is_marked_in_the_table(self, markdown: str) -> None:
        assert "_terminal_" in markdown

    def test_a_flow_without_secrets_has_no_secrets_column(self, markdown: str) -> None:
        assert "secrets" not in markdown

    def test_a_flow_with_secrets_names_them_per_step(self, project: Paths) -> None:
        """Names only. A diagram is meant to be shared."""
        steps = [{"id": "sign", "tool": "classify", "secrets": ["signing_key"]}]
        markdown = render({"flow": "s", "start": "sign"}, steps, project)
        assert "| step | kind | secrets | always runs |" in markdown
        assert "`signing_key`" in markdown

    def test_a_step_with_no_secrets_in_a_flow_that_uses_them_says_none(
        self, project: Paths
    ) -> None:
        steps = [
            {"id": "read", "tool": "classify", "push": ["sign"]},
            {"id": "sign", "tool": "classify", "secrets": ["signing_key"]},
        ]
        markdown = render({"flow": "s", "start": "read"}, steps, project)
        assert "_none_" in markdown

    def test_the_branches_section_lists_what_each_case_skips(self, markdown: str) -> None:
        assert "## Branches" in markdown
        assert "- `deep` → `scan`" in markdown
        assert "(skipped otherwise: `scan`)" in markdown

    def test_a_switch_without_a_default_says_what_happens_to_an_unmatched_value(
        self, markdown: str
    ) -> None:
        assert "no default: a value outside these cases fails the run" in markdown

    def test_a_flow_with_no_switch_has_no_branches_section(self, project: Paths) -> None:
        steps = [{"id": "a", "tool": "classify"}]
        assert "## Branches" not in render({"flow": "s", "start": "a"}, steps, project)

    def test_a_default_branch_is_drawn_and_listed_like_a_case(self, project: Paths) -> None:
        steps = [
            {
                "id": "triage",
                "tool": "classify",
                "switch": "{{ this.text }}",
                "cases": {"deep": ["scan"]},
                "default": ["report"],
            },
            {"id": "scan", "agent": "scanner", "prompt": "look"},
            {"id": "report", "agent": "reporter", "prompt": "write"},
        ]
        markdown = render({"flow": "review", "start": "triage"}, steps, project)
        assert '-.->|"default"|' in markdown
        assert "- `default` → `report`" in markdown
        assert "no default:" not in markdown

    def test_the_joins_section_names_what_may_be_skipped(self, markdown: str) -> None:
        assert "## Joins" in markdown
        assert "`report` waits on `scan`, `triage`" in markdown
        assert "may be skipped, which unblocks rather than stalls this step" in markdown

    def test_it_ends_with_exactly_one_newline(self, markdown: str) -> None:
        """It is written to a file as-is, so a document, not a message."""
        assert markdown.endswith("\n")
        assert not markdown.endswith("\n\n")


class TestRenderALoop:
    """A loop is a real edge and is drawn as one. Everything the report derives is read off
    the graph with the loop opened, because none of it means anything on a cycle."""

    LOOP = [
        {"id": "write", "tool": "classify", "push": ["check"]},
        {
            "id": "check",
            "tool": "classify",
            "switch": "{{ this.text }}",
            "max_loops": 4,
            "cases": {"done": ["report"], "again": ["write"]},
        },
        {"id": "report", "tool": "classify"},
    ]

    @pytest.fixture
    def markdown(self, workspace: Path) -> str:
        make.write_tool(workspace, "classify")
        paths = Paths(workspace, env={}, home=workspace / "home")
        return render({"flow": "draft", "start": "write"}, self.LOOP, paths)

    def test_the_edge_back_is_drawn_and_says_it_is_a_loop(self, markdown: str) -> None:
        assert '-.->|"again (loop)"|' in markdown
        assert '-.->|"done"|' in markdown

    def test_the_loops_section_gives_the_bound_and_what_runs_again(self, markdown: str) -> None:
        assert "## Loops" in markdown
        assert "`check` may send its result back to `write` 4 times" in markdown
        assert "`check`, `write` again" in markdown

    def test_the_branch_list_sends_the_reader_there(self, markdown: str) -> None:
        assert "`again` → `write` (goes back, see Loops)" in markdown

    def test_the_waves_are_counted_with_the_loop_opened(self, markdown: str) -> None:
        """Counted, `write` would wait on a step downstream of it and nothing would settle."""
        assert "| 1 | `write` |" in markdown
        assert "| 3 | `report` |" in markdown

    def test_nothing_around_a_loop_is_marked_skippable(self, markdown: str) -> None:
        """The flow either leaves the loop or runs out of passes and fails."""
        assert " skippable;" not in markdown
