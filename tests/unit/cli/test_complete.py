"""Candidates for a half-typed command line.

Two of these are about argparse rather than about completion. `_flags` and `_subcommands`
read the action list, which is private, so they are tested directly: a Python release moving
it fails here instead of silently completing nothing.

The flows are real files under a real workspace, resolved through the real lookup, because
what completion has to get right is precedence and scope rather than string matching.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.app import build_parser
from cli.complete import (
    SHELLS,
    _flags,
    _reached,
    _subcommands,
    _workspace,
    candidates,
    snippet,
)
from support import components as make


def write_flow(root: Path, name: str) -> None:
    make.write_flow(root, name, {"flow": name, "start": "a", "steps": [{"id": "a", "tool": "x"}]})


class TestCommands:
    def test_the_first_word_offers_every_command(self, workspace: Path) -> None:
        offered = candidates([""], workspace)
        assert all(command in offered for command in ("run", "lint", "inspect", "vault"))

    def test_a_prefix_narrows_it(self, workspace: Path) -> None:
        assert candidates(["li"], workspace) == ["lint", "list"]

    @pytest.mark.parametrize(("command", "prefix"), [("__complete", "__"), ("completion", "comp")])
    def test_a_command_nobody_types_at_a_prompt_is_not_offered(
        self, command: str, prefix: str, workspace: Path
    ) -> None:
        """`__complete` is what the snippet types. `completion` goes into a startup file
        once. Both are in the parser and both still run; neither is an answer to a TAB."""
        assert command not in candidates([""], workspace)
        assert candidates([prefix], workspace) == []

    def test_a_command_not_offered_is_still_walked_into(self, workspace: Path) -> None:
        """Leaving it out of the candidates must not leave it out of the walk, or the words
        after it would be answered as if no command had been named at all."""
        assert candidates(["completion", ""], workspace) == []

    def test_nothing_typed_at_all_is_nothing_to_answer(self, workspace: Path) -> None:
        assert candidates([], workspace) == []

    def test_vault_offers_its_actions(self, workspace: Path) -> None:
        assert candidates(["vault", ""], workspace) == ["create", "list", "set", "view"]

    def test_a_vault_action_takes_a_file_and_offers_nothing(self, workspace: Path) -> None:
        """A file is the shell's own completion, and it does it better than a list here."""
        assert candidates(["vault", "set", ""], workspace) == []


class TestFlags:
    def test_a_dash_offers_the_flags_of_the_command_reached(self, workspace: Path) -> None:
        offered = candidates(["run", "-"], workspace)
        assert all(flag in offered for flag in ("--input", "--trace", "-q", "--vault"))

    def test_a_dash_before_any_command_offers_the_top_level_flags(self, workspace: Path) -> None:
        assert "--workspace" in candidates(["--"], workspace)

    def test_the_flags_offered_are_the_deepest_ones(self, workspace: Path) -> None:
        """`--vault-password-file` is on `vault set`, not on `vault`."""
        assert "--vault-password-file" in candidates(["vault", "set", "-"], workspace)
        assert "--vault-password-file" not in candidates(["vault", "-"], workspace)

    def test_a_command_flag_is_not_offered_before_its_command(self, workspace: Path) -> None:
        assert "--trace" not in candidates(["-"], workspace)


