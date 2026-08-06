"""One agent turn, and the loop that repeats it until a tool accepts the answer.

The adapter here is `support.adapter_echo`: a real adapter that answers with the prompt it
was given. That is what makes the retry loop observable without a model. The engine appends
the gate's feedback to the prompt for the next attempt, so the second turn produces
genuinely different text, and a gate keyed on that text genuinely changes its verdict.

`agent_turn` takes its adapter as an argument, so those tests pass the module straight in.
`run_agent` looks one up by name, so those register it in `ADAPTERS` the way the docs say
an adapter is registered.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from engine.executor import (
    DEFAULT_GATE_ATTEMPTS,
    FlowError,
    GateOutcome,
    agent_turn,
    check_gate,
    execute,
    run_agent,
    run_step,
)
from paths.resolver import Paths
from support import components as make

# Exits 0 only when the text carries the marker, which the feedback is what supplies.
DEMANDS_MARKER = make.python(
    "if 'REVISED' in payload.get('text', ''):\n"
    "    sys.stdout.write('accepted')\n"
    "else:\n"
    "    sys.stderr.write('needs the word REVISED')\n"
    "    sys.exit(1)\n"
)


def agent_step(**extra: Any) -> dict[str, Any]:
    return {"id": "draft", "agent": "writer", "prompt": "write it", **extra}


class TestAgentTurn:
    def test_the_prompt_and_the_system_prompt_are_what_is_sent(
        self, echo_adapter: ModuleType
    ) -> None:
        result = agent_turn(echo_adapter, {"adapter": "echo"}, "be terse", "the question", {})
        assert result["payload"] == {"prompt": "the question", "system": "be terse"}

    @pytest.mark.parametrize(
        ("field", "value"),
        [("model", "sonnet"), ("effort", "low"), ("max_budget_usd", 0.5)],
    )
    def test_a_setting_the_agent_declares_is_forwarded(
        self, echo_adapter: ModuleType, field: str, value: object
    ) -> None:
        agent = {"adapter": "echo", field: value}
        result = agent_turn(echo_adapter, agent, "system", "prompt", {})
        assert result["payload"][field] == value

    def test_a_setting_left_out_is_not_sent_as_null(self, echo_adapter: ModuleType) -> None:
        """The adapter's schema is closed and its defaults are its own to apply."""
        agent = {"adapter": "echo", "model": None, "effort": None}
        result = agent_turn(echo_adapter, agent, "system", "prompt", {})
        assert set(result["payload"]) == {"prompt", "system"}

    def test_output_schema_is_translated_to_the_adapters_own_name_for_it(
        self, echo_adapter: ModuleType
    ) -> None:
        """Agent vocabulary stays runtime-neutral: `output_schema` in, `json_schema` out."""
        agent = {"adapter": "echo", "output_schema": {"type": "object"}}
        result = agent_turn(echo_adapter, agent, "system", "prompt", {})
        assert result["payload"]["json_schema"] == {"type": "object"}
        assert "output_schema" not in result["payload"]

    def test_the_payload_is_checked_against_the_adapters_schema(
        self, echo_adapter: ModuleType
    ) -> None:
        agent = {"adapter": "echo", "effort": "enormous"}
        with pytest.raises(FlowError, match="input rejected by adapter echo"):
            agent_turn(echo_adapter, agent, "system", "prompt", {})

    def test_an_adapter_failure_is_reported_as_a_flow_error(self, echo_adapter: ModuleType) -> None:
        with pytest.raises(FlowError, match="echo: the runtime refused"):
            agent_turn(echo_adapter, {"adapter": "echo"}, "system", "!fail the runtime refused", {})

    def test_the_turns_text_is_also_offered_parsed(self, echo_adapter: ModuleType) -> None:
        result = agent_turn(echo_adapter, {"adapter": "echo"}, "s", '{"verdict": "pass"}', {})
        assert result["json"] == {"verdict": "pass"}

    def test_prose_leaves_the_parsed_view_empty(self, echo_adapter: ModuleType) -> None:
        result = agent_turn(echo_adapter, {"adapter": "echo"}, "s", "just words", {})
        assert result["json"] is None

    def test_the_steps_secrets_reach_the_adapters_environment(
        self, echo_adapter: ModuleType
    ) -> None:
        """Credentials reach a runtime through the environment, never through the prompt."""
        result = agent_turn(echo_adapter, {"adapter": "echo"}, "s", "p", {"API_KEY": "abc"})
        assert result["environment"]["API_KEY"] == "abc"


