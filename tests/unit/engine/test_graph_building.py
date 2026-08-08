"""Deriving the graph from what the steps declare.

A flow only ever says where a step pushes. Everything the engine needs to schedule (who
waits on whom, what is upstream of what) comes out of these three functions, so a mistake
here is a step that runs too early or a join that never runs at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.executor import (
    START,
    FlowError,
    ancestors_of,
    back_edges,
    build_graph,
    descendants_of,
    load_flow,
    loop_body,
    outbound_targets,
    without_back_edges,
)


class TestOutboundTargets:
    def test_a_push_lists_its_targets(self) -> None:
        assert outbound_targets({"id": "a", "push": ["b", "c"]}) == ["b", "c"]

    @pytest.mark.parametrize("push", [[], None])
    def test_a_push_that_goes_nowhere_is_a_valid_ending(self, push: list[str] | None) -> None:
        assert outbound_targets({"id": "a", "push": push}) == []

    def test_a_switch_offers_every_branch(self) -> None:
        """Every target the step *could* reach, which is what the reverse edges need."""
        step = {"id": "a", "switch": "{{ this.text }}", "cases": {"yes": ["b"], "no": ["c"]}}
        assert outbound_targets(step) == ["b", "c"]

    def test_a_switch_includes_its_default(self) -> None:
        step = {"id": "a", "switch": "x", "cases": {"yes": ["b"]}, "default": ["d"]}
        assert outbound_targets(step) == ["b", "d"]

    def test_a_target_named_by_two_branches_appears_once(self) -> None:
        """Both branches reach the join, but there is only one edge into it."""
        step = {"id": "a", "switch": "x", "cases": {"yes": ["j"], "no": ["j"]}, "default": ["j"]}
        assert outbound_targets(step) == ["j"]

    def test_an_empty_branch_contributes_nothing(self) -> None:
        step = {"id": "a", "switch": "x", "cases": {"yes": ["b"], "stop": None}}
        assert outbound_targets(step) == ["b"]

    def test_a_terminal_step_has_no_targets(self) -> None:
        assert outbound_targets({"id": "a", "tool": "t"}) == []


class TestBuildGraph:
    STEPS = [
        {"id": "read", "push": ["left", "right"]},
        {"id": "left", "push": ["join"]},
        {"id": "right", "push": ["join"]},
        {"id": "join", "tool": "t"},
    ]

    def test_every_step_gets_an_outbound_entry(self) -> None:
        outbound, _ = build_graph(self.STEPS)
        assert outbound == {
            "read": ["left", "right"],
            "left": ["join"],
            "right": ["join"],
            "join": [],
        }

    def test_the_reverse_edges_are_derived(self) -> None:
        _, inbound = build_graph(self.STEPS)
        assert inbound["left"] == {"read"}
        assert inbound["join"] == {"left", "right"}

    def test_a_step_nothing_pushes_to_has_no_inbound_edges(self) -> None:
        _, inbound = build_graph(self.STEPS)
        assert inbound.get("read") is None

    def test_an_edge_to_a_step_that_does_not_exist_is_still_recorded(self) -> None:
        """build_graph derives; validate() is what rejects. Keeping them apart means the
        error message can name both ends of the bad edge."""
        _, inbound = build_graph([{"id": "a", "push": ["nowhere"]}])
        assert inbound["nowhere"] == {"a"}


class TestAncestorsOf:
    def test_a_step_with_nothing_upstream_has_no_ancestors(self) -> None:
        _, inbound = build_graph([{"id": "a", "push": ["b"]}, {"id": "b", "tool": "t"}])
        assert ancestors_of("a", inbound) == set()

    def test_upstream_is_transitive(self) -> None:
        steps = [
            {"id": "a", "push": ["b"]},
            {"id": "b", "push": ["c"]},
            {"id": "c", "tool": "t"},
        ]
        _, inbound = build_graph(steps)
        assert ancestors_of("c", inbound) == {"a", "b"}

    def test_a_join_sees_both_branches(self) -> None:
        steps = [
            {"id": "a", "switch": "x", "cases": {"l": ["left"], "r": ["right"]}},
            {"id": "left", "push": ["join"]},
            {"id": "right", "push": ["join"]},
            {"id": "join", "tool": "t"},
        ]
        _, inbound = build_graph(steps)
        assert ancestors_of("join", inbound) == {"a", "left", "right"}

    def test_the_virtual_start_is_not_an_ancestor(self) -> None:
        """`__start__` is the source of the first push, not a step a template can read."""
        _, inbound = build_graph([{"id": "a", "tool": "t"}])
        inbound["a"].add(START)
        assert ancestors_of("a", inbound) == set()

    def test_a_cycle_terminates(self) -> None:
        """validate() rejects cycles, but this walk must not be what discovers that."""
        _, inbound = build_graph([{"id": "a", "push": ["b"]}, {"id": "b", "push": ["a"]}])
        assert ancestors_of("a", inbound) == {"a", "b"}


class TestDescendantsOf:
    def test_downstream_is_transitive(self) -> None:
        outbound, _ = build_graph(
            [{"id": "a", "push": ["b"]}, {"id": "b", "push": ["c"]}, {"id": "c", "tool": "t"}]
        )
        assert descendants_of("a", outbound) == {"b", "c"}

    def test_a_terminal_step_has_no_descendants(self) -> None:
        outbound, _ = build_graph([{"id": "a", "push": ["b"]}, {"id": "b", "tool": "t"}])
        assert descendants_of("b", outbound) == set()

    def test_a_cycle_terminates(self) -> None:
        outbound, _ = build_graph([{"id": "a", "push": ["b"]}, {"id": "b", "push": ["a"]}])
        assert descendants_of("a", outbound) == {"a", "b"}


class TestBackEdges:
    def test_a_flow_that_only_goes_forward_has_none(self) -> None:
        outbound, _ = build_graph([{"id": "a", "push": ["b"]}, {"id": "b", "tool": "t"}])
        assert back_edges(outbound, "a") == set()

    def test_a_diamond_is_not_a_cycle(self) -> None:
        outbound, _ = build_graph(
            [
                {"id": "a", "push": ["left", "right"]},
                {"id": "left", "push": ["join"]},
                {"id": "right", "push": ["join"]},
                {"id": "join", "tool": "t"},
            ]
        )
        assert back_edges(outbound, "a") == set()

    def test_the_edge_that_closes_a_cycle_is_the_one_found(self) -> None:
        outbound, _ = build_graph(
            [{"id": "a", "push": ["b"]}, {"id": "b", "push": ["c"]}, {"id": "c", "push": ["a"]}]
        )
        assert back_edges(outbound, "a") == {("c", "a")}

    def test_two_separate_loops_are_both_found(self) -> None:
        outbound, _ = build_graph(
            [
                {"id": "a", "push": ["b"]},
                {"id": "b", "switch": "x", "cases": {"again": ["a"], "on": ["c"]}},
                {"id": "c", "push": ["d"]},
                {"id": "d", "switch": "x", "cases": {"again": ["c"], "done": []}},
            ]
        )
        assert back_edges(outbound, "a") == {("b", "a"), ("d", "c")}

    def test_a_cycle_the_start_never_reaches_is_not_found(self) -> None:
        """Nothing enters it, so the walk never gets there. validate()'s own cycle check is
        what is left to catch it, which is the one case that check still fires on."""
        outbound, _ = build_graph(
            [{"id": "a", "tool": "t"}, {"id": "x", "push": ["y"]}, {"id": "y", "push": ["x"]}]
        )
        assert back_edges(outbound, "a") == set()

    def test_declaration_order_decides_which_edge_closes_a_cycle(self) -> None:
        """Reaching `d` through `b` makes the edge out of `d` the one going back; reaching
        it through `c` first makes the edge out of `b` the one. Both open the same cycle, so
        either is a correct answer, and the walk follows declaration order so that the
        answer is the same one every time it is asked."""

        def cycle(first: str, second: str) -> set[tuple[str, str]]:
            outbound, _ = build_graph(
                [
                    {"id": "a", "push": [first, second]},
                    {"id": "b", "push": ["d"]},
                    {"id": "c", "push": ["d"]},
                    {"id": "d", "push": ["b"]},
                ]
            )
            return back_edges(outbound, "a")

        assert cycle("b", "c") == {("d", "b")}
        assert cycle("c", "b") == {("b", "d")}


class TestWithoutBackEdges:
    STEPS = [
        {"id": "a", "push": ["b"]},
        {"id": "b", "switch": "x", "cases": {"again": ["a"], "on": ["c"]}},
        {"id": "c", "tool": "t"},
    ]

    def test_the_loop_is_gone_from_both_directions(self) -> None:
        outbound, _ = build_graph(self.STEPS)
        forward, reverse = without_back_edges(outbound, back_edges(outbound, "a"))
        assert forward == {"a": ["b"], "b": ["c"], "c": []}
        assert reverse.get("a") is None
        assert reverse["c"] == {"b"}

    def test_a_graph_with_no_loop_is_unchanged(self) -> None:
        outbound, inbound = build_graph([{"id": "a", "push": ["b"]}, {"id": "b", "tool": "t"}])
        forward, reverse = without_back_edges(outbound, set())
        assert forward == outbound
        assert reverse == inbound


class TestLoopBody:
    @staticmethod
    def body(steps: list[dict[str, str | list[str]]], source: str, head: str) -> set[str]:
        outbound, _ = build_graph(steps)  # type: ignore[arg-type]
        forward, reverse = without_back_edges(outbound, back_edges(outbound, "a"))
        return loop_body(source, head, forward, reverse)

    STEPS = [
        {"id": "a", "push": ["b"]},
        {"id": "b", "switch": "x", "cases": {"again": ["a"], "on": ["c"]}},
        {"id": "c", "tool": "t"},
    ]

    def test_it_is_the_steps_between_the_two_ends(self) -> None:
        assert self.body(self.STEPS, "b", "a") == {"a", "b"}

    def test_what_comes_after_the_loop_is_not_in_it(self) -> None:
        assert "c" not in self.body(self.STEPS, "b", "a")

    def test_a_join_inside_the_loop_is_in_it(self) -> None:
        steps = [
            {"id": "a", "push": ["left", "right"]},
            {"id": "left", "push": ["b"]},
            {"id": "right", "push": ["b"]},
            {"id": "b", "switch": "x", "cases": {"again": ["a"], "on": ["c"]}},
            {"id": "c", "tool": "t"},
        ]
        assert self.body(steps, "b", "a") == {"a", "left", "right", "b"}


class TestLoadFlow:
    def test_reads_a_yaml_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "f.yaml"
        path.write_text("flow: demo\nstart: a\n")
        assert load_flow(path) == {"flow": "demo", "start": "a"}

    @pytest.mark.parametrize("text", ["- a\n- b\n", "just a scalar\n", "", "# only a comment\n"])
    def test_anything_that_is_not_a_mapping_is_refused(self, tmp_path: Path, text: str) -> None:
        path = tmp_path / "f.yaml"
        path.write_text(text)
        with pytest.raises(FlowError, match="YAML mapping"):
            load_flow(path)

    def test_the_message_names_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong.yaml"
        path.write_text("[]")
        with pytest.raises(FlowError, match=str(path)):
            load_flow(path)