class TestComponentNames:
    def test_a_flow_command_offers_the_flows_in_scope(self, workspace: Path) -> None:
        write_flow(workspace, "release")
        write_flow(workspace, "review")
        assert candidates(["run", ""], workspace) == ["release", "review"]

    @pytest.mark.parametrize("command", [["run"], ["lint"], ["inspect", "flow"]])
    def test_every_command_taking_a_flow_offers_them(
        self, command: list[str], workspace: Path
    ) -> None:
        """One missing from NAME_COMMANDS offers filenames instead, which looks like the
        shell working rather than like completion being wrong."""
        write_flow(workspace, "release")
        assert candidates([*command, ""], workspace) == ["release"]

    def test_inspect_offers_its_kinds(self, workspace: Path) -> None:
        assert candidates(["inspect", ""], workspace) == ["adapter", "agent", "flow", "tool"]

    def test_a_kind_is_answered_with_that_kind_and_not_with_flows(self, workspace: Path) -> None:
        """`inspect agent <TAB>` asks about agents. Answering it out of the flow lookup is
        the mistake one shared list of "commands taking a name" would make."""
        write_flow(workspace, "release")
        make.write_agent(workspace, "writer")
        make.write_tool(workspace, "greet")
        assert candidates(["inspect", "agent", ""], workspace) == ["writer"]
        # The built-in tools are in scope from anywhere, so this half is containment.
        offered = candidates(["inspect", "tool", ""], workspace)
        assert "greet" in offered
        assert "release" not in offered and "writer" not in offered

    def test_a_command_taking_no_flow_offers_none(self, workspace: Path) -> None:
        write_flow(workspace, "release")
        assert candidates(["list", ""], workspace) == []

    def test_a_flow_already_named_is_not_asked_about_again(self, workspace: Path) -> None:
        write_flow(workspace, "release")
        assert candidates(["run", "release", ""], workspace) == []

    def test_a_flag_that_takes_no_value_leaves_the_flow_slot_open(self, workspace: Path) -> None:
        write_flow(workspace, "release")
        assert candidates(["run", "--trace", ""], workspace) == ["release"]

    def test_a_flag_value_does_not_fill_the_flow_slot(self, workspace: Path) -> None:
        """`--vault secrets.vault` is two words and neither of them is the flow."""
        write_flow(workspace, "release")
        assert candidates(["run", "--vault", "secrets.vault", ""], workspace) == ["release"]

    def test_a_flow_installed_at_home_is_in_scope_too(self, workspace: Path, home: Path) -> None:
        """The lookup's own layering, not a second list: `~/.arctic` is a search root."""
        write_flow(home / ".arctic", "personal")
        assert candidates(["run", ""], workspace) == ["personal"]

    def test_a_shadowed_flow_is_offered_once(self, workspace: Path, home: Path) -> None:
        write_flow(workspace, "release")
        write_flow(home / ".arctic", "release")
        assert candidates(["run", ""], workspace) == ["release"]


class TestTheWorkspaceOnTheLine:
    def test_it_decides_which_flows_are_in_scope(self, workspace: Path, tmp_path: Path) -> None:
        other = tmp_path / "other"
        write_flow(other, "elsewhere")
        assert candidates(["--workspace", str(other), "run", ""], workspace) == ["elsewhere"]

    def test_the_joined_spelling_works_the_same(self, workspace: Path, tmp_path: Path) -> None:
        other = tmp_path / "other"
        write_flow(other, "elsewhere")
        assert candidates([f"--workspace={other}", "run", ""], workspace) == ["elsewhere"]

    def test_the_last_one_wins_as_argparse_reads_it(self, workspace: Path, tmp_path: Path) -> None:
        first, second = tmp_path / "first", tmp_path / "second"
        write_flow(first, "one")
        write_flow(second, "two")
        words = ["--workspace", str(first), "--workspace", str(second), "run", ""]
        assert candidates(words, workspace) == ["two"]

    def test_a_workspace_with_no_value_yet_falls_back(self, workspace: Path) -> None:
        """Mid-word, the flag is there and its value is not. The shell is still somewhere."""
        assert _workspace(["--workspace"], workspace) == workspace

    def test_a_workspace_that_is_not_there_answers_with_nothing(self, workspace: Path) -> None:
        assert candidates(["--workspace", "/no/such/project", "run", ""], workspace) == []


class TestReadingTheParser:
    def test_a_flag_taking_a_value_is_told_from_one_that_does_not(self) -> None:
        flags = _flags(build_parser())
        assert flags["--workspace"] is True
        assert flags["--version"] is False

    def test_the_subcommands_are_the_commands(self) -> None:
        assert "run" in _subcommands(build_parser())

    def test_a_leaf_command_has_none(self) -> None:
        assert _subcommands(_subcommands(build_parser())["run"]) == {}

    def test_the_walk_names_the_command_it_reached(self) -> None:
        _, command, arguments = _reached(build_parser(), ["vault", "set", "secrets.vault"])
        assert (command, arguments) == ("set", ["secrets.vault"])


class TestTheSnippet:
    @pytest.mark.parametrize("shell", SHELLS)
    def test_every_shell_named_has_one(self, shell: str) -> None:
        """SHELLS is what `atf completion` offers, so a name without a file is a broken flag."""
        assert snippet(shell).strip() != ""

    def test_it_registers_the_function_it_defines(self) -> None:
        """The two halves of the file have to agree on the name, and nothing else checks it."""
        text = snippet("bash")
        assert "_atf_complete()" in text
        assert "complete -F _atf_complete" in text

    def test_it_asks_the_command_being_completed_rather_than_a_name(self) -> None:
        """bash passes that command as $1. Calling it is what lets a build at a path be
        registered and answer for itself, instead of an `atf` somewhere else on PATH."""
        assert '"$1" __complete --' in snippet("bash")
