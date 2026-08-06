"""Which definition of a name wins, once every layer is real.

The unit tests cover the precedence list directly. What only shows up end to end is that
the winner is also the one that *runs*, that `list` and `paths` describe the same order the
engine used, and that where a component was found never changes where it executes.

That last one is the rule most likely to be broken by accident, so it is tested by running
a tool from the home directory against a project somewhere else and asking it where it is.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support import components as make

from .conftest import Runner

PWD_TOOL = make.sh("cat >/dev/null\npwd -P\n")


def one_step(project: Path, tool: str, name: str = "probe") -> None:
    make.write_flow(
        project,
        name,
        {
            "flow": name,
            "start": "a",
            "steps": [{"id": "a", "tool": tool, "input": {"path": "note.txt"}}],
            "output": {"template": "{{ steps.a.text }}"},
        },
    )


class TestOverriding:
    def test_a_project_definition_replaces_the_built_in_one(
        self, project: Path, atf: Runner
    ) -> None:
        """Overriding is per name and total: the project's copy inherits nothing."""
        (project / "note.txt").write_text("the real file\n")
        one_step(project, "read_file")
        assert atf("--workspace", str(project), "run", "probe").out == "the real file\n"

        make.write_tool(project, "read_file", script=make.prints("from the project"))
        assert atf("--workspace", str(project), "run", "probe").out == "from the project\n"

    def test_the_dot_directory_beats_the_project_root(self, project: Path, atf: Runner) -> None:
        make.write_tool(project, "read_file", script=make.prints("top level"))
        make.write_tool(project / ".arctic", "read_file", script=make.prints("dot directory"))
        one_step(project, "read_file")
        assert atf("--workspace", str(project), "run", "probe").out == "dot directory\n"

    def test_atf_path_beats_everything(
        self, project: Path, tmp_path: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make.write_tool(project / ".arctic", "read_file", script=make.prints("dot directory"))
        override = tmp_path / "override"
        make.write_tool(override, "read_file", script=make.prints("from ATF_PATH"))
        monkeypatch.setenv("ATF_PATH", str(override))
        one_step(project, "read_file")
        assert atf("--workspace", str(project), "run", "probe").out == "from ATF_PATH\n"

    def test_a_tool_installed_at_home_is_available_to_every_project(
        self, project: Path, home: Path, atf: Runner
    ) -> None:
        make.write_tool(home / ".arctic", "greet", script=make.prints("hello from home"))
        one_step(project, "greet")
        assert atf("--workspace", str(project), "run", "probe").out == "hello from home\n"


class TestWhereAComponentRuns:
    def test_a_tool_found_at_home_still_acts_on_the_project(
        self, project: Path, home: Path, atf: Runner
    ) -> None:
        """Where a component is found never changes where it runs."""
        make.write_tool(home / ".arctic", "where", script=PWD_TOOL)
        one_step(project, "where")
        assert atf("--workspace", str(project), "run", "probe").out.strip() == str(project)

    def test_the_directory_atf_was_invoked_from_does_not_matter(
        self, project: Path, tmp_path: Path, atf_process: Runner
    ) -> None:
        make.write_tool(project, "where", script=PWD_TOOL)
        one_step(project, "where")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(elsewhere)
            result = atf_process("--workspace", str(project), "run", "probe")
        finally:
            os.chdir(previous)
        assert result.out.strip() == str(project)


class TestListing:
    def test_it_reports_what_a_higher_root_is_hiding(
        self, project: Path, home: Path, atf: Runner
    ) -> None:
        """More than one match is the usual reason an edit appears to do nothing."""
        make.write_tool(home / ".arctic", "greet")
        make.write_tool(project, "greet")
        result = atf("--workspace", str(project), "list")
        line = next(line for line in result.out.splitlines() if line.strip().startswith("greet"))
        assert "shadows" in line
        assert "~/.arctic/tools/greet" in line

    def test_something_shadowing_nothing_is_listed_plainly(
        self, project: Path, atf: Runner
    ) -> None:
        make.write_tool(project, "greet")
        result = atf("--workspace", str(project), "list")
        line = next(line for line in result.out.splitlines() if line.strip().startswith("greet"))
        assert "shadows" not in line

    def test_the_adapters_registered_in_code_are_listed_apart(
        self, project: Path, atf: Runner
    ) -> None:
        """Nothing can shadow an adapter, so it has no root to report."""
        result = atf("--workspace", str(project), "list")
        assert result.out.startswith("adapters:")
        assert "claude_code" in result.out

    def test_the_built_in_tool_is_there_with_no_project_at_all(
        self, tmp_path: Path, atf: Runner
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert "read_file" in atf("--workspace", str(empty), "list").out


class TestPaths:
    def test_the_roots_are_numbered_in_the_order_they_are_searched(
        self, project: Path, atf: Runner
    ) -> None:
        make.write_tool(project / ".arctic", "greet")
        result = atf("--workspace", str(project), "paths")
        assert result.out.index(".arctic") < result.out.index("builtin")

    def test_it_says_where_components_will_run(self, project: Path, atf: Runner) -> None:
        assert str(project) in atf("--workspace", str(project), "paths").out

    def test_a_root_with_nothing_in_it_is_still_answered(self, project: Path, atf: Runner) -> None:
        """A root listed with nothing under it is the answer to "why is my tool not found"."""
        (project / ".arctic").mkdir()
        assert "(nothing)" in atf("--workspace", str(project), "paths").out


class TestNamingAFlow:
    def test_a_name_goes_through_the_lookup(self, project: Path, atf: Runner) -> None:
        make.write_tool(project, "greet", script=make.prints("found by name"))
        one_step(project, "greet")
        assert atf("--workspace", str(project), "run", "probe").out == "found by name\n"

    def test_a_path_outside_the_search_roots_still_runs(
        self, project: Path, tmp_path: Path, atf: Runner
    ) -> None:
        """Which keeps an ad-hoc flow usable without installing it anywhere."""
        make.write_tool(project, "greet", script=make.prints("found by path"))
        scratch = tmp_path / "scratch.yaml"
        scratch.write_text(
            "flow: scratch\nstart: a\nsteps:\n  - id: a\n    tool: greet\n"
            "output:\n  template: '{{ steps.a.text }}'\n"
        )
        assert atf("--workspace", str(project), "run", str(scratch)).out == "found by path\n"
