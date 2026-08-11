"""Running a flow: which step goes next, what a skip does, and what comes back.

Every step here is a tool step, so the whole file runs real processes and no model. That is
not a limitation: skip propagation, joins, switches, the trace and the event stream are all
decided by the executor, and none of them can tell a tool from an agent.

The concurrency test is the one to read twice. Two steps that each wait for the other's
signal only both finish if they genuinely ran at the same time, so the thread pool is
tested by the flow deadlocking if it is not there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from engine.executor import (
    SKIPPED_RESULT,
    FlowError,
    check_inputs,
    chosen_targets,
    execute,
    inputs_from_environment,
    run_flow,
    run_step,
    validate,
    variable_name,
)
from paths.resolver import Paths
from support import components as make
from vault.vault import Vault, VaultError


def flow(*steps: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    built: dict[str, Any] = {"flow": "demo", "start": steps[0]["id"], "steps": list(steps)}
    built.update(overrides)
    return built


class TestCheckInputs:
    DECLARED = {
        "path": {"required": True},
        "depth": {"required": False},
        "note": {},
    }

    def test_a_missing_required_input_is_refused(self) -> None:
        with pytest.raises(FlowError, match="missing required input 'path'"):
            check_inputs({"inputs": self.DECLARED}, {})

    def test_an_optional_input_may_be_left_out(self) -> None:
        assert check_inputs({"inputs": self.DECLARED}, {"path": "a"}) == {"path": "a"}

    def test_an_input_the_flow_never_declared_is_refused(self) -> None:
        with pytest.raises(FlowError, match=r"unknown input 'depht'.*path, depth, note"):
            check_inputs({"inputs": self.DECLARED}, {"path": "a", "depht": 1})

    def test_a_flow_declaring_nothing_says_so(self) -> None:
        with pytest.raises(FlowError, match=r"\(declared: none\)"):
            check_inputs({}, {"path": "a"})

    def test_the_result_is_a_copy_of_what_was_supplied(self) -> None:
        supplied = {"path": "a"}
        checked = check_inputs({"inputs": self.DECLARED}, supplied)
        checked["path"] = "changed"
        assert supplied == {"path": "a"}

    def test_a_missing_input_is_told_which_variable_would_supply_it(self) -> None:
        with pytest.raises(FlowError, match=r"\$ATF_VAR_PATH"):
            check_inputs({"inputs": self.DECLARED}, {})


class TestInputsFromEnvironment:
    DECLARED = {"path": {"required": True}, "depth": {}}

    def test_a_declared_input_reads_its_variable(self) -> None:
        env = {"ATF_VAR_PATH": "notes.md", "ATF_VAR_DEPTH": "2"}
        assert inputs_from_environment({"inputs": self.DECLARED}, env) == {
            "path": "notes.md",
            "depth": "2",
        }

    @pytest.mark.parametrize("name", ["depth", "max_attempts"])
    def test_the_variable_is_the_prefix_and_the_name_upper_cased(self, name: str) -> None:
        assert variable_name(name) == f"ATF_VAR_{name.upper()}"

    def test_a_variable_for_an_input_the_flow_never_declared_is_ignored(self) -> None:
        """A variable is ambient: one exported for another flow must not refuse this one."""
        assert inputs_from_environment({"inputs": self.DECLARED}, {"ATF_VAR_ELSEWHERE": "x"}) == {}

    def test_the_engines_own_variables_are_not_inputs(self) -> None:
        """Why the prefix is not a bare ATF_: $ATF_PATH is the highest-precedence root."""
        env = {"ATF_PATH": "/roots", "ATF_VAULT_PASSWORD": "demo"}
        assert inputs_from_environment({"inputs": self.DECLARED}, env) == {}

    def test_a_flow_declaring_no_inputs_reads_nothing(self) -> None:
        assert inputs_from_environment({}, {"ATF_VAR_PATH": "notes.md"}) == {}

    def test_an_input_with_no_variable_set_is_left_out(self) -> None:
        """Left out rather than empty, so `required` still fails and an optional stays absent."""
        assert inputs_from_environment({"inputs": self.DECLARED}, {}) == {}

    def test_an_empty_variable_is_a_value(self) -> None:
        """`ATF_VAR_PATH= atf run` sets it to the empty string, as `--input path=` does."""
        assert inputs_from_environment({"inputs": self.DECLARED}, {"ATF_VAR_PATH": ""}) == {
            "path": ""
        }


class TestChosenTargets:
    def test_a_push_always_delivers_to_all_of_its_targets(self) -> None:
        step = {"id": "a", "push": ["b", "c"]}
        assert chosen_targets(step, {}, {}, {}) == ["b", "c"]

    def test_a_terminal_step_delivers_nowhere(self) -> None:
        assert chosen_targets({"id": "a"}, {}, {}, {}) == []

    def test_a_switch_selects_the_matching_case(self) -> None:
        step = {"id": "a", "switch": "{{ this.text }}", "cases": {"yes": ["b"], "no": ["c"]}}
        assert chosen_targets(step, {"text": "no"}, {}, {}) == ["c"]

    def test_the_switch_value_is_stripped_before_matching(self) -> None:
        """A tool that ends its output with a newline still matches its own case."""
        step = {"id": "a", "switch": "{{ this.text }}", "cases": {"yes": ["b"]}}
        assert chosen_targets(step, {"text": "yes\n"}, {}, {}) == ["b"]

    def test_a_switch_may_read_a_typed_result(self) -> None:
        step = {"id": "a", "switch": "{{ this.json.verdict }}", "cases": {"pass": ["b"]}}
        assert chosen_targets(step, {"json": {"verdict": "pass"}}, {}, {}) == ["b"]

    def test_a_boolean_in_a_result_renders_as_json_not_as_python(self) -> None:
        """`True` would never match a case written in YAML, which is why it renders as `true`."""
        step = {"id": "a", "switch": "{{ this.json.ok }}", "cases": {"true": ["b"]}}
        assert chosen_targets(step, {"json": {"ok": True}}, {}, {}) == ["b"]

    def test_a_switch_may_read_the_flows_inputs(self) -> None:
        step = {"id": "a", "switch": "{{ inputs.mode }}", "cases": {"fast": ["b"]}}
        assert chosen_targets(step, {}, {"mode": "fast"}, {}) == ["b"]

    def test_an_unmatched_value_falls_to_the_default(self) -> None:
        step = {"id": "a", "switch": "{{ this.text }}", "cases": {"yes": ["b"]}, "default": ["d"]}
        assert chosen_targets(step, {"text": "maybe"}, {}, {}) == ["d"]

    def test_an_unmatched_value_with_no_default_fails_the_step(self) -> None:
        """Silently ending the flow would look like success. This is the loud alternative."""
        step = {"id": "a", "switch": "{{ this.text }}", "cases": {"yes": ["b"]}}
        with pytest.raises(FlowError, match="switched on 'maybe'"):
            chosen_targets(step, {"text": "maybe"}, {}, {})

    def test_a_case_that_ends_the_flow_delivers_nowhere(self) -> None:
        step = {"id": "a", "switch": "{{ this.text }}", "cases": {"stop": None}}
        assert chosen_targets(step, {"text": "stop"}, {}, {}) == []


class TestRunStep:
    def test_a_tool_step_returns_its_stdout_as_text_and_as_json(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "emit", script=make.prints('{"verdict": "pass"}'))
        result = run_step({"id": "a", "tool": "emit"}, {}, paths)
        assert result == {"text": '{"verdict": "pass"}', "json": {"verdict": "pass"}}

    def test_prose_output_leaves_json_empty(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "emit", script=make.prints("just words"))
        assert run_step({"id": "a", "tool": "emit"}, {}, paths)["json"] is None

    def test_string_inputs_are_rendered_against_the_context(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "echo", script=make.ECHO_STDIN)
        step = {"id": "a", "tool": "echo", "input": {"x": "{{ inputs.name }}"}}
        context = {"inputs": {"name": "release-notes.md"}, "steps": {}}
        assert run_step(step, context, paths)["json"] == {"x": "release-notes.md"}

    def test_a_non_string_input_is_passed_through_untouched(
        self, paths: Paths, workspace: Path
    ) -> None:
        """`max_lines: 400` has to arrive as a number, so only strings are rendered."""
        make.write_tool(workspace, "echo", script=make.ECHO_STDIN)
        step = {"id": "a", "tool": "echo", "input": {"max_lines": 400, "flag": True}}
        assert run_step(step, {}, paths)["json"] == {"max_lines": 400, "flag": True}

    def test_a_step_declaring_secrets_without_a_vault_says_so(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "echo")
        step = {"id": "a", "tool": "echo", "secrets": ["token"]}
        with pytest.raises(FlowError, match="no vault is open"):
            run_step(step, {}, paths)

    def test_a_secret_the_vault_does_not_hold_is_refused(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "echo")
        vault = Vault(path=workspace / "secrets.vault", values={"other": "x"})
        step = {"id": "a", "tool": "echo", "secrets": ["token"]}
        with pytest.raises(VaultError, match="has no token"):
            run_step(step, {}, paths, vault)

    def test_a_granted_secret_reaches_the_tools_environment(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "reveal", script=make.echoes_env("token"))
        vault = Vault(path=workspace / "v", values={"token": "s3cret", "other": "no"})
        step = {"id": "a", "tool": "reveal", "secrets": ["token"]}
        assert run_step(step, {}, paths, vault)["text"] == "s3cret"

    def test_a_secret_the_step_did_not_declare_is_absent(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "reveal", script=make.echoes_env("other"))
        vault = Vault(path=workspace / "v", values={"token": "s3cret", "other": "no"})
        step = {"id": "a", "tool": "reveal", "secrets": ["token"]}
        assert run_step(step, {}, paths, vault)["text"] == ""

    def test_a_declared_secret_may_be_templated_into_the_input(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "echo", script=make.ECHO_STDIN)
        vault = Vault(path=workspace / "v", values={"token": "s3cret"})
        step = {
            "id": "a",
            "tool": "echo",
            "secrets": ["token"],
            "input": {"k": "{{ secrets.token }}"},
        }
        assert run_step(step, {}, paths, vault)["json"] == {"k": "s3cret"}


class TestExecute:
    def test_a_linear_flow_runs_both_steps_in_order(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "first", script=make.prints("one"))
        make.write_tool(workspace, "second", script=make.ECHO_STDIN)
        definition = flow(
            {"id": "a", "tool": "first", "push": ["b"]},
            {"id": "b", "tool": "second", "input": {"seen": "{{ steps.a.text }}"}},
        )
        results, trace = execute(definition, definition["steps"], {}, paths)
        assert results["b"]["json"] == {"seen": "one"}
        assert [entry["step"] for entry in trace] == ["a", "b"]

    def test_the_trace_records_what_each_step_did(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "t", script=make.prints("x"))
        definition = flow(
            {"id": "a", "tool": "t", "push": ["b"]},
            {"id": "b", "tool": "t"},
        )
        _, trace = execute(definition, definition["steps"], {}, paths)
        assert trace[0] == {
            "step": "a",
            "ms": trace[0]["ms"],
            "ok": True,
            "pushed_to": ["b"],
            # A tool costs nothing, and the key is present so a consumer need not guess.
            "cost_usd": None,
        }
        assert trace[0]["ms"] >= 0

    def test_the_observer_is_told_when_a_step_starts_and_finishes(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "t", script=make.prints("x"))
        definition = flow({"id": "a", "tool": "t"})
        events: list[dict[str, Any]] = []
        execute(definition, definition["steps"], {}, paths, on_event=events.append)
        assert [event["kind"] for event in events] == ["started", "finished"]
        assert events[0]["component"] == "tool t"

    def test_a_flow_with_no_observer_runs_the_same(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "t", script=make.prints("x"))
        definition = flow({"id": "a", "tool": "t"})
        results, _ = execute(definition, definition["steps"], {}, paths)
        assert results["a"]["text"] == "x"

    def test_two_steps_off_one_push_run_at_the_same_time(
        self, paths: Paths, workspace: Path, tmp_path: Path
    ) -> None:
        """Each waits for the other's signal, so the flow only completes if both are running."""
        make.write_tool(workspace, "start_tool", script=make.prints("go"))
        left = make.rendezvous(tmp_path / "left.flag", tmp_path / "right.flag")
        right = make.rendezvous(tmp_path / "right.flag", tmp_path / "left.flag")
        make.write_tool(workspace, "left", script=left)
        make.write_tool(workspace, "right", script=right)
        definition = flow(
            {"id": "a", "tool": "start_tool", "push": ["left", "right"]},
            {"id": "left", "tool": "left"},
            {"id": "right", "tool": "right"},
        )
        results, _ = execute(definition, definition["steps"], {}, paths)
        assert results["left"]["text"] == results["right"]["text"] == "met"

    def test_the_untaken_branch_is_skipped(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "choose", script=make.prints("left"))
        make.write_tool(workspace, "t", script=make.prints("ran"))
        definition = flow(
            {
                "id": "choice",
                "tool": "choose",
                "switch": "{{ this.text }}",
                "cases": {"left": ["l"], "right": ["r"]},
            },
            {"id": "l", "tool": "t"},
            {"id": "r", "tool": "t"},
        )
        results, _ = execute(definition, definition["steps"], {}, paths)
        assert results["l"]["text"] == "ran"
        assert results["r"] == SKIPPED_RESULT

    def test_skipping_cascades_down_the_untaken_subtree(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "choose", script=make.prints("left"))
        make.write_tool(workspace, "t", script=make.prints("ran"))
        definition = flow(
            {
                "id": "choice",
                "tool": "choose",
                "switch": "{{ this.text }}",
                "cases": {"left": ["l"], "right": ["r"]},
            },
            {"id": "l", "tool": "t"},
            {"id": "r", "tool": "t", "push": ["r2"]},
            {"id": "r2", "tool": "t"},
        )
        events: list[dict[str, Any]] = []
        execute(definition, definition["steps"], {}, paths, on_event=events.append)
        skipped = [event["step"] for event in events if event["kind"] == "skipped"]
        assert skipped == ["r", "r2"]

    def test_a_join_runs_once_one_of_its_branches_is_skipped(
        self, paths: Paths, workspace: Path
    ) -> None:
        """Without skip propagation this waits forever on a branch that will never deliver."""
        make.write_tool(workspace, "choose", script=make.prints("left"))
        make.write_tool(workspace, "t", script=make.prints("ran"))
        make.write_tool(workspace, "echo", script=make.ECHO_STDIN)
        definition = flow(
            {
                "id": "choice",
                "tool": "choose",
                "switch": "{{ this.text }}",
                "cases": {"left": ["l"], "right": ["r"]},
            },
            {"id": "l", "tool": "t", "push": ["join"]},
            {"id": "r", "tool": "t", "push": ["join"]},
            {
                "id": "join",
                "tool": "echo",
                "input": {"left": "{{ steps.l.text }}", "right": "{{ steps.r.text }}"},
            },
        )
        results, _ = execute(definition, definition["steps"], {}, paths)
        # A skipped step still resolves, as the literal "(not run)", so the join can say so.
        assert results["join"]["json"] == {"left": "ran", "right": "(not run)"}

    def test_a_failing_step_fails_the_run_and_names_itself(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "boom", script=make.fails(3, "it broke"))
        definition = flow({"id": "a", "tool": "boom"})
        with pytest.raises(FlowError, match="step 'a': boom failed"):
            execute(definition, definition["steps"], {}, paths)

    def test_a_failure_is_recorded_and_reported_before_it_is_raised(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "boom", script=make.fails(3, "it broke"))
        definition = flow({"id": "a", "tool": "boom"})
        events: list[dict[str, Any]] = []
        with pytest.raises(FlowError):
            execute(definition, definition["steps"], {}, paths, on_event=events.append)
        assert events[-1]["kind"] == "failed"
        assert "it broke" in events[-1]["error"]

    def test_nothing_downstream_of_a_failure_runs(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "boom", script=make.fails(3, "it broke"))
        make.write_tool(workspace, "t", script=make.prints("ran"))
        definition = flow(
            {"id": "a", "tool": "boom", "push": ["b"]},
            {"id": "b", "tool": "t"},
        )
        events: list[dict[str, Any]] = []
        with pytest.raises(FlowError):
            execute(definition, definition["steps"], {}, paths, on_event=events.append)
        assert "b" not in [event["step"] for event in events]

    def test_a_secret_a_failing_tool_echoed_is_scrubbed_from_the_error(
        self, paths: Paths, workspace: Path
    ) -> None:
        """The message reaches logs and terminals, so it is scrubbed before it travels."""
        make.write_tool(
            workspace,
            "leaky",
            script=make.sh('cat >/dev/null\nprintf %s "${token-}" >&2\nexit 4\n'),
        )
        vault = Vault(path=workspace / "v", values={"token": "s3cret-value"})
        definition = flow({"id": "a", "tool": "leaky", "secrets": ["token"]})
        with pytest.raises(FlowError) as caught:
            execute(definition, definition["steps"], {}, paths, vault)
        assert "s3cret-value" not in str(caught.value)
        assert "***" in str(caught.value)

    def test_an_unmatched_switch_fails_after_its_step_succeeded(
        self, paths: Paths, workspace: Path
    ) -> None:
        """The step is still reported as started; a step that vanishes from the progress
        display is worse than one that fails."""
        make.write_tool(workspace, "choose", script=make.prints("sideways"))
        make.write_tool(workspace, "t", script=make.prints("ran"))
        definition = flow(
            {
                "id": "choice",
                "tool": "choose",
                "switch": "{{ this.text }}",
                "cases": {"left": ["l"]},
            },
            {"id": "l", "tool": "t"},
        )
        events: list[dict[str, Any]] = []
        with pytest.raises(FlowError, match="switched on 'sideways'"):
            execute(definition, definition["steps"], {}, paths, on_event=events.append)
        assert [event["kind"] for event in events] == ["started", "failed"]

    def test_a_switch_reports_where_it_sent_its_result(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "choose", script=make.prints("left"))
        make.write_tool(workspace, "t", script=make.prints("ran"))
        definition = flow(
            {
                "id": "choice",
                "tool": "choose",
                "switch": "{{ this.text }}",
                "cases": {"left": ["l"], "right": ["r"]},
            },
            {"id": "l", "tool": "t"},
            {"id": "r", "tool": "t"},
        )
        events: list[dict[str, Any]] = []
        execute(definition, definition["steps"], {}, paths, on_event=events.append)
        finished = next(e for e in events if e["kind"] == "finished" and e["step"] == "choice")
        assert finished["is_switch"] is True
        assert finished["pushed_to"] == ["l"]


