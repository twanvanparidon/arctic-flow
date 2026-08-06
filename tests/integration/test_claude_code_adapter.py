"""The adapter against a real process.

The unit suite tests `build_args` and `_failure_detail` and stops there, because `run()`
needs the CLI. Here it gets one: `tests/support/fake_claude.py`, on `PATH` as `claude`,
speaking the protocol of `--print --output-format json` without calling a model.

So everything between the payload and the envelope is real. The argv is really built and
really parsed by another program, the prompt really travels over a pipe, and the four ways
a turn can fail are four real exit codes. That last part is the point of the file: each of
those branches exists because the CLI behaved that way once, and none of them is reachable
from a unit test.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from adapters import claude_code
from adapters.errors import AdapterProtocolError, AdapterRunFailed, AdapterUnavailable
from engine.executor import child_environment


def turn(prompt: str, **payload: Any) -> dict[str, Any]:
    return claude_code.run({"prompt": prompt, **payload}, child_environment())


def invocation(**payload: Any) -> dict[str, Any]:
    """Ask the fake to describe how it was invoked, and read that off its answer."""
    return json.loads(turn("!invocation", **payload)["text"])


class TestASuccessfulTurn:
    def test_the_answer_comes_back_as_text(self) -> None:
        assert turn("say this back")["text"] == "say this back"

    def test_the_envelope_is_normalised(self) -> None:
        """Nothing downstream reads a runtime's own field names, which is what makes a
        second adapter a new module and no change anywhere else."""
        result = turn("hello")
        assert result["ok"] is True
        assert result["session_id"] == "fake-session"
        assert result["cost_usd"] == 0.01
        assert result["usage"]["input_tokens"] == 10
        assert result["num_turns"] == 1

    def test_the_runtimes_version_is_recorded_on_the_envelope(self) -> None:
        assert turn("hello")["adapter"] == {"name": "claude_code", "cli_version": "2.1.222"}

    def test_the_model_that_was_asked_for_is_recorded(self) -> None:
        assert turn("hello", model="sonnet")["requested_model"] == "sonnet"


class TestWhatCrossesTheProcessBoundary:
    def test_the_prompt_travels_on_stdin(self) -> None:
        """Clear of ARG_MAX, and clear of --tools, which would swallow a trailing positional."""
        described = invocation()
        assert described["prompt"] == "!invocation"
        assert "!invocation" not in described["switches"]

    def test_a_prompt_far_longer_than_a_command_line_survives(self) -> None:
        long_prompt = "word " * 100_000
        assert turn(long_prompt)["text"] == long_prompt

    def test_the_turn_is_isolated_by_default(self) -> None:
        """--safe-mode, so the host's CLAUDE.md, skills, hooks and MCP servers stay out."""
        assert invocation()["isolated"] is True

    def test_isolation_can_be_turned_off_deliberately(self) -> None:
        assert invocation(isolate=False)["isolated"] is False

    def test_no_tools_are_offered(self) -> None:
        """Empty rather than absent: "" is the CLI's disable-all, so a turn is a completion."""
        assert invocation()["tools"] == ""

    def test_the_settings_an_agent_declares_arrive_as_flags(self) -> None:
        described = invocation(model="sonnet", effort="high", system="be terse")
        assert (described["model"], described["effort"]) == ("sonnet", "high")
        assert described["system"] == "be terse"

    def test_the_environment_the_engine_prepared_is_the_one_the_process_gets(self) -> None:
        """How a credential reaches a runtime, since it may not go through the prompt."""
        env = child_environment({"ATF_PROBE_token": "s3cret"})
        described = json.loads(claude_code.run({"prompt": "!invocation"}, env)["text"])
        assert described["env"] == {"ATF_PROBE_token": "s3cret"}

    def test_a_response_schema_is_honoured(self) -> None:
        schema = {
            "type": "object",
            "properties": {"verdict": {"enum": ["risky", "clean"]}},
            "required": ["verdict"],
        }
        assert json.loads(turn("classify this", json_schema=schema)["text"]) == {"verdict": "risky"}


class TestTheFourWaysATurnFails:
    def test_a_refusal_reported_in_the_result_object(self) -> None:
        with pytest.raises(AdapterRunFailed, match="overloaded"):
            turn("!fail overloaded")

    def test_a_process_that_died_without_saying_anything_useful(self) -> None:
        """No result to read, so the exit code becomes the whole story."""
        with pytest.raises(AdapterRunFailed, match="exited 2") as caught:
            turn("!crash out of memory")
        assert "out of memory" in str(caught.value)

    def test_an_answer_this_adapter_does_not_recognise(self) -> None:
        """Distinct from a failed turn: the request may have succeeded, but the reply
        cannot be trusted, usually because the output format changed underneath us."""
        with pytest.raises(AdapterProtocolError, match="expected a JSON result object"):
            turn("!garbage")

    def test_a_result_claiming_success_from_a_process_that_failed(self) -> None:
        """Contradictory, so the exit code is believed rather than the claim."""
        with pytest.raises(AdapterRunFailed, match="claims success"):
            turn("!contradiction")

    def test_a_runtime_that_is_not_installed(self, unset_path: None) -> None:
        with pytest.raises(AdapterUnavailable, match="not on PATH"):
            turn("hello")


class TestVersionProbe:
    def test_it_reads_the_installed_version(self) -> None:
        assert claude_code.cli_version() == "2.1.222"

    def test_the_flags_were_verified_against_the_version_the_module_names(self) -> None:
        """A reminder in the form of a test: move VERIFIED_CLI_VERSION when you add a flag."""
        assert claude_code.VERIFIED_CLI_VERSION == claude_code.cli_version()
