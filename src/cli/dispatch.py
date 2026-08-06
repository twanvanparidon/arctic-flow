"""From parsed arguments to a command call, and from its result to the terminal.

The seam between `app.py` and `commands/`, and the only file that knows both. Every
function takes a `Namespace` and a `Paths` and returns an exit code, because that is what
argparse's `set_defaults(handler=...)` dispatches to.

Three things live here rather than in `commands/`, all true of a terminal and not of the
engine:

- **Which stream.** Output to stdout, frame and progress and trace to stderr, so
  `atf run … > file` produces the flow's result alone.
- **Where a value comes from.** `--input KEY=VALUE` is a syntax; stdin-or-prompt is a
  policy. A command takes a mapping and does not care how it was obtained.
- **When to ask.** `vault create` hands over a callable so a mistyped filename is reported
  before anything prompts. `vault set` resolves first, so its two prompts arrive in the
  order a person expects.

A second front end reimplements this file and imports everything else unchanged.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import yaml

import commands
from cli import render
from cli.output import flow_output
from cli.progress import Progress
from engine.executor import FlowError
from paths.resolver import Paths
from vault.vault import VaultError, resolve_password


def parse_input_pairs(pairs: list[str]) -> dict[str, str]:
    """`--input KEY=VALUE`, repeated, as a mapping. A repeated key takes its last value.

    Parsed here because the syntax is the command line's. A front end with a form already
    holds a mapping and should not build strings for this layer to take apart.
    """
    if any("=" not in pair for pair in pairs):
        raise FlowError("--input expects KEY=VALUE")
    return dict(pair.split("=", 1) for pair in pairs)


def password_provider(args: argparse.Namespace) -> commands.PasswordProvider:
    """A callable that resolves the vault password when, and if, it is needed."""
    return lambda: resolve_password(getattr(args, "vault_password_file", None))


def stdin_text() -> str:
    """Everything on stdin, or nothing when there is a person there instead.

    A terminal is not read from: a command whose piped input is optional would hang,
    looking frozen rather than waiting.
    """
    return sys.stdin.read() if not sys.stdin.isatty() else ""


def secret_value(name: str) -> str:
    """One secret's value: everything piped in, or a prompt when there is a terminal.

    Never a flag. A `--value` lands in shell history and the process list, which is most
    of what a vault exists to avoid.
    """
    if sys.stdin.isatty():
        return getpass.getpass(f"Value for {name}: ")
    # Trailing newlines go: `echo key | …` and `printf key | …` have to store the same
    # secret, and a credential with a newline on the end fails far from here.
    return sys.stdin.read().rstrip("\n")


# --------------------------------------------------------------------------- #
# flows
# --------------------------------------------------------------------------- #


def run(args: argparse.Namespace, paths: Paths) -> int:
    # Prepared before the progress display exists, so a bad input or a locked vault is
    # reported as itself instead of arriving under a spinner with a "failed after 0ms"
    # over the top of it.
    plan = commands.prepare(
        args.flow,
        paths,
        parse_input_pairs(args.input),
        vault_ref=args.vault,
        password=password_provider(args),
    )

    # Progress goes to stderr; the flow's output is stdout and stays pipeable. The
    # reporter is handed to the command as an observer, so nothing below this layer
    # formats anything.
    with Progress(enabled=not args.quiet) as progress:
        try:
            result = commands.run(plan, on_event=progress)
        except BaseException:
            progress.summary(ok=False)
            raise
        progress.summary()

    # The frame is stderr; the output itself goes to stdout untouched. --quiet drops the
    # frame with the rest of the reporting. It asks for the flow's output and nothing else.
    flow_output(result.output, label=result.flow, frame=not args.quiet)

    if args.trace:
        # stderr, so `run` stays pipeable into whatever consumes the output.
        print(render.trace(result), file=sys.stderr)
    return 0


def lint(args: argparse.Namespace, paths: Paths) -> int:
    print(render.lint(commands.lint(args.flow, paths)))
    return 0


def graph(args: argparse.Namespace, paths: Paths) -> int:
    print(commands.graph(args.flow, paths).text)
    return 0


def diagram(args: argparse.Namespace, paths: Paths) -> int:
    result = commands.diagram(args.flow, paths, args.out)
    if result.written_to:
        print(f"wrote {result.written_to}")
    else:
        # end="" because the markdown carries its own final newline, and a document is not
        # a message: what is printed here should be byte-for-byte what --out would write.
        print(result.markdown, end="")
    return 0


# --------------------------------------------------------------------------- #
# the installation
# --------------------------------------------------------------------------- #


def list_components(args: argparse.Namespace, paths: Paths) -> int:
    print(render.inventory(commands.inventory(paths)))
    return 0


def show_paths(args: argparse.Namespace, paths: Paths) -> int:
    print(render.search_paths(commands.search_paths(paths)))
    return 0


# --------------------------------------------------------------------------- #
# vault
# --------------------------------------------------------------------------- #


def vault_create(args: argparse.Namespace, paths: Paths) -> int:
    """Create a vault from a YAML mapping on stdin."""
    raw = stdin_text()
    loaded = yaml.safe_load(raw) if raw.strip() else {}
    if loaded is not None and not isinstance(loaded, dict):
        raise VaultError("expected a YAML mapping of name to value on stdin")

    result = commands.create_vault(
        Path(args.file),
        paths,
        loaded or {},
        # Lazy: `create_vault` refuses an existing file before it asks for anything.
        password_provider(args),
        force=args.force,
    )
    print(render.vault_created(result))
    return 0


def vault_set(args: argparse.Namespace, paths: Paths) -> int:
    """Add or replace one secret, reading the value from stdin or a prompt."""
    # Resolved before the value is read, so the password prompt comes first. That is the
    # order someone typing two secrets in a row is expecting.
    password = resolve_password(args.vault_password_file)
    result = commands.set_secret(
        Path(args.file), paths, args.name, secret_value(args.name), password
    )
    print(render.secret_set(result))
    return 0


def vault_list(args: argparse.Namespace, paths: Paths) -> int:
    """Names only: the one command safe to run in front of other people."""
    result = commands.secret_names(Path(args.file), paths, password_provider(args))
    print(render.secret_names(result))
    return 0


def vault_view(args: argparse.Namespace, paths: Paths) -> int:
    """Decrypt to stdout. This prints secrets, which is the point of it."""
    result = commands.vault_contents(Path(args.file), paths, password_provider(args))
    print(render.vault_contents(result))
    return 0
