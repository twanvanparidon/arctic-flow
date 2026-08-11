"""The echo adapter: what it answers, and what it reports having been asked.

`run()` is here rather than in the integration suite, unlike `claude_code`'s, because there
is no runtime to pay for. That is the whole point of the module.

The engine reads this envelope, so the fields worth pinning are the ones something
downstream acts on: `cost_usd`, which `RunResult` sums; `text`, which templates read; and
`requested_model`, which is the only trace of what was asked for.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from adapters import echo
from adapters.errors import AdapterRunFailed
from engine import specs

VERDICT = {
    "type": "object",
    "properties": {"verdict": {"enum": ["risky", "clean"]}, "note": {"type": "string"}},
    "required": ["verdict"],
}


def answer(prompt: str, **payload: Any) -> str:
    return echo.run({"prompt": prompt, **payload}, {})["text"]


class TestTheAnswer:
    def test_a_plain_prompt_comes_back_as_it_went_in(self) -> None:
        """What makes a loop observable: a pass reads the last one out of `steps`, so a
        prompt that guards on it really does produce different text."""
        assert answer("write it") == "write it"

    def test_a_directive_is_only_read_from_the_first_line(self) -> None:
        prompt = "write it\n!fail not a directive down here"
        assert answer(prompt) == prompt

    def test_a_word_that_merely_starts_with_a_bang_is_not_a_directive(self) -> None:
        assert answer("!failsafe means something else") == "!failsafe means something else"


class TestFail:
    def test_it_arrives_as_a_runtime_refusal(self) -> None:
        with pytest.raises(AdapterRunFailed, match="the runtime refused"):
            answer("!fail the runtime refused")

    def test_a_bare_directive_still_says_something(self) -> None:
        with pytest.raises(AdapterRunFailed, match="refused"):
            answer("!fail")


class TestJson:
    def test_the_rest_of_the_line_is_the_answer(self) -> None:
        assert answer('!json {"verdict": "risky"}') == '{"verdict": "risky"}'

    def test_it_beats_a_schema(self) -> None:
        """A switch has to be pointed at a case, and the smallest instance is only ever one."""
        assert answer('!json {"verdict": "clean"}', json_schema=VERDICT) == '{"verdict": "clean"}'

    def test_something_that_is_not_json_is_refused_here_rather_than_downstream(self) -> None:
        """Otherwise it surfaces as a template failing on `this.json`, three steps later."""
        with pytest.raises(AdapterRunFailed, match="wants one line of JSON"):
            answer("!json not json at all")


class TestInvocation:
    def test_it_reports_what_the_engine_sent(self) -> None:
        sent = json.loads(answer("!invocation", system="be terse", model="sonnet"))
        assert sent["payload"]["system"] == "be terse"
        assert sent["payload"]["model"] == "sonnet"

    def test_the_environment_it_reports_is_only_the_probe_prefix(self) -> None:
        """Unfiltered, a report of an invocation would print the caller's environment to
        stdout."""
        granted = {"ATF_PROBE_token": "abc", "HOME": "/home/someone"}
        reported = json.loads(echo.run({"prompt": "!invocation"}, granted)["text"])
        assert reported["env"] == {"ATF_PROBE_token": "abc"}


class TestASchemaWithoutADirective:
    def test_the_answer_satisfies_it_instead_of_repeating_the_prompt(self) -> None:
        """So a flow written for a real runtime dry-runs without being edited."""
        assert json.loads(answer("summarise this", json_schema=VERDICT)) == {"verdict": "risky"}

    def test_an_empty_schema_leaves_the_prompt_alone(self) -> None:
        assert answer("summarise this", json_schema={}) == "summarise this"


class TestInstanceFor:
    @pytest.mark.parametrize(
        ("schema", "expected"),
        [
            ({"enum": ["risky", "clean"]}, "risky"),
            ({"type": "string"}, "text"),
            ({"type": "integer"}, 0),
            ({"type": "number"}, 0),
            ({"type": "boolean"}, False),
            ({"type": "array"}, []),
            ({}, "text"),
        ],
    )
    def test_the_smallest_value_of_each_kind(self, schema: dict[str, Any], expected: Any) -> None:
        assert echo.instance_for(schema) == expected

    def test_an_object_carries_only_what_it_requires(self) -> None:
        assert echo.instance_for(VERDICT) == {"verdict": "risky"}

    def test_an_object_with_no_required_list_carries_every_property(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "array"}}}
        assert echo.instance_for(schema) == {"a": "text", "b": []}

    def test_nesting_is_followed(self) -> None:
        schema = {
            "type": "object",
            "properties": {"risks": {"type": "array"}, "verdict": {"enum": ["clean"]}},
            "required": ["risks", "verdict"],
        }
        assert echo.instance_for(schema) == {"risks": [], "verdict": "clean"}


class TestTheEnvelope:
    def test_a_turn_costs_a_flat_rate(self) -> None:
        """Flat, so a flow that looped twice reports twice this and the total is checkable
        against a number rather than a range."""
        assert echo.run({"prompt": "p"}, {})["cost_usd"] == echo.COST_PER_TURN

    def test_the_model_that_was_asked_for_is_echoed_back(self) -> None:
        assert echo.run({"prompt": "p", "model": "sonnet"}, {})["requested_model"] == "sonnet"

    def test_no_model_asked_for_reports_none(self) -> None:
        assert echo.run({"prompt": "p"}, {})["requested_model"] is None

    def test_it_names_itself_as_the_adapter(self) -> None:
        assert echo.run({"prompt": "p"}, {})["adapter"]["name"] == echo.NAME

    def test_usage_counts_both_sides_of_the_turn(self) -> None:
        usage = echo.run({"prompt": "one two three"}, {})["usage"]
        assert usage["input_tokens"] == 3
        assert usage["output_tokens"] == 3

    def test_the_request_does_not_ride_along_in_the_envelope(self) -> None:
        """`!invocation` is how a caller sees what was sent. Widening the contract every
        adapter keeps, so that one of them can be inspected, is not."""
        envelope = echo.run({"prompt": "p"}, {"SECRET": "s"})
        assert "payload" not in envelope
        assert "environment" not in envelope


class TestSchema:
    def test_nothing_the_adapter_does_not_understand_is_accepted(self) -> None:
        assert echo.INPUT_SCHEMA["additionalProperties"] is False

    def test_it_accepts_everything_the_engine_forwards_and_nothing_else(self) -> None:
        """An agent spec written against a real runtime has to dry-run unedited, so the
        schema has to be exactly what the engine can send. Read off the contract rather
        than listed here: a new forwarded field fails at lint instead of at the first turn
        that uses it, and the closed schema means a stale extra fails too."""
        sends = {"prompt", "system", "json_schema", "tool_server", *specs.FORWARDED.values()}
        assert set(echo.INPUT_SCHEMA["properties"]) == sends

    def test_effort_is_the_same_enumeration_a_real_runtime_enforces(self) -> None:
        assert echo.INPUT_SCHEMA["properties"]["effort"]["enum"] == [
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ]
