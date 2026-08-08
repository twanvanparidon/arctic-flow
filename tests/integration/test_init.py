"""`atf init`, and what the directory it writes then does.

The claim worth an integration test is not that files appear. It is that the four parts
agree afterwards: `init` writes a layer, the resolver searches it, `list` reports what is
in it, and the config it left behind is one the engine reads and acts on. Each of those is
a place they could stop agreeing, and none of them is visible from `commands.initialise`
alone.

`$HOME` is the temporary one for the whole suite (see `tests/conftest.py`), so these write
a real home layer without touching the developer's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from support import components as make
from support.outcome import Runner


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with nothing in it. Its own directory, since `tmp_path` also holds
    `bin/` for the fake `claude` and `home/` for `$HOME`."""
    root = tmp_path / "project"
    root.mkdir()
    return root


class TestInit:
    def test_it_reports_what_it_wrote(self, atf: Runner) -> None:
        result = atf("init")
        assert result.code == 0, result.err
        for name in ("tools/", "agents/", "flows/", "config.yaml"):
            assert name in result.out

    def test_running_it_twice_writes_nothing_the_second_time(self, atf: Runner) -> None:
        atf("init")
        result = atf("init")
        assert result.code == 0
        assert "created" not in result.out

    def test_a_tool_dropped_in_it_resolves_from_any_project(
        self, atf: Runner, home: Path, project: Path
    ) -> None:
        """What the command is for. The layer was always searched; it had to exist first."""
        atf("init")
        make.write_tool(home / ".arctic", "greet", script=make.prints("hello"))
        result = atf("--workspace", str(project), "list")
        assert result.code == 0, result.err
        assert "greet" in result.out and "$HOME/.arctic" in result.out

    def test_a_project_still_overrides_what_it_inherits(
        self, atf: Runner, home: Path, project: Path
    ) -> None:
        atf("init")
        make.write_tool(home / ".arctic", "greet", script=make.prints("from home"))
        make.write_tool(project, "greet", script=make.prints("from the project"))
        result = atf("--workspace", str(project), "inspect", "tool", "greet")
        assert result.code == 0, result.err
        assert "./tools/greet" in result.out


class TestTheConfigItWrites:
    def test_every_command_refuses_while_it_is_broken(self, atf: Runner, home: Path) -> None:
        """It is read as the paths are built, so this is not one command's problem."""
        atf("init")
        (home / ".arctic" / "config.yaml").write_text("run:\n  max_minute: 5\n")
        result = atf("list")
        assert result.code == 1
        assert "max_minute" in result.err

    def test_its_ceiling_stops_a_run(self, atf: Runner, home: Path, project: Path) -> None:
        atf("init")
        # A second, written as the fraction of a minute the file takes. A whole minute
        # would be a whole minute of suite.
        (home / ".arctic" / "config.yaml").write_text(f"run:\n  max_minutes: {1 / 60}\n")
        make.write_tool(
            project,
            "slow",
            script=make.sleeps(30),
            run={"command": ["./run.sh"], "timeout_seconds": 30},
        )
        make.write_flow(
            project,
            "slow",
            {
                "flow": "slow",
                "start": "wait",
                "steps": [{"id": "wait", "tool": "slow", "input": {}}],
            },
        )

        result = atf("--workspace", str(project), "run", "slow")
        assert result.code == 1
        assert "run.max_minutes" in result.err

    def test_a_source_it_names_becomes_a_search_layer(
        self, atf: Runner, home: Path, project: Path, tmp_path: Path
    ) -> None:
        shared = tmp_path / "shared"
        make.write_tool(shared, "greet", script=make.prints("shared"))
        atf("init")
        (home / ".arctic" / "config.yaml").write_text(f"sources:\n  - {shared}\n")

        result = atf("--workspace", str(project), "list")
        assert result.code == 0, result.err
        assert "greet" in result.out
