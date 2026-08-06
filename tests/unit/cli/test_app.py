"""The shape of the interface: which commands exist, which flags they take, and the one
place a failure turns into an exit code.

The flag names and the exit codes are a contract. Scripts are written against them, so a
rename is a breaking change and these tests are where it shows up as one.

The help colouring is applied to argparse's finished text rather than through a formatter,
because a formatter returning coloured strings breaks argparse's column arithmetic. So it
is a string transformation, and it is tested as one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cli import colour
from cli.app import (
    build_parser,
    colourise_help,
    main,
    paint_invocation,
)
from support import components as make

PAINT = colour.Painter(on=True)


class TestTheCommandsThatExist:
    @pytest.mark.parametrize("command", ["run", "lint", "graph", "diagram", "list", "paths"])
    def test_each_top_level_command_parses(self, command: str) -> None:
        argv = [command, "demo"] if command in ("run", "lint", "graph", "diagram") else [command]
        assert build_parser().parse_args(argv).handler is not None

    @pytest.mark.parametrize("action", ["create", "set", "list", "view"])
    def test_each_vault_action_parses(self, action: str) -> None:
        argv = ["vault", action, "secrets.vault"]
        if action == "set":
            argv.append("token")
        assert build_parser().parse_args(argv).handler is not None

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit) as caught:
            build_parser().parse_args([])
        assert caught.value.code == 2

    def test_a_vault_action_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["vault"])


class TestRunFlags:
    def test_inputs_accumulate(self) -> None:
        args = build_parser().parse_args(["run", "demo", "--input", "a=1", "--input", "b=2"])
        assert args.input == ["a=1", "b=2"]

    def test_the_defaults_are_quiet_off_and_trace_off(self) -> None:
        args = build_parser().parse_args(["run", "demo"])
        assert (args.input, args.trace, args.quiet, args.vault) == ([], False, False, None)

    def test_quiet_has_a_short_form(self) -> None:
        assert build_parser().parse_args(["run", "demo", "-q"]).quiet is True

    def test_a_password_typed_as_a_flag_is_read_as_a_filename(self) -> None:
        """There is deliberately no --vault-password, but argparse accepts any unambiguous
        prefix, so `--vault-password s3cret` reaches --vault-password-file and fails with
        "cannot read a password from s3cret". Pinned because it is surprising, not because
        it is wanted: closing it means allow_abbrev=False, which drops every abbreviation."""
        args = build_parser().parse_args(["run", "demo", "--vault-password", "s3cret"])
        assert args.vault_password_file == Path("s3cret")
        assert not hasattr(args, "vault_password")

    def test_the_password_file_flag_is_a_path(self, tmp_path: Path) -> None:
        args = build_parser().parse_args(["run", "demo", "--vault-password-file", str(tmp_path)])
        assert args.vault_password_file == tmp_path

    def test_the_workspace_defaults_to_the_current_directory(self) -> None:
        assert build_parser().parse_args(["list"]).workspace == Path.cwd()

    def test_diagram_takes_an_output_file(self, tmp_path: Path) -> None:
        args = build_parser().parse_args(["diagram", "demo", "-o", str(tmp_path / "d.md")])
        assert args.out == tmp_path / "d.md"


class TestExitCodes:
    def test_a_bare_invocation_prints_help_and_succeeds(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Someone typing the command with nothing after it is asking what it does."""
        assert main([]) == 0
        assert "usage: atf" in capsys.readouterr().out

    def test_version_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as caught:
            main(["--version"])
        assert caught.value.code == 0
        assert capsys.readouterr().out.strip() == "atf 0.1.0"

    def test_an_expected_failure_is_one_line_on_stderr_and_exit_one(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--workspace", str(workspace), "lint", "absent"]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("engine: unknown flow 'absent'")
        assert captured.err.count("\n") == 1

    def test_a_working_command_exits_zero(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        make.write_tool(workspace, "emit", script=make.prints("x"))
        make.write_flow(
            workspace,
            "demo",
            {"flow": "demo", "start": "a", "steps": [{"id": "a", "tool": "emit"}]},
        )
        assert main(["--workspace", str(workspace), "lint", "demo"]) == 0
        assert "ok, 1 step, no issues found" in capsys.readouterr().out

    def test_an_unknown_command_is_argparse_saying_two(self) -> None:
        with pytest.raises(SystemExit) as caught:
            main(["invented"])
        assert caught.value.code == 2


class TestHelpColouring:
    def test_a_plain_stream_gets_plain_text(self) -> None:
        text = "usage: atf [-h]\n\noptions:\n  -h, --help  show this\n"
        assert colourise_help(text, colour.Painter(on=False), "atf") == text

    def test_the_program_name_is_painted_once_in_the_usage_line(self) -> None:
        painted = colourise_help("usage: atf run atf\n", PAINT, "atf")
        assert painted.count("\033[36matf\033[0m") == 1

    def test_a_heading_is_bold(self) -> None:
        assert colourise_help("options:", PAINT, "atf") == "\033[1moptions:\033[0m"

    def test_a_flag_in_an_entry_is_green(self) -> None:
        painted = colourise_help("options:\n  -q, --quiet  no progress\n", PAINT, "atf")
        assert "\033[32m--quiet\033[0m" in painted

    def test_the_help_text_beside_a_flag_is_left_alone(self) -> None:
        painted = colourise_help("options:\n  -q, --quiet  no progress", PAINT, "atf")
        assert painted.endswith("\033[0m  no progress")

    def test_wrapped_help_text_is_not_painted_as_if_it_were_a_name(self) -> None:
        """It starts far to the right, past the entry indent, so the indent is the test."""
        text = "options:\n  --vault FILE  first line\n" + " " * 20 + "--second line\n"
        assert "\033[32m--second" not in colourise_help(text, PAINT, "atf")

    def test_a_metavar_recedes_and_the_flag_does_not(self) -> None:
        """A flag is something you type; a metavar is a placeholder for something you supply."""
        assert paint_invocation("--vault FILE", PAINT) == (
            "\033[32m--vault\033[0m \033[2mFILE\033[0m"
        )

    def test_a_subcommand_name_is_paintable_too(self) -> None:
        assert paint_invocation("run", PAINT) == "\033[32mrun\033[0m"

    def test_a_comma_stays_outside_the_colour(self) -> None:
        """Otherwise the reset lands after the comma and the separator is coloured."""
        assert paint_invocation("-q, --quiet", PAINT) == (
            "\033[32m-q\033[0m, \033[32m--quiet\033[0m"
        )

    def test_double_spacing_inside_an_invocation_survives(self) -> None:
        """argparse pads some entries, and an empty token is not a name to paint."""
        assert paint_invocation("run  DIR", PAINT) == "\033[32mrun\033[0m  \033[2mDIR\033[0m"

    def test_a_usage_line_that_wrapped_is_still_painted(self) -> None:
        """Usage runs to several lines on a narrow terminal, and the flags are on all of them."""
        painted = colourise_help("usage: atf run\n             [--trace]", PAINT, "atf")
        assert "\033[32m--trace\033[0m" in painted


class TestHelpOutput:
    def test_help_into_a_pipe_carries_no_escape_sequences(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Python 3.14 colours argparse help itself, which made the CLI's appearance a
        property of the interpreter. Turned off, and painted here instead."""
        main([])
        assert "\033[" not in capsys.readouterr().out

    def test_help_into_a_pipe_carries_no_banner(self, capsys: pytest.CaptureFixture[str]) -> None:
        main([])
        assert "A R C T I C" not in capsys.readouterr().out

    def test_every_command_is_listed(self, capsys: pytest.CaptureFixture[str]) -> None:
        main([])
        out = capsys.readouterr().out
        assert all(command in out for command in ("run", "lint", "graph", "diagram", "vault"))