class TestRunFlow:
    def test_renders_the_declared_output_template(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "t", script=make.prints("the answer"))
        definition = flow({"id": "a", "tool": "t"}, output={"template": "  {{ steps.a.text }}\n"})
        output, _ = run_flow(definition, {}, paths)
        assert output == "the answer"

    def test_a_flow_that_declares_no_output_has_none(self, paths: Paths, workspace: Path) -> None:
        """A flow may be there for its effect, so the step's result is not printed for it."""
        make.write_tool(workspace, "t", script=make.prints("x"))
        definition = flow({"id": "a", "tool": "t"})
        output, _ = run_flow(definition, {}, paths)
        assert output == ""

    def test_the_flow_is_validated_before_anything_runs(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "t", script=make.prints("x"))
        definition = flow({"id": "a", "tool": "t", "push": ["ghost"]})
        with pytest.raises(FlowError, match="pushes to unknown step 'ghost'"):
            run_flow(definition, {}, paths)

    def test_the_output_template_may_read_the_flows_inputs(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(workspace, "t", script=make.prints("sig"))
        definition = flow(
            {"id": "a", "tool": "t"},
            inputs={"path": {"required": True}},
            output={"template": "{{ steps.a.text }}  {{ inputs.path }}"},
        )
        output, _ = run_flow(definition, {"path": "notes.md"}, paths)
        assert output == "sig  notes.md"

    def test_the_returned_steps_are_the_validated_ones(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "t", script=make.prints("x"))
        definition = flow({"id": "a", "tool": "t"})
        assert validate(definition, paths) == definition["steps"]
