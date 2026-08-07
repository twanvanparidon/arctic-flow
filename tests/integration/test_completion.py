"""Completion through the CLI, and the snippet through a real bash.

The snippet is the half no unit test reaches. It decides which words are sent, that the
empty one under the cursor is among them, and what is done with the answer. bash runs it
here against an `atf` on PATH, because the two halves agreeing is the whole feature.

`bash` is a hard requirement rather than a skip: the engine's own tools are bash scripts, so
a machine without it cannot run a flow either.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from support import components as make

from .conftest import ENTRY_POINT, Runner

FLOW = {"flow": "release", "start": "a", "steps": [{"id": "a", "tool": "echo_input"}]}


@pytest.fixture
def installed_atf(tmp_path: Path) -> Path:
    """An `atf` on PATH, which is the command the snippet completes.

    A wrapper around this checkout rather than a built binary. The snippet cares that the
    command exists and answers on stdout, not what it is underneath.
    """
    binaries = tmp_path / "completion-bin"
    binaries.mkdir(exist_ok=True)
    wrapper = binaries / "atf"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ENTRY_POINT}" "$@"\n')
    wrapper.chmod(0o755)
    return wrapper


def bash(script: str, on_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a completion by hand, the way readline would.

    `set -eu` is part of the test: an interactive shell may well have `-u` set, and a
    completion function that trips over an unset array element there would take the prompt
    with it. `on_path` is left out by the test that types a path instead of a name, so that
    one fails if the snippet ever calls a bare `atf` again.
    """
    env = dict(os.environ)
    if on_path:
        env["PATH"] = f"{on_path.parent}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", "-c", f"set -eu\n{script}"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


class TestCandidatesThroughTheCli:
    def test_a_flow_in_the_workspace_is_offered(self, atf: Runner, project: Path) -> None:
        make.write_flow(project, "release", FLOW)
        outcome = atf("--workspace", str(project), "__complete", "--", "run", "")
        assert outcome.code == 0
        assert outcome.out.split() == ["release"]

    def test_the_commands_come_out_one_per_line(self, atf: Runner) -> None:
        """One candidate per line is the protocol; the snippet reads it with mapfile."""
        outcome = atf("__complete", "--", "")
        assert "run" in outcome.out.split("\n")

    def test_no_candidates_prints_nothing_at_all(self, atf: Runner, project: Path) -> None:
        """A blank line would reach bash as one empty candidate, which reads as an answer
        and stops it falling back to filename completion."""
        outcome = atf("--workspace", str(project), "__complete", "--", "run", "")
        assert outcome.out == ""
        assert outcome.code == 0

    def test_a_workspace_that_is_not_there_is_not_an_error(self, atf: Runner) -> None:
        outcome = atf("--workspace", "/no/such/project", "__complete", "--", "run", "")
        assert (outcome.code, outcome.out, outcome.err) == (0, "", "")

    def test_a_directory_it_cannot_read_is_not_an_error_either(
        self, atf: Runner, project: Path
    ) -> None:
        """The lookup raises PermissionError here. A traceback would land on top of the
        command line someone is still typing, so completion answers with nothing instead."""
        if os.geteuid() == 0:
            pytest.skip("root reads an unreadable directory anyway")
        make.write_flow(project, "release", FLOW)
        flows = project / "flows"
        flows.chmod(0o000)
        try:
            outcome = atf("--workspace", str(project), "__complete", "--", "run", "")
        finally:
            flows.chmod(0o755)
        assert (outcome.code, outcome.out, outcome.err) == (0, "", "")


class TestTheCompletionCommand:
    def test_it_prints_a_snippet_that_registers_itself(self, atf: Runner) -> None:
        outcome = atf("completion", "bash")
        assert outcome.code == 0
        assert "complete -F _atf_complete" in outcome.out
        assert outcome.err == ""

    def test_a_shell_with_no_snippet_is_refused(self, atf: Runner) -> None:
        """The flag's choices are the files that exist, so this is argparse saying two."""
        assert atf("completion", "fish").code == 2


class TestTheSnippetInBash:
    """The function is called the way readline calls it, with the command as its first
    argument. Calling it with none would test a shape bash never produces."""

    def test_it_completes_a_flow_name(
        self, atf: Runner, project: Path, installed_atf: Path
    ) -> None:
        make.write_flow(project, "release", FLOW)
        completed = bash(
            f"""
            eval "$(atf completion bash)"
            COMP_WORDS=(atf --workspace {project} run)
            COMP_CWORD=4
            _atf_complete atf
            printf '%s\\n' "${{COMPREPLY[@]}}"
            """,
            installed_atf,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.split() == ["release"]

    def test_it_completes_a_command_with_nothing_typed_after_atf(self, installed_atf: Path) -> None:
        """COMP_CWORD of 1 is the case a slice gets wrong: there is no word at the cursor
        yet, and the empty one still has to be sent."""
        completed = bash(
            """
            eval "$(atf completion bash)"
            COMP_WORDS=(atf)
            COMP_CWORD=1
            _atf_complete atf
            printf '%s\\n' "${COMPREPLY[@]}"
            """,
            installed_atf,
        )
        assert completed.returncode == 0, completed.stderr
        assert "run" in completed.stdout.split()

    def test_a_partial_word_is_narrowed_rather_than_listed(self, installed_atf: Path) -> None:
        completed = bash(
            """
            eval "$(atf completion bash)"
            COMP_WORDS=(atf li)
            COMP_CWORD=1
            _atf_complete atf
            printf '%s\\n' "${COMPREPLY[@]}"
            """,
            installed_atf,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.split() == ["lint", "list"]

    def test_a_build_at_a_path_completes_against_itself(self, installed_atf: Path) -> None:
        """Nothing called `atf` is on PATH here. A build somewhere else is how this gets
        tested during development, and the snippet has to answer from the build being typed
        rather than from whatever the name resolves to."""
        completed = bash(
            f"""
            eval "$("{installed_atf}" completion bash)"
            complete -F _atf_complete -o default -o bashdefault "{installed_atf}"
            COMP_WORDS=("{installed_atf}" li)
            COMP_CWORD=1
            _atf_complete "{installed_atf}"
            printf '%s\\n' "${{COMPREPLY[@]}}"
            """
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.split() == ["lint", "list"]

    def test_it_registers_atf_for_completion(self, installed_atf: Path) -> None:
        """`complete -p` is bash reporting what the eval actually installed."""
        completed = bash('eval "$(atf completion bash)"\ncomplete -p atf', installed_atf)
        assert completed.returncode == 0, completed.stderr
        assert "_atf_complete" in completed.stdout
