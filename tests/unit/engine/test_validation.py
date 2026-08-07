"""`validate`: everything checkable without running a step.

The engine's position is that it would rather refuse a flow than run half of one, so this
function says no to a great many things. Each of those refusals is a promise: a green
`atf lint` means the flow will not fail on its own definitions. A missing check is a
promise quietly withdrawn, which is why the shape of this file is one test per refusal.

The components are real directories. The last block of `validate` loads every tool and
agent a flow names and holds it to its contract, and that is not observable against
components that do not exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from engine.executor import FlowError, check_gate_shape, validate
from paths.resolver import Paths
from support import components as make


@pytest.fixture
def project(workspace: Path, paths: Paths) -> Paths:
    """A workspace holding one permissive tool and one agent, so any flow below resolves."""
    make.write_tool(workspace, "noop")
    make.write_agent(workspace, "writer")
    return paths


def flow(*steps: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    built: dict[str, Any] = {"flow": "demo", "start": steps[0]["id"], "steps": list(steps)}
    built.update(overrides)
    return built


def tool_step(sid: str, **extra: Any) -> dict[str, Any]:
    return {"id": sid, "tool": "noop", **extra}


def agent_step(sid: str, **extra: Any) -> dict[str, Any]:
    return {"id": sid, "agent": "writer", "prompt": "say something", **extra}


class TestTheFlowItself:
    @pytest.mark.parametrize("field", ["flow", "start", "steps"])
    def test_a_missing_top_level_field_is_named(self, project: Paths, field: str) -> None:
        definition = flow(tool_step("a"))
        del definition[field]
        with pytest.raises(FlowError, match=f"missing required field '{field}'"):
            validate(definition, project)

    @pytest.mark.parametrize("steps", [[], {}, "a", None])
    def test_steps_must_be_a_non_empty_list(self, project: Paths, steps: object) -> None:
        with pytest.raises(FlowError, match="'steps' must be a non-empty list"):
            validate({"flow": "d", "start": "a", "steps": steps}, project)

    def test_start_must_name_a_step(self, project: Paths) -> None:
        with pytest.raises(FlowError, match=r"start references.*'elsewhere'"):
            validate(flow(tool_step("a"), start="elsewhere"), project)

    def test_a_valid_flow_returns_its_steps(self, project: Paths) -> None:
        definition = flow(tool_step("a"))
        assert validate(definition, project) == definition["steps"]


class TestStepShape:
    def test_every_step_needs_an_id(self, project: Paths) -> None:
        with pytest.raises(FlowError, match="every step needs an 'id'"):
            validate(flow(tool_step("a"), {"tool": "noop"}), project)

    def test_ids_are_unique(self, project: Paths) -> None:
        with pytest.raises(FlowError, match="duplicate step id 'a'"):
            validate(flow(tool_step("a", push=["a"]), tool_step("a")), project)

    def test_a_step_runs_a_tool_or_an_agent_and_not_both(self, project: Paths) -> None:
        step = {"id": "a", "tool": "noop", "agent": "writer", "prompt": "x"}
        with pytest.raises(FlowError, match="exactly one of 'tool' or 'agent'"):
            validate(flow(step), project)

    def test_a_step_that_runs_neither_is_refused(self, project: Paths) -> None:
        with pytest.raises(FlowError, match="exactly one of 'tool' or 'agent'"):
            validate(flow({"id": "a"}), project)

    @pytest.mark.parametrize("prompt", [None, "", []])
    def test_an_agent_step_needs_a_prompt(self, project: Paths, prompt: object) -> None:
        step = {"id": "a", "agent": "writer", "prompt": prompt}
        with pytest.raises(FlowError, match="needs a 'prompt'"):
            validate(flow(step), project)

    def test_a_step_cannot_both_push_and_switch(self, project: Paths) -> None:
        step = tool_step("a", push=["b"], switch="{{ this.text }}", cases={"x": ["b"]})
        with pytest.raises(FlowError, match="sets both 'push' and 'switch'"):
            validate(flow(step, tool_step("b")), project)


class TestDeclaredSecrets:
    @pytest.mark.parametrize("declared", ["token", {"token": 1}, [1], ["ok", 2]])
    def test_secrets_must_be_a_list_of_names(self, project: Paths, declared: object) -> None:
        with pytest.raises(FlowError, match="secrets must be a list"):
            validate(flow(tool_step("a", secrets=declared)), project)

    def test_a_repeated_secret_is_refused(self, project: Paths) -> None:
        with pytest.raises(FlowError, match="lists a secret more than once"):
            validate(flow(tool_step("a", secrets=["token", "token"])), project)

    def test_an_empty_list_is_accepted(self, project: Paths) -> None:
        """`secrets: []` says the step takes none, which is a thing worth being able to say."""
        assert validate(flow(tool_step("a", secrets=[])), project)


class TestSwitchShape:
    @pytest.mark.parametrize("key", ["cases", "default"])
    def test_branch_keys_without_a_switch_are_refused(self, project: Paths, key: str) -> None:
        step = tool_step("a", **{key: {"x": ["b"]} if key == "cases" else ["b"]})
        with pytest.raises(FlowError, match="no 'switch'"):
            validate(flow(step, tool_step("b")), project)

    @pytest.mark.parametrize("expression", ["", "   ", 3, None])
    def test_the_switch_expression_must_be_a_non_empty_string(
        self, project: Paths, expression: object
    ) -> None:
        step = tool_step("a", switch=expression, cases={"x": ["b"]})
        with pytest.raises(FlowError, match="needs a 'switch'"):
            validate(flow(step, tool_step("b")), project)

    @pytest.mark.parametrize("cases", [None, {}, [], "x"])
    def test_a_switch_needs_cases(self, project: Paths, cases: object) -> None:
        step = tool_step("a", switch="{{ this.text }}", cases=cases)
        with pytest.raises(FlowError, match="no 'cases'"):
            validate(flow(step, tool_step("b")), project)

    def test_a_case_key_that_is_not_a_string_says_why(self, project: Paths) -> None:
        """YAML 1.1 reads bare `yes` as True, which could never match a rendered string."""
        step = tool_step("a", switch="{{ this.text }}", cases={True: ["b"]})
        with pytest.raises(FlowError, match="case key True is not a string"):
            validate(flow(step, tool_step("b")), project)

    def test_a_case_branch_must_be_a_list(self, project: Paths) -> None:
        step = tool_step("a", switch="{{ this.text }}", cases={"x": "b"})
        with pytest.raises(FlowError, match="case 'x' must be a list"):
            validate(flow(step, tool_step("b")), project)

    def test_a_branch_may_be_empty(self, project: Paths) -> None:
        """A case that ends the flow is a real answer, distinct from a missing one."""
        step = tool_step("a", switch="{{ this.text }}", cases={"stop": None, "go": ["b"]})
        assert validate(flow(step, tool_step("b")), project)

    def test_default_must_be_a_list(self, project: Paths) -> None:
        step = tool_step("a", switch="{{ this.text }}", cases={"x": ["b"]}, default="b")
        with pytest.raises(FlowError, match="default must be a list"):
            validate(flow(step, tool_step("b")), project)


class TestEdges:
    def test_a_push_to_a_step_that_does_not_exist_is_refused(self, project: Paths) -> None:
        with pytest.raises(FlowError, match="pushes to unknown step 'ghost'"):
            validate(flow(tool_step("a", push=["ghost"])), project)

    def test_a_switch_branch_to_a_step_that_does_not_exist_is_refused(self, project: Paths) -> None:
        step = tool_step("a", switch="{{ this.text }}", cases={"x": ["ghost"]})
        with pytest.raises(FlowError, match="pushes to unknown step 'ghost'"):
            validate(flow(step), project)

    def test_a_step_cannot_push_to_itself(self, project: Paths) -> None:
        with pytest.raises(FlowError, match="step 'a' pushes to itself"):
            validate(flow(tool_step("a", push=["a"])), project)

    def test_a_step_nothing_reaches_is_refused(self, project: Paths) -> None:
        with pytest.raises(FlowError, match="step 'orphan' is unreachable"):
            validate(flow(tool_step("a"), tool_step("orphan")), project)

    def test_a_cycle_is_refused(self, project: Paths) -> None:
        definition = flow(
            tool_step("a", push=["b"]),
            tool_step("b", push=["c"]),
            tool_step("c", push=["b"]),
        )
        with pytest.raises(FlowError, match="steps form a cycle: b, c"):
            validate(definition, project)

    def test_a_diamond_is_not_a_cycle(self, project: Paths) -> None:
        definition = flow(
            tool_step("a", push=["left", "right"]),
            tool_step("left", push=["join"]),
            tool_step("right", push=["join"]),
            tool_step("join"),
        )
        assert validate(definition, project)


class TestTemplateReferences:
    def test_an_unknown_namespace_is_refused(self, project: Paths) -> None:
        step = tool_step("a", input={"x": "{{ nonsense.value }}"})
        with pytest.raises(FlowError, match="unknown namespace 'nonsense'"):
            validate(flow(step), project)

    def test_an_undeclared_input_is_refused(self, project: Paths) -> None:
        step = tool_step("a", input={"x": "{{ inputs.path }}"})
        with pytest.raises(FlowError, match="undeclared input 'path'"):
            validate(flow(step), project)

    def test_a_declared_input_is_accepted(self, project: Paths) -> None:
        step = tool_step("a", input={"x": "{{ inputs.path }}"})
        definition = flow(step, inputs={"path": {"required": True}})
        assert validate(definition, project)

    def test_reading_from_a_step_that_does_not_exist_is_refused(self, project: Paths) -> None:
        step = tool_step("a", input={"x": "{{ steps.ghost.text }}"})
        with pytest.raises(FlowError, match="unknown step 'ghost'"):
            validate(flow(step), project)

    def test_reading_from_a_step_that_is_not_upstream_is_refused(self, project: Paths) -> None:
        """`sibling` may not have run when `reader` does, so the value may not exist."""
        definition = flow(
            tool_step("a", push=["sibling", "reader"]),
            tool_step("sibling"),
            tool_step("reader", input={"x": "{{ steps.sibling.text }}"}),
        )
        with pytest.raises(FlowError, match=r"'sibling'.*not upstream"):
            validate(definition, project)

    def test_reading_from_a_transitive_ancestor_is_accepted(self, project: Paths) -> None:
        definition = flow(
            tool_step("a", push=["b"]),
            tool_step("b", push=["c"]),
            tool_step("c", input={"x": "{{ steps.a.text }}"}),
        )
        assert validate(definition, project)

    def test_this_is_refused_outside_a_switch_or_a_gate(self, project: Paths) -> None:
        step = tool_step("a", input={"x": "{{ this.text }}"})
        with pytest.raises(FlowError, match=r"\{\{ this\.\* \}\}"):
            validate(flow(step), project)

    def test_this_is_accepted_in_a_switch(self, project: Paths) -> None:
        step = tool_step("a", switch="{{ this.json.verdict }}", cases={"ok": ["b"]})
        assert validate(flow(step, tool_step("b")), project)

    def test_gate_is_refused_outside_the_gate_feedback(self, project: Paths) -> None:
        step = agent_step("a", prompt="{{ gate.text }}")
        with pytest.raises(FlowError, match=r"\{\{ gate\.\* \}\}"):
            validate(flow(step), project)


class TestSecretReferences:
    def test_a_tool_may_template_a_secret_it_declared(self, project: Paths) -> None:
        step = tool_step("a", secrets=["token"], input={"x": "{{ secrets.token }}"})
        assert validate(flow(step), project)

    def test_a_tool_may_not_template_a_secret_it_did_not_declare(self, project: Paths) -> None:
        step = tool_step("a", input={"x": "{{ secrets.token }}"})
        with pytest.raises(FlowError, match="without declaring it"):
            validate(flow(step), project)

    def test_a_secret_declared_by_another_step_is_still_not_readable(self, project: Paths) -> None:
        definition = flow(
            tool_step("a", secrets=["token"], push=["b"]),
            tool_step("b", input={"x": "{{ secrets.token }}"}),
        )
        with pytest.raises(FlowError, match=r"step 'b'.*without declaring"):
            validate(definition, project)

    def test_a_secret_in_an_agent_prompt_is_refused_outright(self, project: Paths) -> None:
        """It would be sent to the model and stay in the session. Declaring it changes nothing."""
        step = agent_step("a", secrets=["token"], prompt="sign with {{ secrets.token }}")
        with pytest.raises(FlowError, match="secrets.token"):
            validate(flow(step), project)

    def test_a_secret_in_gate_feedback_is_refused(self, project: Paths) -> None:
        """Feedback becomes the next prompt, so the same rule applies to it."""
        step = agent_step(
            "a",
            secrets=["token"],
            gate={"tool": "noop", "feedback": "use {{ secrets.token }}"},
        )
        with pytest.raises(FlowError, match="secrets.token"):
            validate(flow(step), project)

    def test_a_secret_in_a_gate_input_is_accepted_when_declared(self, project: Paths) -> None:
        """A gate's input is a tool's input, and a tool reads secrets from its environment."""
        step = agent_step(
            "a",
            secrets=["token"],
            gate={"tool": "noop", "feedback": "again", "input": {"k": "{{ secrets.token }}"}},
        )
        assert validate(flow(step), project)


class TestOutput:
    def test_output_must_be_a_mapping(self, project: Paths) -> None:
        """`output: "{{ steps.a.text }}"` is the natural typo, and used to be a traceback."""
        with pytest.raises(FlowError, match="'output' must be a mapping"):
            validate(flow(tool_step("a"), output="{{ steps.a.text }}"), project)

    def test_a_mapping_without_a_template_is_accepted(self, project: Paths) -> None:
        assert validate(flow(tool_step("a"), output={}), project)

    def test_the_template_may_read_any_step(self, project: Paths) -> None:
        """Unlike a step, the output runs after everything, so nothing is out of reach."""
        definition = flow(
            tool_step("a", push=["b"]),
            tool_step("b"),
            output={"template": "{{ steps.a.text }} {{ steps.b.text }}"},
        )
        assert validate(definition, project)

    def test_the_template_may_not_read_an_unknown_step(self, project: Paths) -> None:
        definition = flow(tool_step("a"), output={"template": "{{ steps.ghost.text }}"})
        with pytest.raises(FlowError, match=r"output references.*'ghost'"):
            validate(definition, project)

    def test_the_template_may_not_read_an_undeclared_input(self, project: Paths) -> None:
        definition = flow(tool_step("a"), output={"template": "{{ inputs.path }}"})
        with pytest.raises(FlowError, match=r"output references.*'path'"):
            validate(definition, project)

    def test_the_template_may_not_read_a_secret(self, project: Paths) -> None:
        definition = flow(tool_step("a"), output={"template": "{{ secrets.token }}"})
        with pytest.raises(FlowError, match=r"output references.*'secrets'"):
            validate(definition, project)


class TestGateShape:
    def test_a_gate_on_a_tool_step_is_refused(self, project: Paths) -> None:
        """A tool given the same input returns the same result, so the retry cannot converge."""
        step = tool_step("a", gate={"tool": "noop", "feedback": "again"})
        with pytest.raises(FlowError, match=r"step 'a' has a gate"):
            validate(flow(step), project)

    @pytest.mark.parametrize("gate", ["noop", ["noop"], 3])
    def test_a_gate_must_be_a_mapping(self, project: Paths, gate: object) -> None:
        with pytest.raises(FlowError, match="gate must be a mapping"):
            check_gate_shape("a", {"gate": gate})

    @pytest.mark.parametrize("field", ["tool", "feedback"])
    def test_a_gate_needs_a_tool_and_a_feedback(self, field: str) -> None:
        gate = {"tool": "noop", "feedback": "again"}
        del gate[field]
        with pytest.raises(FlowError, match=f"gate needs a '{field}'"):
            check_gate_shape("a", {"gate": gate})

    @pytest.mark.parametrize("value", ["", "   ", None, 3, []])
    def test_a_blank_gate_field_counts_as_missing(self, value: object) -> None:
        with pytest.raises(FlowError, match="gate needs a 'feedback'"):
            check_gate_shape("a", {"gate": {"tool": "noop", "feedback": value}})

    @pytest.mark.parametrize("attempts", [1, 0, -1])
    def test_fewer_than_two_attempts_leaves_no_turn_to_act_on_the_feedback(
        self, attempts: int
    ) -> None:
        gate = {"tool": "noop", "feedback": "again", "max_attempts": attempts}
        with pytest.raises(FlowError, match="max_attempts"):
            check_gate_shape("a", {"gate": gate})

    def test_a_yaml_boolean_is_not_an_attempt_count(self) -> None:
        """YAML 1.1 reads `max_attempts: yes` as True, and a bool is an int, so it passed as 1."""
        gate = {"tool": "noop", "feedback": "again", "max_attempts": True}
        with pytest.raises(FlowError, match="max_attempts"):
            check_gate_shape("a", {"gate": gate})

    @pytest.mark.parametrize("attempts", ["3", 3.0, None])
    def test_an_attempt_count_that_is_not_an_integer_is_refused(self, attempts: object) -> None:
        gate = {"tool": "noop", "feedback": "again", "max_attempts": attempts}
        with pytest.raises(FlowError, match="max_attempts"):
            check_gate_shape("a", {"gate": gate})

    def test_omitting_max_attempts_is_fine(self) -> None:
        assert check_gate_shape("a", {"gate": {"tool": "noop", "feedback": "again"}}) is None

    def test_a_complete_gate_validates(self, project: Paths) -> None:
        step = agent_step(
            "a", gate={"tool": "noop", "feedback": "again, shorter", "max_attempts": 2}
        )
        assert validate(flow(step), project)


class TestTheComponentsAFlowNames:
    def test_an_unknown_tool_is_refused(self, project: Paths) -> None:
        with pytest.raises(FlowError, match="unknown tool 'absent'"):
            validate(flow({"id": "a", "tool": "absent"}), project)

    def test_an_unknown_agent_is_refused(self, project: Paths) -> None:
        with pytest.raises(FlowError, match="unknown agent 'absent'"):
            validate(flow({"id": "a", "agent": "absent", "prompt": "x"}), project)

    def test_a_tool_whose_script_is_not_executable_is_refused_before_the_run(
        self, project: Paths, workspace: Path
    ) -> None:
        """The most common way a tool fails on someone else's machine, caught by lint."""
        make.make_unexecutable(workspace / "tools" / "noop" / "run.sh")
        with pytest.raises(FlowError, match="is not executable"):
            validate(flow(tool_step("a")), project)

    def test_a_step_passing_something_the_tool_does_not_accept_is_refused(
        self, project: Paths, workspace: Path
    ) -> None:
        make.write_tool(
            workspace,
            "strict",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            },
        )
        step = {"id": "a", "tool": "strict", "input": {"txt": "typo"}}
        with pytest.raises(FlowError, match="passes txt to strict"):
            validate(flow(step), project)

    def test_an_agent_whose_prompt_file_is_missing_is_refused(
        self, project: Paths, workspace: Path
    ) -> None:
        (workspace / "agents" / "writer" / "agent.md").unlink()
        with pytest.raises(FlowError, match="which is missing"):
            validate(flow(agent_step("a")), project)

    def test_an_agent_setting_its_adapter_rejects_is_refused(
        self, project: Paths, workspace: Path
    ) -> None:
        make.write_agent(workspace, "eager", effort="maximum")
        step = {"id": "a", "agent": "eager", "prompt": "x"}
        with pytest.raises(FlowError, match="rejected by adapter echo"):
            validate(flow(step), project)

    def test_the_gates_tool_is_held_to_the_tool_contract(
        self, project: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "checker", executable=False)
        step = agent_step("a", gate={"tool": "checker", "feedback": "again"})
        with pytest.raises(FlowError, match="checker.*is not executable"):
            validate(flow(step), project)

    def test_a_gate_input_the_tool_does_not_accept_is_refused(
        self, project: Paths, workspace: Path
    ) -> None:
        make.write_tool(
            workspace,
            "checker",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        )
        step = agent_step("a", gate={"tool": "checker", "feedback": "again", "input": {}})
        with pytest.raises(FlowError, match="gate does not pass text"):
            validate(flow(step), project)


