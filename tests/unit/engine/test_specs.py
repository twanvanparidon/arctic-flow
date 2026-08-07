"""`engine/specs.py`: is this component one the engine can actually run?

The rule the module states for itself is "check what the runtime reads", and every failure
here is one that would otherwise have surfaced mid-run, after earlier steps had already
spent time and money. So these tests are mostly about a spec that loads perfectly well as
JSON and still cannot be executed: a script that was never committed, one that lost its
executable bit in a copy, an `input_schema` with a typo in the word `object`.

The specs are read back off disk after being written, so what is checked is what a
component directory actually contains.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from engine.specs import (
    SpecError,
    _check_is_schema,
    _templated,
    check_agent_spec,
    check_gate_input,
    check_step_input,
    check_tool_spec,
)
from support import components as make

WHERE = "./tools/sample/spec.json"


def written(workspace: Path, **kwargs: Any) -> tuple[dict[str, Any], Path]:
    """Write a tool, then read its spec back, so the two cannot drift apart."""
    base = make.write_tool(workspace, "sample", **kwargs)
    return json.loads((base / "spec.json").read_text()), base


class TestCheckToolSpec:
    def test_a_complete_spec_passes(self, workspace: Path) -> None:
        spec, base = written(workspace)
        assert check_tool_spec(spec, base, WHERE) is None

    @pytest.mark.parametrize("field", ["name", "description", "run", "input_schema", "permissions"])
    def test_a_missing_required_field_is_reported(self, workspace: Path, field: str) -> None:
        full = make.tool_spec("sample")
        del full[field]
        spec, base = written(workspace, spec=full)
        with pytest.raises(SpecError, match=f"'{field}' is a required property"):
            check_tool_spec(spec, base, WHERE)

    def test_the_message_says_where_the_spec_is(self, workspace: Path) -> None:
        spec, base = written(workspace, spec={"name": "sample"})
        with pytest.raises(SpecError, match=r"\./tools/sample/spec\.json"):
            check_tool_spec(spec, base, WHERE)

    @pytest.mark.parametrize("declared", ["", 3, None])
    def test_the_name_must_be_a_non_empty_string(self, workspace: Path, declared: object) -> None:
        spec, base = written(workspace, spec={**make.tool_spec("sample"), "name": declared})
        with pytest.raises(SpecError, match="name:"):
            check_tool_spec(spec, base, WHERE)

    @pytest.mark.parametrize("command", [[], "run.sh", [1]])
    def test_the_command_must_be_a_non_empty_argv_list(
        self, workspace: Path, command: object
    ) -> None:
        spec, base = written(workspace, run={"command": command})
        with pytest.raises(SpecError, match="run/command"):
            check_tool_spec(spec, base, WHERE)

    @pytest.mark.parametrize("timeout", [0, -1])
    def test_a_timeout_must_be_positive(self, workspace: Path, timeout: int) -> None:
        spec, base = written(workspace, run={"command": ["./run.sh"], "timeout_seconds": timeout})
        with pytest.raises(SpecError, match="run/timeout_seconds"):
            check_tool_spec(spec, base, WHERE)

    def test_an_exit_code_key_has_to_read_as_a_number(self, workspace: Path) -> None:
        """JSON object keys are strings, so the code is spelled "3" and not 3."""
        spec, base = written(workspace, exit_codes={"oops": "not a code"})
        with pytest.raises(SpecError, match="exit_codes"):
            check_tool_spec(spec, base, WHERE)

    def test_an_exit_code_description_has_to_be_text(self, workspace: Path) -> None:
        spec, base = written(workspace, exit_codes={"3": 3})
        with pytest.raises(SpecError, match="exit_codes/3"):
            check_tool_spec(spec, base, WHERE)

    @pytest.mark.parametrize("field", ["input_schema", "output_schema"])
    def test_a_declared_schema_has_to_be_a_schema(self, workspace: Path, field: str) -> None:
        """`"type": "objekt"` is caught by nothing at run time until a payload arrives."""
        spec, base = written(workspace, **{field: {"type": "objekt"}})
        with pytest.raises(SpecError, match=f"{field} is not a valid JSON Schema"):
            check_tool_spec(spec, base, WHERE)

    def test_a_command_that_was_never_committed_is_reported(self, workspace: Path) -> None:
        spec, base = written(workspace)
        (base / "run.sh").unlink()
        with pytest.raises(SpecError, match=r"run\.command points at \./run\.sh"):
            check_tool_spec(spec, base, WHERE)

    def test_a_command_that_lost_its_executable_bit_is_reported(self, workspace: Path) -> None:
        """The most common way a tool works here and fails on someone else's machine."""
        spec, base = written(workspace, executable=False)
        with pytest.raises(SpecError, match="is not executable"):
            check_tool_spec(spec, base, WHERE)

    def test_the_command_is_resolved_against_the_components_own_directory(
        self, workspace: Path
    ) -> None:
        spec, base = written(workspace, run={"command": ["./bin/run.sh"]})
        assert check_tool_spec(spec, base, WHERE) is None
        assert (base / "bin" / "run.sh").is_file()

    def test_optional_fields_may_be_left_out_entirely(self, workspace: Path) -> None:
        minimal = {
            "name": "sample",
            "description": "the least a tool can say",
            "run": {"command": ["./run.sh"]},
            "input_schema": {"type": "object"},
            "permissions": {"filesystem": "none"},
        }
        spec, base = written(workspace, spec=minimal)
        assert check_tool_spec(spec, base, WHERE) is None

    @pytest.mark.parametrize("reach", ["rw", "readwrite", "all", "Write", ""])
    def test_a_reach_the_engine_does_not_know_is_refused(self, workspace: Path, reach: str) -> None:
        """Every one of these would read as "not write" and silently open the gate."""
        spec, base = written(workspace, permissions={"filesystem": reach})
        with pytest.raises(SpecError, match="permissions/filesystem"):
            check_tool_spec(spec, base, WHERE)

    @pytest.mark.parametrize("reach", ["none", "read", "write"])
    def test_the_three_reaches_a_tool_may_declare(self, workspace: Path, reach: str) -> None:
        spec, base = written(workspace, permissions={"filesystem": reach})
        assert check_tool_spec(spec, base, WHERE) is None

    def test_the_secrets_a_tool_expects_are_named_as_text(self, workspace: Path) -> None:
        spec, base = written(workspace, secrets=["signing_key"])
        assert check_tool_spec(spec, base, WHERE) is None


