"""Loading a component, and running one.

The half of the engine that touches the world: it reads a directory someone else wrote and
spawns a process it did not write. So these tests write real directories and run real
processes. Nothing is substituted, because the failures worth catching here are exactly the
ones a substitute cannot have: a missing file, a lost executable bit, a process that
outlives its timeout, a secret arriving in an environment it should not be in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine.executor import (
    FlowError,
    check_payload,
    child_environment,
    exit_summary,
    invoke,
    load_agent,
    load_component,
    maybe_json,
    spawn,
)
from paths.resolver import Paths
from support import components as make


class TestLoadComponent:
    def test_returns_the_directory_and_the_parsed_spec(self, paths: Paths, workspace: Path) -> None:
        base = make.write_tool(workspace, "greet")
        found, spec = load_component(paths, "tool", "greet")
        assert found == base
        assert spec["name"] == "greet"

    def test_a_name_that_resolves_to_nothing_is_a_flow_error(self, paths: Paths) -> None:
        """The resolver's LookupError_ is re-raised as a FlowError, so a flow fails as a flow."""
        with pytest.raises(FlowError, match="unknown tool 'absent'"):
            load_component(paths, "tool", "absent")

    def test_a_spec_that_is_not_json_names_the_file(self, paths: Paths, workspace: Path) -> None:
        base = make.write_tool(workspace, "broken")
        (base / "spec.json").write_text("{not json")
        with pytest.raises(FlowError, match=r"\./tools/broken/spec\.json is not valid JSON"):
            load_component(paths, "tool", "broken")

    def test_the_first_root_wins(self, paths: Paths, workspace: Path) -> None:
        """Overriding is per name and total, so the project's copy is the whole answer."""
        make.write_tool(workspace / ".arctic", "greet", script=make.prints("from .arctic"))
        make.write_tool(workspace, "greet", script=make.prints("from the project"))
        base, _ = load_component(paths, "tool", "greet")
        assert base == workspace / ".arctic" / "tools" / "greet"