class TestGateOutcome:
    def test_it_is_frozen(self) -> None:
        outcome = GateOutcome(ok=True, text="fine")
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.ok = False  # type: ignore[misc]

    def test_json_defaults_to_nothing(self) -> None:
        assert GateOutcome(ok=True, text="fine").json is None


class TestCheckGate:
    def test_exit_zero_accepts_and_keeps_the_output(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "checker", script=make.prints('{"words": 40}'))
        outcome = check_gate({"tool": "checker"}, {"text": "answer"}, {}, paths, {})
        assert outcome.ok is True
        assert outcome.text == '{"words": 40}'
        assert outcome.json == {"words": 40}

    def test_a_rejection_is_a_verdict_rather_than_a_failed_run(
        self, paths: Paths, workspace: Path
    ) -> None:
        """What the check printed is all the next attempt has to go on, so it is kept."""
        make.write_tool(workspace, "checker", script=make.fails(1, "over by 12 words"))
        outcome = check_gate({"tool": "checker"}, {"text": "answer"}, {}, paths, {})
        assert outcome.ok is False
        assert outcome.text == "over by 12 words"

    def test_both_streams_are_read(self, paths: Paths, workspace: Path) -> None:
        """A check that prints findings and one that prints a parting line are equally common."""
        make.write_tool(workspace, "checker", script=make.fails(1, "on stderr", stdout="on stdout"))
        outcome = check_gate({"tool": "checker"}, {"text": "a"}, {}, paths, {})
        assert outcome.text == "on stdout\non stderr"

    def test_a_silent_rejection_falls_back_to_the_exit_summary(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(
            workspace, "checker", script=make.fails(7), exit_codes={"7": "unreadable answer"}
        )
        outcome = check_gate({"tool": "checker"}, {"text": "a"}, {}, paths, {})
        assert outcome.text == "checker failed (exit 7: unreadable answer)"

    def test_the_input_may_read_the_result_being_judged(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "checker", script=make.ECHO_STDIN)
        gate = {"tool": "checker", "input": {"text": "{{ this.text }}"}}
        outcome = check_gate(gate, {"text": "the answer"}, {}, paths, {})
        assert outcome.json == {"text": "the answer"}

    def test_the_input_may_read_the_rest_of_the_flow(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "checker", script=make.ECHO_STDIN)
        gate = {"tool": "checker", "input": {"want": "{{ inputs.limit }}"}}
        context = {"inputs": {"limit": "60"}, "steps": {}}
        outcome = check_gate(gate, {"text": "a"}, context, paths, {})
        assert outcome.json == {"want": "60"}

    def test_a_non_string_input_is_passed_through_untouched(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "checker", script=make.ECHO_STDIN)
        gate = {"tool": "checker", "input": {"max_words": 60}}
        outcome = check_gate(gate, {"text": "a"}, {}, paths, {})
        assert outcome.json == {"max_words": 60}

    def test_the_steps_secrets_reach_the_gate(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "checker", script=make.echoes_env("token"))
        outcome = check_gate({"tool": "checker"}, {"text": "a"}, {}, paths, {"token": "s3cret"})
        assert outcome.text == "s3cret"


class TestRunAgentWithoutAGate:
    def test_runs_exactly_once(
        self, paths: Paths, workspace: Path, echo_adapter: ModuleType
    ) -> None:
        make.write_agent(workspace, "writer")
        result = run_agent(agent_step(), {}, paths, {}, lambda _event: None)
        assert result["text"] == "write it"

    def test_reports_no_attempt_count(
        self, paths: Paths, workspace: Path, echo_adapter: ModuleType
    ) -> None:
        """`attempts` is only meaningful where a gate ran; null on every other step is noise."""
        make.write_agent(workspace, "writer")
        assert "attempts" not in run_agent(agent_step(), {}, paths, {}, lambda _event: None)

    def test_the_prompt_is_rendered_against_the_context(
        self, paths: Paths, workspace: Path, echo_adapter: ModuleType
    ) -> None:
        make.write_agent(workspace, "writer")
        step = agent_step(prompt="summarise {{ steps.read.text }}")
        context = {"inputs": {}, "steps": {"read": {"text": "the file"}}}
        assert run_agent(step, context, paths, {}, lambda _e: None)["text"] == "summarise the file"

    def test_an_unknown_adapter_is_a_flow_error(self, paths: Paths, workspace: Path) -> None:
        make.write_agent(workspace, "writer", adapter="nonexistent")
        with pytest.raises(FlowError, match="unknown adapter 'nonexistent'"):
            run_agent(agent_step(), {}, paths, {}, lambda _event: None)


class TestRunAgentWithAGate:
    @pytest.fixture(autouse=True)
    def components(self, workspace: Path, echo_adapter: ModuleType) -> None:
        make.write_agent(workspace, "writer")
        make.write_tool(workspace, "marker", script=DEMANDS_MARKER)

    @staticmethod
    def gate(**extra: Any) -> dict[str, Any]:
        return {
            "tool": "marker",
            "input": {"text": "{{ this.text }}"},
            "feedback": "It said: {{ gate.text }}. You wrote: {{ this.text }}",
            **extra,
        }

    def test_an_accepted_answer_comes_back_on_the_first_attempt(self, paths: Paths) -> None:
        step = agent_step(prompt="already REVISED", gate=self.gate())
        result = run_agent(step, {}, paths, {}, lambda _event: None)
        assert result["attempts"] == 1

    def test_a_rejected_answer_is_retried_with_the_feedback_appended(self, paths: Paths) -> None:
        """Each turn is a fresh session, so the prompt is the only place history can live."""
        step = agent_step(gate=self.gate())
        result = run_agent(step, {}, paths, {}, lambda _event: None)
        assert result["attempts"] == 2
        assert result["text"].startswith("write it\n\nIt said: needs the word REVISED")
        assert "You wrote: write it" in result["text"]

    def test_every_attempt_is_paid_for(self, paths: Paths) -> None:
        """The envelope only carries the last turn's cost, so the total is accumulated here."""
        step = agent_step(gate=self.gate())
        result = run_agent(step, {}, paths, {}, lambda _event: None)
        assert result["cost_usd"] == pytest.approx(0.02)

    def test_the_observer_is_told_about_each_verdict(self, paths: Paths) -> None:
        events: list[dict[str, Any]] = []
        run_agent(agent_step(gate=self.gate()), {}, paths, {}, events.append)
        assert [(e["attempt"], e["ok"]) for e in events] == [(1, False), (2, True)]
        assert events[0]["kind"] == "gated"
        assert events[0]["tool"] == "marker"

    def test_the_attempt_budget_defaults_to_three(self, paths: Paths) -> None:
        events: list[dict[str, Any]] = []
        with pytest.raises(FlowError):
            run_agent(
                agent_step(prompt="never", gate=self.gate(feedback="try again")),
                {},
                paths,
                {},
                events.append,
            )
        assert [event["of"] for event in events] == [DEFAULT_GATE_ATTEMPTS] * 3

    def test_running_out_of_attempts_fails_the_step_with_what_the_gate_said(
        self, paths: Paths
    ) -> None:
        """A gate is not a suggestion."""
        step = agent_step(prompt="never", gate=self.gate(feedback="try again", max_attempts=2))
        with pytest.raises(
            FlowError, match="did not pass gate 'marker' in 2 attempts. needs the word REVISED"
        ):
            run_agent(step, {}, paths, {}, lambda _event: None)

    def test_a_gated_step_reached_through_run_step_behaves_the_same(self, paths: Paths) -> None:
        result = run_step(agent_step(gate=self.gate()), {}, paths)
        assert result["attempts"] == 2

    def test_the_trace_carries_the_attempt_count_only_where_a_gate_ran(
        self, paths: Paths, workspace: Path
    ) -> None:
        """`null` on every step of every other flow would be noise."""
        make.write_tool(workspace, "emit", script=make.prints("go"))
        steps = [
            {"id": "read", "tool": "emit", "push": ["draft"]},
            agent_step(gate=self.gate()),
        ]
        definition = {"flow": "demo", "start": "read", "steps": steps}
        _, trace = execute(definition, steps, {}, paths)
        assert "attempts" not in trace[0]
        assert trace[1]["attempts"] == 2
        assert trace[1]["cost_usd"] == pytest.approx(0.02)
