"""`render`, `parse_template` and `template_refs`: the smallest pieces of the engine.

Everything a flow says about its own data goes through these. `render` is where a prompt
either carries the previous step's answer or carries a hole, and the module's stated
position is that a hole is an error rather than an empty string.

A conditional is the second way to answer that. Rather than templating a placeholder and
explaining it to the model, a prompt guards the section and says what happened instead, so
the two things worth pinning here are that the branch not taken is never rendered and that
a malformed tag is refused rather than sent.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from engine.executor import (
    SKIPPED_RESULT,
    Conditional,
    FlowError,
    parse_template,
    render,
    template_refs,
    truthy,
)


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


class TestTruthy:
    """What a conditional counts as present. The first case is the one it exists for."""

    def test_a_step_that_did_not_run_is_false(self) -> None:
        """Its result is a non-empty mapping, so emptiness alone would call it true."""
        assert truthy(SKIPPED_RESULT) is False

    def test_a_step_that_ran_is_true(self) -> None:
        assert truthy({"text": "", "json": None}) is True

    @pytest.mark.parametrize("value", [None, False, 0, "", [], {}])
    def test_json_emptiness_is_false(self, value: object) -> None:
        assert truthy(value) is False

    @pytest.mark.parametrize("value", ["no", "false", 0.5, [0], {"a": 1}])
    def test_anything_else_is_true(self, value: object) -> None:
        """A string is never parsed, so the *text* "false" is a value like any other."""
        assert truthy(value) is True


class TestConditionals:
    SKIPPED: dict[str, Any] = {"steps": {"scan": dict(SKIPPED_RESULT)}}
    RAN: dict[str, Any] = {"steps": {"scan": {"text": "found things", "json": {"n": 2}}}}

    def test_the_body_is_kept_when_the_path_is_present(self) -> None:
        assert render("{% if steps.scan %}seen{% endif %}", self.RAN) == "seen"

    def test_the_body_is_dropped_when_it_is_not(self) -> None:
        assert render("{% if steps.scan %}seen{% endif %}", self.SKIPPED) == ""

    def test_not_inverts_the_test(self) -> None:
        assert render("{% if not steps.scan %}absent{% endif %}", self.SKIPPED) == "absent"
        assert render("{% if not steps.scan %}absent{% endif %}", self.RAN) == ""

    def test_else_is_the_other_branch(self) -> None:
        template = "{% if steps.scan %}yes{% else %}no{% endif %}"
        assert render(template, self.RAN) == "yes"
        assert render(template, self.SKIPPED) == "no"

    def test_the_branch_not_taken_is_never_rendered(self) -> None:
        """The whole point. `.json.n` is unresolvable until the step has run, so a guard is
        what makes it safe to write at all."""
        template = "{% if steps.scan %}{{ steps.scan.json.n }}{% endif %}"
        assert render(template, self.SKIPPED) == ""
        assert render(template, self.RAN) == "2"

    def test_conditionals_nest(self) -> None:
        template = (
            "{% if steps.scan %}{% if steps.scan.json %}deep{% else %}shallow{% endif %}"
            "{% else %}none{% endif %}"
        )
        assert render(template, self.RAN) == "deep"
        assert render(template, self.SKIPPED) == "none"
        assert render(template, {"steps": {"scan": {"json": None, "text": "x"}}}) == "shallow"

    def test_a_tag_alone_on_its_line_takes_the_line_with_it(self) -> None:
        """Otherwise every conditional leaves a blank line in the prompt it was added to
        tidy up, and a prompt is whitespace-sensitive."""
        template = "before\n{% if steps.scan %}\ninside\n{% endif %}\nafter\n"
        assert render(template, self.RAN) == "before\ninside\nafter\n"
        assert render(template, self.SKIPPED) == "before\nafter\n"

    def test_an_indented_tag_takes_its_indentation_with_it(self) -> None:
        """Which is what a prompt written inside a YAML block scalar looks like."""
        template = "    {% if steps.scan %}\n    inside\n    {% endif %}\n"
        assert render(template, self.RAN) == "    inside\n"

    def test_a_tag_with_text_on_its_line_is_left_where_it_is(self) -> None:
        assert render("a {% if steps.scan %}b{% endif %} c", self.RAN) == "a b c"

    def test_an_unresolvable_condition_is_an_error(self) -> None:
        """Same position as a substitution: a path that does not resolve is a mistake, not a
        quiet false."""
        with pytest.raises(FlowError, match=r"\{% if steps.ghost %\}"):
            render("{% if steps.ghost %}x{% endif %}", self.RAN)

    def test_the_error_names_the_condition_as_it_was_written(self) -> None:
        with pytest.raises(FlowError, match=r"\{% if not steps.ghost %\}"):
            render("{% if not steps.ghost %}x{% endif %}", self.RAN)


class TestParseTemplate:
    """Every malformed tag, because `template_refs` parses too and so `lint` refuses these."""

    def test_a_template_without_tags_is_one_literal(self) -> None:
        assert parse_template("plain {{ a }}") == ("plain {{ a }}",)

    def test_a_conditional_becomes_a_node(self) -> None:
        assert parse_template("{% if a.b %}x{% endif %}") == (
            Conditional(path="a.b", negated=False, then=("x",), otherwise=()),
        )

    @pytest.mark.parametrize(
        ("text", "match"),
        [
            ("{% if a %}x", "no '{% endif %}'"),
            ("{% endif %}", "no '{% if %}'"),
            ("{% else %}x{% endif %}", "no '{% if %}'"),
            ("{% if a %}x{% else %}y{% else %}z{% endif %}", "two '{% else %}'"),
            ("{% iff a %}x{% endif %}", "unknown tag"),
            ("{% if %}x{% endif %}", "unknown tag"),
            ("{% for x in y %}x{% endfor %}", "unknown tag"),
            ("{% if a-b %}x{% endif %}", "unknown tag"),
        ],
    )
    def test_a_malformed_tag_is_refused(self, text: str, match: str) -> None:
        with pytest.raises(FlowError, match=re.escape(match)):
            parse_template(text)

    @pytest.mark.parametrize("text", ["{% if a", "half a tag %} here", "{ % if a %}"])
    def test_half_a_tag_is_refused_rather_than_sent(self, text: str) -> None:
        """A tag opens and closes on one line, so this is text as far as the pattern goes.
        Left alone it would reach the model with the body it was meant to guard below it."""
        with pytest.raises(FlowError, match="not part of a tag"):
            parse_template(text)


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

    def test_a_condition_is_a_reference(self) -> None:
        """So `validate` checks it: a guard on a step that is not upstream is still a read
        of a step that is not upstream."""
        assert template_refs("{% if steps.a %}x{% endif %}") == ["steps.a"]

    def test_both_branches_are_searched(self) -> None:
        """`validate` decides whether a reference is allowed, not which branch will run. A
        guard is not a way to smuggle one past it."""
        template = "{% if steps.a %}{{ steps.b.text }}{% else %}{{ secrets.TOKEN }}{% endif %}"
        assert template_refs(template) == ["steps.a", "steps.b.text", "secrets.TOKEN"]

    def test_a_reference_inside_a_nested_conditional_is_found(self) -> None:
        template = "{% if a %}{% if b %}{{ c.d }}{% endif %}{% endif %}"
        assert template_refs(template) == ["a", "b", "c.d"]