class TestLoadAgent:
    def test_reads_agent_md_as_the_system_prompt(self, paths: Paths, workspace: Path) -> None:
        make.write_agent(workspace, "writer", prompt="You are terse.")
        spec, system = load_agent(paths, "writer")
        assert spec["adapter"] == "echo"
        assert system == "You are terse."

    def test_honours_a_declared_prompt_filename(self, paths: Paths, workspace: Path) -> None:
        make.write_agent(workspace, "writer", system_prompt="role.md", prompt="From role.md.")
        _, system = load_agent(paths, "writer")
        assert system == "From role.md."

    def test_strips_surrounding_whitespace(self, paths: Paths, workspace: Path) -> None:
        make.write_agent(workspace, "writer", prompt="\n\n  You are terse.  \n\n")
        _, system = load_agent(paths, "writer")
        assert system == "You are terse."

    def test_a_missing_prompt_file_is_an_error(self, paths: Paths, workspace: Path) -> None:
        make.write_agent(workspace, "writer", write_prompt=False)
        with pytest.raises(FlowError, match="points at agent.md .* which is missing"):
            load_agent(paths, "writer")

    def test_a_declared_prompt_file_that_is_missing_names_that_file(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_agent(workspace, "writer", system_prompt="role.md", write_prompt=False)
        with pytest.raises(FlowError, match="points at role.md"):
            load_agent(paths, "writer")

    @pytest.mark.parametrize("prompt", ["", "   \n  \t "])
    def test_an_empty_prompt_is_an_error(self, paths: Paths, workspace: Path, prompt: str) -> None:
        """A blank system prompt is a silent behaviour change, so it is refused loudly."""
        make.write_agent(workspace, "writer", prompt=prompt)
        with pytest.raises(FlowError, match="empty system prompt"):
            load_agent(paths, "writer")


class TestCheckPayload:
    SCHEMA = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "max_lines": {"type": "integer"}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def test_a_valid_payload_passes_quietly(self) -> None:
        assert check_payload(self.SCHEMA, {"path": "a.txt"}, "who") is None

    def test_names_the_component_that_rejected_it(self) -> None:
        with pytest.raises(FlowError, match="input rejected by read_file/spec.json"):
            check_payload(self.SCHEMA, {}, "read_file/spec.json")

    def test_a_failure_at_the_document_root_is_labelled_root(self) -> None:
        with pytest.raises(FlowError, match="<root>: 'path' is a required property"):
            check_payload(self.SCHEMA, {}, "who")

    def test_a_failure_inside_the_payload_carries_its_key(self) -> None:
        with pytest.raises(FlowError, match="max_lines: 'many' is not of type 'integer'"):
            check_payload(self.SCHEMA, {"path": "a", "max_lines": "many"}, "who")

    def test_every_failure_is_reported_at_once(self) -> None:
        """Sorted by path, so two runs of the same broken payload read the same."""
        with pytest.raises(FlowError) as caught:
            check_payload(self.SCHEMA, {"max_lines": "many", "extra": 1}, "who")
        message = str(caught.value)
        assert message.index("<root>") < message.index("max_lines")
        assert "'path' is a required property" in message
        assert "Additional properties are not allowed" in message


class TestSpawn:
    def test_the_payload_arrives_on_stdin_as_one_json_object(
        self, paths: Paths, workspace: Path
    ) -> None:
        base = make.write_tool(workspace, "echo_in", script=make.ECHO_STDIN)
        _, spec = load_component(paths, "tool", "echo_in")
        proc = spawn(base, spec, {"a": 1, "b": "two"}, paths)
        assert json.loads(proc.stdout) == {"a": 1, "b": "two"}

    def test_the_payload_is_checked_against_the_tools_own_schema_first(
        self, paths: Paths, workspace: Path
    ) -> None:
        base = make.write_tool(
            workspace,
            "picky",
            input_schema={"type": "object", "required": ["text"]},
        )
        _, spec = load_component(paths, "tool", "picky")
        with pytest.raises(FlowError, match="input rejected by picky/spec.json"):
            spawn(base, spec, {}, paths)

    def test_the_process_runs_in_the_workspace_wherever_the_tool_was_found(
        self, paths: Paths, workspace: Path, home: Path
    ) -> None:
        """Found in ~/.arctic, run against the project in front of it."""
        make.write_tool(home / ".arctic", "where", script=make.sh("cat >/dev/null\npwd -P\n"))
        base, spec = load_component(paths, "tool", "where")
        assert base.is_relative_to(home)
        assert spawn(base, spec, {}, paths).stdout.strip() == str(workspace)

    def test_arguments_after_the_command_are_passed_through(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(
            workspace,
            "argued",
            script=make.sh('cat >/dev/null\nprintf %s "$1"\n'),
            run={"command": ["./run.sh", "second"], "timeout_seconds": 20},
        )
        base, spec = load_component(paths, "tool", "argued")
        assert spawn(base, spec, {}, paths).stdout == "second"

    def test_an_absolute_command_is_used_as_written(self, paths: Paths, workspace: Path) -> None:
        """`base / "/bin/sh"` is `/bin/sh`, so a tool may name something already installed."""
        make.write_tool(
            workspace,
            "systemwide",
            run={"command": ["/bin/echo", "hello"], "timeout_seconds": 20},
        )
        base, spec = load_component(paths, "tool", "systemwide")
        assert spawn(base, spec, {}, paths).stdout == "hello\n"

    def test_a_non_zero_exit_comes_back_rather_than_raising(
        self, paths: Paths, workspace: Path
    ) -> None:
        """spawn() hands the code to its caller: a tool step and a gate read it differently."""
        make.write_tool(workspace, "refuses", script=make.fails(3, "no", stdout="partial"))
        base, spec = load_component(paths, "tool", "refuses")
        proc = spawn(base, spec, {}, paths)
        assert (proc.returncode, proc.stdout, proc.stderr) == (3, "partial", "no")

    def test_a_tool_that_outlives_its_timeout_is_a_flow_error(
        self, paths: Paths, workspace: Path
    ) -> None:
        make.write_tool(
            workspace,
            "slow",
            script=make.sleeps(5),
            run={"command": ["./run.sh"], "timeout_seconds": 1},
        )
        base, spec = load_component(paths, "tool", "slow")
        with pytest.raises(FlowError, match="slow exceeded its 1s timeout"):
            spawn(base, spec, {}, paths)

    def test_only_the_granted_secrets_reach_the_child(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "env_dump", script=make.echoes_environment())
        base, spec = load_component(paths, "tool", "env_dump")
        environment = json.loads(spawn(base, spec, {}, paths, secrets={"granted": "s3cret"}).stdout)
        assert environment["granted"] == "s3cret"
        assert "withheld" not in environment

    def test_a_command_that_does_not_exist_raises_the_os_error(
        self, paths: Paths, workspace: Path
    ) -> None:
        """Not wrapped: `lint` refuses this spec long before a run reaches it."""
        base = make.write_tool(workspace, "absent")
        (base / "run.sh").unlink()
        base, spec = load_component(paths, "tool", "absent")
        with pytest.raises(FileNotFoundError):
            spawn(base, spec, {}, paths)


class TestExitSummary:
    @staticmethod
    def completed(code: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["./run.sh"], returncode=code, stdout="", stderr=stderr
        )

    def test_uses_the_components_own_words_for_the_code(self) -> None:
        spec = {"name": "signer", "exit_codes": {"5": "no signing key"}}
        assert exit_summary(spec, self.completed(5)) == "signer failed (exit 5: no signing key)"

    def test_a_code_the_spec_does_not_list_says_so(self) -> None:
        spec = {"name": "signer", "exit_codes": {"5": "no signing key"}}
        assert "unspecified exit code" in exit_summary(spec, self.completed(9))

    def test_a_spec_with_no_exit_codes_at_all_still_summarises(self) -> None:
        assert "unspecified exit code" in exit_summary({"name": "t"}, self.completed(1))

    def test_the_last_line_of_stderr_is_appended(self) -> None:
        """Tools are told to print one line; the last one is the one that failed."""
        summary = exit_summary({"name": "t"}, self.completed(1, "warming up\nreal reason\n"))
        assert summary.endswith(". real reason")

    def test_blank_stderr_adds_nothing(self) -> None:
        assert exit_summary({"name": "t"}, self.completed(1, "  \n ")).endswith(")")


class TestInvoke:
    def test_returns_stdout_on_success(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(workspace, "ok", script=make.prints("the answer"))
        base, spec = load_component(paths, "tool", "ok")
        assert invoke(base, spec, {}, paths) == "the answer"

    def test_any_non_zero_exit_fails_the_step(self, paths: Paths, workspace: Path) -> None:
        make.write_tool(
            workspace,
            "refuses",
            script=make.fails(4, "not permitted"),
            exit_codes={"4": "not permitted"},
        )
        base, spec = load_component(paths, "tool", "refuses")
        with pytest.raises(FlowError, match=r"refuses failed \(exit 4: not permitted\)"):
            invoke(base, spec, {}, paths)


class TestChildEnvironment:
    def test_starts_from_the_engines_own_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INHERITED", "yes")
        assert child_environment()["INHERITED"] == "yes"

    def test_adds_the_granted_secrets(self) -> None:
        assert child_environment({"token": "abc"})["token"] == "abc"

    def test_a_secret_overrides_an_inherited_variable_of_the_same_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("token", "from the shell")
        assert child_environment({"token": "from the vault"})["token"] == "from the vault"

    def test_no_secrets_is_the_environment_unchanged(self) -> None:
        assert child_environment() == child_environment({})

    def test_the_result_is_a_copy(self) -> None:
        env = child_environment({"token": "abc"})
        env["token"] = "changed"
        assert os.environ.get("token") is None

    def test_outside_a_frozen_build_the_library_path_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/lib")
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
        env = child_environment()
        assert env["LD_LIBRARY_PATH"] == "/opt/lib"
        assert env["LD_LIBRARY_PATH_ORIG"] == "/usr/lib"

    def test_a_frozen_build_restores_the_original_library_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PyInstaller saves what it overwrote as <NAME>_ORIG. Without putting it back, a
        spawned system binary loads the bundle's OpenSSL and fails."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
        env = child_environment()
        assert env["LD_LIBRARY_PATH"] == "/usr/lib"
        assert "LD_LIBRARY_PATH_ORIG" not in env

    def test_a_frozen_build_drops_a_variable_that_had_no_original(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
        assert "LD_LIBRARY_PATH" not in child_environment()


class TestMaybeJson:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('{"a": 1}', {"a": 1}),
            ("[1, 2]", [1, 2]),
            ('"just a string"', "just a string"),
            ("3", 3),
            ("true", True),
            ("  {}  ", {}),
        ],
    )
    def test_parses_what_json_can_parse(self, text: str, expected: object) -> None:
        assert maybe_json(text) == expected

    @pytest.mark.parametrize("text", ["", "   ", "not json", "{unclosed", "a: 1"])
    def test_anything_else_is_none_rather_than_an_error(self, text: str) -> None:
        """A tool is allowed to print prose. Only the `json` view of its result is empty."""
        assert maybe_json(text) is None

    def test_the_literal_null_is_indistinguishable_from_unparseable(self) -> None:
        """Both are None. Nothing in the engine tells them apart, so nothing should try."""
        assert maybe_json("null") is None
