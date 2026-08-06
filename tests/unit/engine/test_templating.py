"""`render` and `template_refs`: the two smallest pieces of the engine.

Everything a flow says about its own data goes through these. `render` is where a prompt
either carries the previous step's answer or carries a hole, and the module's stated
position is that a hole is an error rather than an empty string.
"""

from __future__ import annotations

import pytest

from engine.executor import FlowError, render, template_refs


class TestRender:
    def test_text_without_a_placeholder_is_returned_unchanged(self) -> None:
        assert render("nothing to do here", {}) == "nothing to do here"

    def test_substitutes_a_dotted_path(self) -> None:
        context = {"steps": {"read": {"text": "file contents"}}}
        assert render("saw: {{ steps.read.text }}", context) == "saw: file contents"

    def test_tolerates_whitespace_inside_the_braces(self) -> None:
        assert render("{{inputs.a}} {{  inputs.a  }}", {"inputs": {"a": "x"}}) == "x x"

    def test_substitutes_every_occurrence(self) -> None:
        assert render("{{ a }} and {{ a }}", {"a": "one"}) == "one and one"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ({"z": 1}, '{\n  "z": 1\n}'),
            ([1, 2], "[\n  1,\n  2\n]"),
            (3, "3"),
            (True, "true"),
            (None, "null"),
        ],
    )
    def test_a_non_string_value_is_inserted_as_indented_json(
        self, value: object, expected: str
    ) -> None:
        """So a template can reference a typed step result without stringifying it first."""
        assert render("{{ a }}", {"a": value}) == expected

    def test_an_empty_string_value_substitutes_to_nothing(self) -> None:
        assert render("[{{ a }}]", {"a": ""}) == "[]"

    def test_an_unknown_namespace_is_an_error(self) -> None:
        with pytest.raises(FlowError, match=r"\{\{ nope.a \}\}"):
            render("{{ nope.a }}", {"inputs": {}})

    def test_an_unknown_leaf_is_an_error(self) -> None:
        with pytest.raises(FlowError, match=r"\{\{ inputs.missing \}\}"):
            render("{{ inputs.missing }}", {"inputs": {"present": "x"}})

    def test_a_path_through_a_non_mapping_is_an_error(self) -> None:
        """`a.b` resolved to a string, so `a.b.c` has nowhere left to go."""
        with pytest.raises(FlowError, match=r"\{\{ a.b.c \}\}"):
            render("{{ a.b.c }}", {"a": {"b": "text"}})

    def test_a_substituted_value_is_not_itself_expanded(self) -> None:
        """One pass. A step result that happens to contain braces is data, not a template."""
        assert render("{{ a }}", {"a": "{{ b }}"}) == "{{ b }}"

    @pytest.mark.parametrize("text", ["{{ a-b }}", "{{}}", "{{ a b }}", "{ a }"])
    def test_something_that_is_not_a_reference_is_left_alone(self, text: str) -> None:
        """The pattern is deliberately narrow, so prose containing braces survives it."""
        assert render(text, {}) == text


class TestTemplateRefs:
    def test_finds_every_reference_in_a_string(self) -> None:
        assert template_refs("{{ a.b }} then {{ c }}") == ["a.b", "c"]

    def test_keeps_duplicates(self) -> None:
        """The caller checks each reference; collapsing them would hide a second use."""
        assert template_refs("{{ a }}{{ a }}") == ["a", "a"]

    def test_walks_into_dict_values(self) -> None:
        assert template_refs({"one": "{{ a }}", "two": "{{ b }}"}) == ["a", "b"]

    def test_walks_into_lists(self) -> None:
        assert template_refs(["{{ a }}", ["{{ b }}"]]) == ["a", "b"]

    def test_walks_into_nested_structures(self) -> None:
        value = {"outer": [{"inner": "{{ steps.x.text }}"}]}
        assert template_refs(value) == ["steps.x.text"]

    def test_ignores_a_reference_used_as_a_mapping_key(self) -> None:
        """Only values are rendered, so only values are searched."""
        assert template_refs({"{{ a }}": "plain"}) == []

    @pytest.mark.parametrize("value", [None, 3, True, 1.5, set()])
    def test_a_scalar_that_cannot_hold_a_reference_yields_nothing(self, value: object) -> None:
        assert template_refs(value) == []
