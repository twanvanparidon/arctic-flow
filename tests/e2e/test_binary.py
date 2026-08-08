"""The artefact is what it claims to be.

A frozen build can fail in ways a checkout cannot, and every one of them is silent. Package
data that was never collected leaves the built-in search layer empty. An adapter resolved by
name rather than imported is missing from the registry. A version stamp that ran after the
install writes a source tree nothing reads again, and the binary reports the placeholder.
Each of those produces a binary that starts, prints help, and is wrong.

So these are the cheap questions asked of the thing that ships, rather than of the thing it
was built from. `packaging/Dockerfile.build` asks several of them too, and deliberately: it
fails before an artefact exists, which is earlier than any test can. What it cannot do is
run on a laptop, skip when `jq` is absent, or say which assertion failed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support.outcome import Runner

from .conftest import VERSION_PREFIX, reported_version

BUILT_IN_TOOLS = (
    "common/read_file",
    "common/write_file",
    "common/glob",
    "common/grep",
    "common/fetch_url",
)
SHIPPED_ADAPTERS = ("claude_code", "echo")

# Every subcommand, so one that was added without reaching the bundle is caught here rather
# than by whoever runs it first. `mcp-serve` is the one that matters: it is spawned by a
# runtime rather than typed, so a build that could not run it would look like a tool that
# does not work.
COMMANDS = (
    ("run",),
    ("lint",),
    ("list",),
    ("inspect",),
    ("inspect", "flow"),
    ("inspect", "agent"),
    ("inspect", "adapter"),
    ("inspect", "tool"),
    ("create",),
    ("create", "flow"),
    ("create", "agent"),
    ("create", "tool"),
    ("completion",),
    ("vault",),
    ("mcp-serve",),
)

# The kinds `create` writes, and the file each one has to arrive with. A scaffold is package
# data like the built-in tools, so it is one more thing that can be left out of the bundle.
SCAFFOLDED = (
    ("flow", "review", "flows/review.yaml"),
    ("agent", "reviewer", "agents/reviewer/agent.md"),
    ("tool", "shout", "tools/shout/run.sh"),
)


class TestIdentity:
    def test_the_version_is_one_line_on_a_pipe(self, atf: Runner) -> None:
        """`release.sh` reads the number back out of it, so the shape is the decision."""
        result = atf("--version")
        assert result.code == 0
        assert result.out.startswith(VERSION_PREFIX)
        assert len(result.out.splitlines()) == 1

    def test_the_version_is_the_one_the_tag_promised(
        self, atf: Runner, expected_version: str | None
    ) -> None:
        """The stamp is the step that can silently not happen: run after `pip install` it
        writes a source tree nothing reads again, and every other check still passes."""
        if expected_version is None:
            pytest.skip("no $ATF_EXPECTED_VERSION: not a release build")
        assert atf("--version").out == f"{VERSION_PREFIX}{expected_version}\n"

    def test_the_placeholder_is_not_a_plausible_release(self, atf: Runner) -> None:
        """An unstamped build has to be obvious rather than look like 0.1.0."""
        version = reported_version(atf("--version").out)
        assert version == "0.0.0.dev0" or not version.endswith(".dev0")


class TestWhatTheBundleCarries:
    @pytest.mark.parametrize("tool", BUILT_IN_TOOLS)
    def test_each_built_in_tool_came_along(self, atf: Runner, tool: str) -> None:
        """They are package data, not code. Without their entry in pyproject.toml the
        analysis pass drops them and the built-in search layer comes up empty."""
        result = atf("list")
        assert result.code == 0
        assert tool in result.out

    @pytest.mark.parametrize("command", COMMANDS)
    def test_each_command_is_reachable(self, atf: Runner, command: tuple[str, ...]) -> None:
        """`--help` on each, which parses the subcommand without doing anything. A command
        whose module is only reached lazily would be missing from the bundle."""
        assert atf(*command, "--help").code == 0

    @pytest.mark.parametrize("name", SHIPPED_ADAPTERS)
    def test_each_adapter_is_in_the_frozen_registry(self, atf: Runner, name: str) -> None:
        """`ADAPTERS` is static imports for this reason: a frozen build misses anything
        resolved by name, and would say `unknown adapter` at the first agent step."""
        assert name in atf("list").out

    @pytest.mark.parametrize(("kind", "name", "written"), SCAFFOLDED)
    def test_each_scaffold_came_along(
        self, atf: Runner, tmp_path: Path, kind: str, name: str, written: str
    ) -> None:
        """`create` copies out of package data the same way the built-in tools are read.
        Dropped from the bundle, every one of these fails at the first thing a new user
        types, and nothing before this notices."""
        result = atf("--workspace", str(tmp_path), "create", kind, name)
        assert result.code == 0, result.err
        assert (tmp_path / written).is_file()

    def test_a_scaffolded_tool_arrives_executable(self, atf: Runner, tmp_path: Path) -> None:
        """The mode is set on the way out rather than carried from the bundle, which is the
        only arrangement that survives however PyInstaller collected the file."""
        assert atf("--workspace", str(tmp_path), "create", "tool", "shout").code == 0
        assert os.access(tmp_path / "tools" / "shout" / "run.sh", os.X_OK)

    def test_what_ships_with_the_engine_is_named_as_the_engines_own(
        self, atf: Runner, binary: Path, tmp_path: Path
    ) -> None:
        """`builtin_root()` resolves against its own module, which sits somewhere else once
        frozen, and `engine_root()` one directory above it. Both landing inside the bundle
        is what makes a shipped tool print as `$ATF_ROOT/...`: either one resolving outside
        it falls through to an absolute path here, and the adapter half is only reachable
        in a frozen build, where a module's `__file__` is a path into the bundle.

        The workspace is elsewhere on purpose, so nothing shortens to `./x` by accident.
        """
        printed = atf("--workspace", str(tmp_path), "list").out
        assert "$ATF_ROOT/tools/common/read_file" in printed, printed
        assert "$ATF_ROOT/adapters/" in printed, printed
        assert str(binary.parent) not in printed


class TestTheInterface:
    def test_nothing_after_the_command_prints_help_and_succeeds(self, atf: Runner) -> None:
        result = atf()
        assert result.code == 0
        assert "usage: atf" in result.out

    def test_help_succeeds(self, atf: Runner) -> None:
        assert atf("--help").code == 0

    def test_help_into_a_pipe_carries_no_banner(self, atf: Runner) -> None:
        """The half a pipe can ask. The terminal half is in test_terminal.py."""
        assert "A R C T I C" not in atf("--help").out

    def test_a_usage_mistake_is_argparses_two(self, atf: Runner) -> None:
        assert atf("--nonsense").code == 2

    def test_an_expected_failure_is_one_line_and_no_traceback(
        self, atf: Runner, tmp_path: Path
    ) -> None:
        """`EXPECTED_ERRORS` is caught in `cli.app`. If the frozen build somehow raised past
        it, a user would meet a Python traceback instead of a sentence."""
        result = atf("--workspace", str(tmp_path), "run", "absent")
        assert result.code == 1
        assert result.out == ""
        assert result.err.startswith("engine: ")
        assert "Traceback" not in result.err

    def test_a_flow_that_does_not_exist_says_where_it_looked(
        self, atf: Runner, tmp_path: Path
    ) -> None:
        assert "unknown flow 'absent'" in atf("--workspace", str(tmp_path), "run", "absent").err


class TestCompletion:
    def test_the_snippet_is_printed(self, atf: Runner) -> None:
        result = atf("completion", "bash")
        assert result.code == 0
        assert "_atf_complete" in result.out

    def test_an_unknown_shell_is_refused(self, atf: Runner) -> None:
        assert atf("completion", "fish").code == 2

    def test_what_the_snippet_calls_resolves_a_flow_name(self, atf: Runner, examples: Path) -> None:
        """The completion snippet is package data too, and `__complete` reads the lookup, so
        this covers both halves reaching each other inside the bundle."""
        result = atf("--workspace", str(examples / "sign-release"), "__complete", "--", "run", "")
        assert result.out.splitlines() == ["sign_release"]

    def test_no_candidates_prints_nothing_at_all(self, atf: Runner, tmp_path: Path) -> None:
        """Not a blank line: the shell would offer it as a completion."""
        result = atf("--workspace", str(tmp_path), "__complete", "--", "run", "")
        assert result.out == ""

    def test_a_broken_command_line_never_shows_a_traceback(self, atf: Runner) -> None:
        """It runs while someone is typing. There is no good moment for a stack trace."""
        result = atf("__complete", "--", "--workspace")
        assert result.code == 0
        assert "Traceback" not in result.err
