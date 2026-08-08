"""The run ceiling: `run.max_minutes` from `~/.arctic/config.yaml`.

Real tools that really outlive it, because the claim is about a process being stopped and
that is not something a stand-in can fail at. The ceilings here are short so the suite
stays fast; nothing asserts on how long anything took, only on what happened.

Not covered, and deliberately: the gap where an agent turn already in flight runs to its
own timeout. Reaching it needs an adapter that blocks, and `adapters.echo` answers at once
by design. `_check_ceiling` says what the gap is and why it is left open.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from engine.executor import FlowError, run_flow, validate
from paths.resolver import Paths
from support import components as make


def flow(*steps: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    built: dict[str, Any] = {"flow": "demo", "start": steps[0]["id"], "steps": list(steps)}
    built.update(overrides)
    return built


def ceiling(home: Path, seconds: float) -> None:
    """Takes seconds and writes minutes, which is what the file is in.

    A test ceiling has to fire while the suite is still running, so every one of them is
    a fraction of a minute. Converting here keeps each test saying the short thing it
    means instead of a `0.0167` nobody can read."""
    (home / ".arctic").mkdir(exist_ok=True)
    (home / ".arctic" / "config.yaml").write_text(f"run:\n  max_minutes: {seconds / 60}\n")


class TestTheCeiling:
    def test_a_run_that_outlives_it_fails(self, paths: Paths, workspace: Path, home: Path) -> None:
        make.write_tool(
            workspace,
            "slow",
            script=make.sleeps(30),
            run={"command": ["./run.sh"], "timeout_seconds": 30},
        )
        ceiling(home, 1)
        definition = flow({"id": "wait", "tool": "slow", "input": {}})

        with pytest.raises(FlowError, match="ceiling"):
            run_flow(definition, {}, Paths(workspace, env={}, home=home))

    def test_the_failure_names_the_setting_that_caused_it(
        self, workspace: Path, home: Path
    ) -> None:
        """Five search roots and one config: saying which knob is the difference between
        fixing it and hunting for it."""
        make.write_tool(
            workspace,
            "slow",
            script=make.sleeps(30),
            run={"command": ["./run.sh"], "timeout_seconds": 30},
        )
        ceiling(home, 1)
        definition = flow({"id": "wait", "tool": "slow", "input": {}})

        with pytest.raises(FlowError, match="run.max_minutes"):
            run_flow(definition, {}, Paths(workspace, env={}, home=home))

    def test_it_stops_the_tool_rather_than_waiting_out_its_timeout(
        self, workspace: Path, home: Path, tmp_path: Path
    ) -> None:
        """A ceiling that only stopped *starting* steps would raise here and leave the
        tool running, and both look the same from the caller's side. The marker is what
        tells them apart, so it is the claim rather than the exception."""
        finished = tmp_path / "finished"
        make.write_tool(
            workspace,
            "blocker",
            script=make.marks_later(finished, seconds=2),
            run={"command": ["./run.sh"], "timeout_seconds": 30},
        )
        ceiling(home, 1)
        definition = flow({"id": "wait", "tool": "blocker", "input": {}})

        with pytest.raises(FlowError, match="ceiling"):
            run_flow(definition, {}, Paths(workspace, env={}, home=home))
        # Past the marker's own deadline, so a survivor has had its chance to write it.
        # Asserting straight after the raise would pass whether or not anything stopped.
        time.sleep(2.5)
        assert not finished.exists()

    def test_a_run_inside_it_is_untouched(self, workspace: Path, home: Path) -> None:
        make.write_tool(workspace, "greet", script=make.prints("hello"))
        ceiling(home, 60)
        definition = flow(
            {"id": "say", "tool": "greet", "input": {}},
            output={"template": "{{ steps.say.text }}"},
        )
        output, _ = run_flow(definition, {}, Paths(workspace, env={}, home=home))
        assert output == "hello"

    def test_no_config_means_no_ceiling(self, paths: Paths, workspace: Path) -> None:
        """The default has to be the behaviour that existed before the setting did."""
        assert paths.config.max_run_minutes is None
        make.write_tool(workspace, "greet", script=make.prints("hello"))
        definition = flow(
            {"id": "say", "tool": "greet", "input": {}},
            output={"template": "{{ steps.say.text }}"},
        )
        assert run_flow(definition, {}, paths)[0] == "hello"

    def test_it_is_the_whole_run_rather_than_one_step(self, workspace: Path, home: Path) -> None:
        """Three steps that each finish well inside the ceiling still exceed it together.
        A per-step limit is what `timeout_seconds` already is; this is the other thing."""
        make.write_tool(
            workspace,
            "tick",
            script=make.sleeps(1),
            run={"command": ["./run.sh"], "timeout_seconds": 30},
        )
        ceiling(home, 2)
        definition = flow(
            {"id": "one", "tool": "tick", "input": {}, "push": ["two"]},
            {"id": "two", "tool": "tick", "input": {}, "push": ["three"]},
            {"id": "three", "tool": "tick", "input": {}},
        )
        with pytest.raises(FlowError, match="ceiling"):
            run_flow(definition, {}, Paths(workspace, env={}, home=home))

    def test_a_ceiling_does_not_change_what_validate_accepts(
        self, workspace: Path, home: Path
    ) -> None:
        """It is enforced while running, not declared by the flow, so `lint` on a machine
        with a ceiling and one without must answer the same."""
        make.write_tool(workspace, "greet", script=make.prints("hello"))
        ceiling(home, 1)
        definition = flow({"id": "say", "tool": "greet", "input": {}})
        assert validate(definition, Paths(workspace, env={}, home=home))