class TestCheckIsSchema:
    @pytest.mark.parametrize("candidate", [{}, {"type": "object"}, True, False])
    def test_accepts_what_the_meta_schema_accepts(self, candidate: object) -> None:
        """A boolean is a legal JSON Schema: `true` accepts everything, `false` nothing."""
        assert _check_is_schema(candidate, "subject") is None

    @pytest.mark.parametrize("candidate", ["object", 3, None, [], {"type": "objekt"}])
    def test_rejects_anything_else(self, candidate: object) -> None:
        with pytest.raises(SpecError, match="not a valid JSON Schema"):
            _check_is_schema(candidate, "subject")


class TestCheckAgentSpec:
    def test_a_complete_spec_passes(self) -> None:
        assert check_agent_spec(make.agent_spec("writer"), WHERE) is None

    @pytest.mark.parametrize("field", ["name", "description", "adapter"])
    def test_a_missing_required_field_is_reported(self, field: str) -> None:
        spec = make.agent_spec("writer")
        del spec[field]
        with pytest.raises(SpecError, match=f"'{field}' is a required property"):
            check_agent_spec(spec, WHERE)

    def test_an_adapter_that_is_not_registered_is_reported(self) -> None:
        with pytest.raises(SpecError, match="unknown adapter 'imaginary'"):
            check_agent_spec(make.agent_spec("writer", adapter="imaginary"), WHERE)

    def test_an_agent_with_tools_is_probed_with_a_placeholder_server(self) -> None:
        """The real command is not knowable from a spec. What is being asked is whether
        the adapter accepts a turn that has tools at all."""
        assert check_agent_spec(make.agent_spec("writer", tools=["reader"]), WHERE) is None

    def test_unattended_is_the_engines_own_and_never_reaches_an_adapter(self) -> None:
        """A closed adapter schema would reject it, which is what adding it to FORWARDED
        would cause. It is enforced in validate(), where the tools are resolved."""
        spec = make.agent_spec("writer", adapter="claude_code", model="sonnet", unattended=True)
        assert check_agent_spec(spec, WHERE) is None

    def test_a_claude_code_agent_has_to_name_its_model(self) -> None:
        """The CLI's own default is the per-machine dependency `isolate` exists to remove,
        so lint refuses a spec that would inherit it rather than a turn discovering it."""
        spec = make.agent_spec("writer", adapter="claude_code")
        with pytest.raises(SpecError, match="'model' is a required property"):
            check_agent_spec(spec, WHERE)

    def test_kind_may_only_say_agent(self) -> None:
        with pytest.raises(SpecError, match="kind:"):
            check_agent_spec(make.agent_spec("writer", kind="tool"), WHERE)

    def test_a_budget_has_to_be_worth_setting(self) -> None:
        with pytest.raises(SpecError, match="max_budget_usd"):
            check_agent_spec(make.agent_spec("writer", max_budget_usd=0), WHERE)

    def test_an_output_schema_has_to_be_a_schema(self) -> None:
        spec = make.agent_spec("writer", output_schema={"type": "objekt"})
        with pytest.raises(SpecError, match="output_schema"):
            check_agent_spec(spec, WHERE)

    def test_a_setting_the_adapter_would_reject_is_caught_here_instead(self) -> None:
        """Asked of the adapter's own schema, so adding a parameter needs no change here."""
        spec = make.agent_spec("writer", effort="colossal")
        with pytest.raises(SpecError, match="rejected by adapter echo"):
            check_agent_spec(spec, WHERE)

    def test_a_setting_the_adapter_accepts_passes(self) -> None:
        spec = make.agent_spec("writer", model="sonnet", effort="high", max_budget_usd=0.25)
        assert check_agent_spec(spec, WHERE) is None

    def test_an_output_schema_is_probed_under_the_adapters_name_for_it(self) -> None:
        """The probe is the payload the engine would build, so it is `json_schema` by then."""
        spec = make.agent_spec("writer", output_schema={"type": "object"})
        assert check_agent_spec(spec, WHERE) is None

    def test_tools_must_be_a_list_of_names(self) -> None:
        with pytest.raises(SpecError, match="tools"):
            check_agent_spec(make.agent_spec("writer", tools="read_file"), WHERE)


