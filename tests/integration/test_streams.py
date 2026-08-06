"""Which file descriptor each byte came out of, from a real process.

This is the one thing the in-process runner cannot answer honestly. `atf run > file` has to
produce the flow's result and nothing else, the frame has to appear only when a person is
watching both streams, and the exit status has to survive `sys.exit`. All three are claims
about a process, so all three are tested with one.

The terminal cases use a pty pair, because "is this a tty" is a question the kernel answers
and an object with an `isatty` method does not.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support import components as make

from .conftest import ENTRY_POINT, Outcome, Runner

FLOW = {
    "flow": "one",
    "start": "a",
    "steps": [{"id": "a", "tool": "shout", "input": {"text": "the answer"}}],
    "output": {"template": "{{ steps.a.text }}"},
}


@pytest.fixture(autouse=True)
def one_step(project: Path) -> None:
    make.write_flow(project, "one", FLOW)


def on_a_terminal(argv: list[str], env: dict[str, str] | None = None) -> Outcome:
    """Run the CLI with both streams attached to real terminals."""
    out_controller, out_follower = pty.openpty()
    err_controller, err_follower = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, str(ENTRY_POINT), *argv],
        stdin=subprocess.DEVNULL,
        stdout=out_follower,
        stderr=err_follower,
        env={**os.environ, **(env or {})},
    )
    # Our copies go, so a read returns end-of-file once the child's copies close too.
    os.close(out_follower)
    os.close(err_follower)

    collected = {out_controller: b"", err_controller: b""}
    deadline = time.monotonic() + 30
    open_fds = [out_controller, err_controller]
    while open_fds and time.monotonic() < deadline:
        ready, _, _ = select.select(open_fds, [], [], 0.1)
        for fd in ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # the far end closed, which on a pty is an error rather than b""
                chunk = b""
            if chunk:
                collected[fd] += chunk
            else:
                open_fds.remove(fd)
    for fd in (out_controller, err_controller):
        os.close(fd)

    def text(fd: int) -> str:
        return collected[fd].decode("utf-8", "replace").replace("\r\n", "\n")

    return Outcome(
        code=process.wait(timeout=30), out=text(out_controller), err=text(err_controller)
    )


class TestRedirection:
    def test_the_file_holds_the_flows_result_and_nothing_else(
        self, project: Path, tmp_path: Path, atf_process: Runner
    ) -> None:
        """The command the whole output design exists for."""
        destination = tmp_path / "result.txt"
        with destination.open("w") as handle:
            result = atf_process("--workspace", str(project), "run", "one", stdout=handle)
        assert result.code == 0
        assert destination.read_text() == "THE ANSWER\n"

    def test_the_progress_went_to_the_other_stream(
        self, project: Path, tmp_path: Path, atf_process: Runner
    ) -> None:
        destination = tmp_path / "result.txt"
        with destination.open("w") as handle:
            result = atf_process("--workspace", str(project), "run", "one", stdout=handle)
        assert "✓ a" in result.err

    def test_a_piped_run_draws_no_frame(self, project: Path, atf_process: Runner) -> None:
        """A header captured into the file someone is building is worse than none."""
        result = atf_process("--workspace", str(project), "run", "one")
        assert result.out == "THE ANSWER\n"
        assert "─" not in result.err


class TestOnATerminal:
    def test_the_output_is_framed(self, project: Path) -> None:
        result = on_a_terminal(["--workspace", str(project), "run", "one"])
        assert result.code == 0
        rule = next(line for line in result.err.splitlines() if "─" in line)
        assert "one" in rule  # the flow's name sits on the rule

    def test_the_frame_is_on_the_other_stream_from_the_output(self, project: Path) -> None:
        result = on_a_terminal(["--workspace", str(project), "run", "one"])
        assert result.out.strip() == "THE ANSWER"
        assert "─" not in result.out

    def test_progress_is_coloured(self, project: Path) -> None:
        assert "\033[" in on_a_terminal(["--workspace", str(project), "run", "one"]).err

    def test_no_color_takes_the_colour_and_leaves_the_progress(self, project: Path) -> None:
        result = on_a_terminal(["--workspace", str(project), "run", "one"], env={"NO_COLOR": "1"})
        assert "\033[" not in result.err
        assert "✓ a" in result.err

    def test_quiet_removes_the_frame_along_with_the_rest(self, project: Path) -> None:
        result = on_a_terminal(["--workspace", str(project), "run", "one", "--quiet"])
        assert result.out.strip() == "THE ANSWER"
        assert result.err == ""

    def test_the_banner_appears_only_for_a_person(self, project: Path, atf_process: Runner) -> None:
        assert "A R C T I C" in on_a_terminal(["--version"]).out
        assert atf_process("--version").out == "atf 0.1.0\n"


class TestExitStatus:
    def test_a_flow_that_ran_exits_zero(self, project: Path, atf_process: Runner) -> None:
        assert atf_process("--workspace", str(project), "run", "one").code == 0

    def test_an_expected_failure_exits_one(self, project: Path, atf_process: Runner) -> None:
        result = atf_process("--workspace", str(project), "run", "absent")
        assert result.code == 1
        assert result.err.startswith("engine: ")
        assert result.out == ""

    def test_a_usage_mistake_is_argparses_two(self, atf_process: Runner) -> None:
        assert atf_process("run").code == 2

    def test_nothing_after_the_command_prints_help_and_succeeds(self, atf_process: Runner) -> None:
        result = atf_process()
        assert result.code == 0
        assert "usage: atf" in result.out

    def test_an_interrupted_run_exits_130(self, project: Path, tmp_path: Path) -> None:
        """Ctrl-C is not a crash. The engine says so and leaves a conventional status."""
        started = tmp_path / "started.flag"
        make.write_tool(
            project,
            "slow",
            script=make.python(
                f"import pathlib, time\npathlib.Path({str(started)!r}).write_text('x')\n"
                "time.sleep(3)\n"
            ),
        )
        make.write_flow(
            project,
            "slow_flow",
            {"flow": "slow_flow", "start": "a", "steps": [{"id": "a", "tool": "slow"}]},
        )
        process = subprocess.Popen(
            [sys.executable, str(ENTRY_POINT), "--workspace", str(project), "run", "slow_flow"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 20
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        process.send_signal(signal.SIGINT)
        _, err = process.communicate(timeout=30)
        assert process.returncode == 130
        assert "engine: interrupted" in err
