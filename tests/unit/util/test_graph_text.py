"""`atf inspect flow`: the push edges as text.

It renders what `validate()` already accepted, so there is nothing to check here and every
test is about what the text says. The one worth keeping is `(terminal)`: "this step ends
the flow" and "I forgot to draw the rest" must not look the same.
"""

from __future__ import annotations

from typing import Any

from util.graph import render


def flow(*steps: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return {"flow": "demo", "start": steps[0]["id"]}, list(steps)


class TestRender:
    def test_the_first_line_is_where_the_flow_starts(self) -> None:
        assert render(*flow({"id": "a", "tool": "t"})).splitlines()[0] == "demo: start -> a"

    def test_a_tool_step_names_its_tool(self) -> None:
        assert "  a  (tool:read_file)" in render(*flow({"id": "a", "tool": "read_file"}))

    def test_an_agent_step_names_its_agent(self) -> None:
        step = {"id": "a", "agent": "summarizer", "prompt": "p"}
        assert "  a  (agent:summarizer)" in render(*flow(step))

    def test_a_push_is_drawn_as_one_arrow_per_target(self) -> None:
        text = render(*flow({"id": "a", "tool": "t", "push": ["b", "c"]}, {"id": "b", "tool": "t"}))
        assert "    -> b" in text
        assert "    -> c" in text

    def test_a_step_with_nowhere_to_go_says_it_ends_the_flow(self) -> None:
        assert "    (terminal)" in render(*flow({"id": "a", "tool": "t"}))

    def test_a_switch_shows_its_expression_and_every_case(self) -> None:
        step = {
            "id": "a",
            "tool": "t",
            "switch": "{{ this.text }}",
            "cases": {"pass": ["b"], "fail": ["c"]},
        }
        text = render(*flow(step))
        assert "    switch {{ this.text }}" in text
        assert "      pass       -> b" in text
        assert "      fail       -> c" in text

    def test_a_default_branch_is_drawn_like_a_case(self) -> None:
        step = {"id": "a", "tool": "t", "switch": "x", "cases": {"pass": ["b"]}, "default": ["c"]}
        assert "      default    -> c" in render(*flow(step))

    def test_a_branch_that_ends_the_flow_says_so(self) -> None:
        """Distinct from a branch nobody filled in, which validate would have refused."""
        step = {"id": "a", "tool": "t", "switch": "x", "cases": {"stop": None}}
        assert "      stop       -> (ends)" in render(*flow(step))

    def test_a_long_case_value_is_not_truncated(self) -> None:
        step = {"id": "a", "tool": "t", "switch": "x", "cases": {"needs_more_review": ["b"]}}
        assert "      needs_more_review -> b" in render(*flow(step))

    LOOP = (
        {"id": "write", "tool": "t", "push": ["check"]},
        {
            "id": "check",
            "tool": "t",
            "switch": "{{ this.text }}",
            "max_loops": 4,
            "cases": {"done": ["report"], "again": ["write"]},
        },
        {"id": "report", "tool": "t"},
    )

    def test_a_case_going_back_upstream_is_marked_as_a_loop(self) -> None:
        """Which case loops cannot be read off the YAML: it depends on where the target
        already sits. Unmarked, a loop reads as an ordinary edge."""
        lines = render(*flow(*self.LOOP)).splitlines()
        again = next(line for line in lines if "again" in line)
        assert "write" in again
        assert "loops back, max 4" in again

    def test_a_case_going_forward_is_not(self) -> None:
        lines = render(*flow(*self.LOOP)).splitlines()
        assert "loops back" not in next(line for line in lines if "done" in line)

    def test_a_default_that_goes_back_is_marked_too(self) -> None:
        steps = list(self.LOOP)
        steps[1] = {**steps[1], "cases": {"done": ["report"]}, "default": ["write"]}
        lines = render(*flow(*steps)).splitlines()
        assert "loops back, max 4" in next(line for line in lines if "default" in line)

    def test_each_step_is_separated_by_a_blank_line(self) -> None:
        text = render(*flow({"id": "a", "tool": "t", "push": ["b"]}, {"id": "b", "tool": "t"}))
        assert "\n\n  b  (tool:t)" in text

    def test_a_push_to_nowhere_draws_no_edges(self) -> None:
        """`push: []` is a step that deliberately hands on to nothing, and prints as itself."""
        text = render(*flow({"id": "a", "tool": "t", "push": []}))
        assert "(terminal)" not in text
        assert text.splitlines()[-1] == "  a  (tool:t)"