class TestAnAgentsToolGrant:
    def test_a_grant_that_resolves_and_validates_is_accepted(
        self, project: Paths, workspace: Path
    ) -> None:
        make.write_agent(workspace, "handy", tools=["noop"])
        assert validate(flow({"id": "a", "agent": "handy", "prompt": "x"}), project)

    def test_naming_a_tool_that_does_not_exist_is_reported_first(
        self, project: Paths, workspace: Path
    ) -> None:
        """Of the two problems, the misspelling is the more confusing one to be told second."""
        make.write_agent(workspace, "handy", tools=["ghost"])
        step = {"id": "a", "agent": "handy", "prompt": "x"}
        with pytest.raises(FlowError, match="unknown tool 'ghost'"):
            validate(flow(step), project)

    def test_a_granted_tool_is_held_to_the_tool_contract(
        self, project: Paths, workspace: Path
    ) -> None:
        """Otherwise a broken grant is discovered by a model saying the tool does not work."""
        base = make.write_tool(workspace, "broken")
        make.make_unexecutable(base / "run.sh")
        make.write_agent(workspace, "handy", tools=["broken"])
        step = {"id": "a", "agent": "handy", "prompt": "x"}
        with pytest.raises(FlowError, match="is not executable"):
            validate(flow(step), project)

    def test_granting_a_tool_that_writes_needs_saying_so(
        self, project: Paths, workspace: Path
    ) -> None:
        """Nothing approves a call an agent makes for itself, so the grant says it out loud."""
        make.write_tool(workspace, "scribe", permissions={"filesystem": "write"})
        make.write_agent(workspace, "handy", tools=["scribe"])
        step = {"id": "a", "agent": "handy", "prompt": "x"}
        with pytest.raises(FlowError, match="unattended"):
            validate(flow(step), project)

    def test_saying_it_is_unattended_allows_the_grant(
        self, project: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "scribe", permissions={"filesystem": "write"})
        make.write_agent(workspace, "handy", tools=["scribe"], unattended=True)
        assert validate(flow({"id": "a", "agent": "handy", "prompt": "x"}), project)

    def test_a_read_only_tool_needs_no_declaration(self, project: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "reader", permissions={"filesystem": "read"})
        make.write_agent(workspace, "handy", tools=["reader"])
        assert validate(flow({"id": "a", "agent": "handy", "prompt": "x"}), project)

    def test_two_write_tools_read_as_plural(self, project: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "scribe", permissions={"filesystem": "write"})
        make.write_tool(workspace, "editor", permissions={"filesystem": "write"})
        make.write_agent(workspace, "handy", tools=["scribe", "editor"])
        step = {"id": "a", "agent": "handy", "prompt": "x"}
        with pytest.raises(FlowError, match="editor, scribe, which change the workspace"):
            validate(flow(step), project)

    def test_granting_a_tool_that_needs_a_secret_is_refused(
        self, project: Paths, workspace: Path
    ) -> None:
        """Nothing scopes a secret to one in-turn call, so the tool would find none."""
        make.write_tool(workspace, "signer", secrets=["signing_key"])
        make.write_agent(workspace, "handy", tools=["signer"])
        step = {"id": "a", "agent": "handy", "prompt": "x"}
        with pytest.raises(FlowError, match="expects a secret in its environment"):
            validate(flow(step), project)

    def test_a_step_cannot_grant_secrets_to_a_turn_that_has_tools(
        self, project: Paths, workspace: Path
    ) -> None:
        """The adapter is given the step's secrets, so every in-turn tool would inherit them."""
        make.write_agent(workspace, "handy", tools=["noop"])
        step = {"id": "a", "agent": "handy", "prompt": "x", "secrets": ["token"]}
        with pytest.raises(FlowError, match="cannot be combined"):
            validate(flow(step), project)

    def test_a_namespaced_tool_can_be_granted(self, project: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "common/reader", permissions={"filesystem": "read"})
        make.write_agent(workspace, "handy", tools=["common/reader"])
        assert validate(flow({"id": "a", "agent": "handy", "prompt": "x"}), project)

    def test_two_namespaces_holding_the_same_leaf_can_both_be_granted(
        self, project: Paths, workspace: Path
    ) -> None:
        """The namespace is kept in the name a model sees, so these are two tools."""
        make.write_tool(workspace, "common/reader")
        make.write_tool(workspace, "legacy/reader")
        make.write_agent(workspace, "handy", tools=["common/reader", "legacy/reader"])
        assert validate(flow({"id": "a", "agent": "handy", "prompt": "x"}), project)

    def test_two_grants_a_model_would_see_as_one_tool_are_refused(
        self, project: Paths, workspace: Path
    ) -> None:
        """An in-turn call arrives by a name with no separator in it, so `git/commit` and
        `git__commit` are one name for two tools and the server cannot tell them apart."""
        make.write_tool(workspace, "git/commit")
        make.write_tool(workspace, "git__commit")
        make.write_agent(workspace, "handy", tools=["git/commit", "git__commit"])
        step = {"id": "a", "agent": "handy", "prompt": "x"}
        with pytest.raises(FlowError, match="one tool called 'git__commit'"):
            validate(flow(step), project)