class TestCheckStepInput:
    STRICT = {
        "name": "strict",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "max_lines": {"type": "integer"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    }

    def test_an_input_the_tool_accepts_passes(self) -> None:
        step = {"id": "a", "input": {"text": "hello"}}
        assert check_step_input(step, self.STRICT, WHERE) is None

    def test_a_key_the_tool_does_not_accept_is_reported_with_what_it_does(self) -> None:
        step = {"id": "a", "input": {"text": "hello", "txt": "typo"}}
        with pytest.raises(SpecError, match="passes txt to strict"):
            check_step_input(step, self.STRICT, WHERE)

    def test_two_unknown_keys_read_as_plural(self) -> None:
        step = {"id": "a", "input": {"text": "hi", "one": 1, "two": 2}}
        with pytest.raises(SpecError, match="does not accept them"):
            check_step_input(step, self.STRICT, WHERE)

    def test_a_tool_accepting_anything_is_not_second_guessed(self) -> None:
        """No `additionalProperties: false`, so the tool has not said what is unknown."""
        spec = {"name": "open", "input_schema": {"type": "object"}}
        assert check_step_input({"id": "a", "input": {"anything": 1}}, spec, WHERE) is None

    def test_a_required_key_no_template_would_have_filled_is_reported(self) -> None:
        with pytest.raises(SpecError, match="does not pass text"):
            check_step_input({"id": "a", "input": {}}, self.STRICT, WHERE)

    def test_a_step_passing_nothing_at_all_is_still_checked(self) -> None:
        with pytest.raises(SpecError, match="does not pass text"):
            check_step_input({"id": "a"}, self.STRICT, WHERE)

    def test_a_literal_of_the_wrong_type_is_wrong_today(self) -> None:
        step = {"id": "a", "input": {"text": "hi", "max_lines": "many"}}
        with pytest.raises(SpecError, match="invalid max_lines"):
            check_step_input(step, self.STRICT, WHERE)

    def test_a_templated_value_is_taken_on_trust(self) -> None:
        """A lint that guesses what a template renders to is a lint people switch off."""
        step = {"id": "a", "input": {"text": "hi", "max_lines": "{{ inputs.depth }}"}}
        assert check_step_input(step, self.STRICT, WHERE) is None

    def test_a_templated_value_still_counts_as_supplied(self) -> None:
        step = {"id": "a", "input": {"text": "{{ steps.read.text }}"}}
        assert check_step_input(step, self.STRICT, WHERE) is None

    def test_a_tool_accepting_nothing_says_so(self) -> None:
        spec = {
            "name": "bare",
            "input_schema": {"type": "object", "additionalProperties": False},
        }
        with pytest.raises(SpecError, match="allows nothing"):
            check_step_input({"id": "a", "input": {"x": 1}}, spec, WHERE)

    def test_a_gate_input_is_reported_as_the_gates(self) -> None:
        """Otherwise the message points at the step's own input, which is a different thing."""
        step = {"id": "a", "gate": {"tool": "strict", "input": {}}}
        with pytest.raises(SpecError, match="gate does not pass text"):
            check_gate_input(step, self.STRICT, WHERE)

    def test_a_gate_that_passes_nothing_is_still_checked(self) -> None:
        step = {"id": "a", "gate": {"tool": "strict"}}
        with pytest.raises(SpecError, match="gate does not pass text"):
            check_gate_input(step, self.STRICT, WHERE)


class TestTemplated:
    @pytest.mark.parametrize("value", ["{{ inputs.a }}", "prefix {{ a }} suffix", "{{"])
    def test_a_string_carrying_braces_is_decided_at_run_time(self, value: str) -> None:
        assert _templated(value) is True

    @pytest.mark.parametrize("value", ["plain", "{ a }", "", 3, None, True, ["{{ a }}"]])
    def test_anything_else_is_written_in_the_flow(self, value: object) -> None:
        assert _templated(value) is False
