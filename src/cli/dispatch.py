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
from cli import mcp_server, render
from cli.complete import candidates, snippet
from cli.output import flow_output
from cli.progress import Progress
from engine.executor import FlowError
from paths.resolver import Paths
from vault.vault import VaultError, resolve_password

# What `inspect -o` takes: the flow's graph as text, or as the Mermaid document. Declared
# here rather than in `app.py` because the handler below is what reads the value, and
# `app.py` already imports this module. The reverse import would close the circle.
GRAPH_TEXT, GRAPH_MERMAID = "raw", "md"
GRAPH_FORMATS = (GRAPH_TEXT, GRAPH_MERMAID)

# `lint .` means every flow, the same as naming none. Spelled the way other linters spell
# it, and unambiguous here because flows are named rather than pathed: `.` is not a flow
# name the lookup could ever return, and `resolve_flow` would only ever refuse it.
EVERYTHING = "."


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
    """One flow, or every flow in scope when none is named.

    The sweep returns its own exit code rather than raising, because it has already
    checked the flows after the one that failed and the report is the point. A single
    flow still raises, so `lint one_flow` reports its problem the way every other
    command reports one.
    """
    if args.flow in (None, EVERYTHING):
        report = commands.lint_all(paths)
        print(render.lint_report(report))
        return 0 if report.ok else 1

    print(render.lint(commands.lint(args.flow, paths)))
    return 0


def inspect_adapter(args: argparse.Namespace, paths: Paths) -> int:
    print(render.adapter_detail(commands.adapter_detail(args.name, paths)))
    return 0


def inspect_agent(args: argparse.Namespace, paths: Paths) -> int:
    print(render.agent_detail(commands.agent_detail(args.name, paths)))
    return 0


def inspect_tool(args: argparse.Namespace, paths: Paths) -> int:
    print(render.tool_detail(commands.tool_detail(args.name, paths)))
    return 0


def inspect_flow(args: argparse.Namespace, paths: Paths) -> int:
    if args.output == GRAPH_MERMAID:
        # end="" because the markdown carries its own final newline, and a document is not
        # a message: `> flow.md` has to hold what a file of it would.
        print(commands.diagram(args.flow, paths).markdown, end="")
    else:
        print(commands.graph(args.flow, paths).text)
    return 0


def create(args: argparse.Namespace, paths: Paths) -> int:
    """Scaffold one component. stdout, because what it prints is the command's result."""
    print(render.component_created(commands.create(args.create_kind, args.name, paths)))
    return 0


def mcp_serve(args: argparse.Namespace, paths: Paths) -> int:
    """Serve the named tools to an agent's turn, until stdin closes.

    The one handler that neither prints nor frames anything: its stdout is a protocol. See
    `cli.mcp_server`.
    """
    return mcp_server.serve(args.tools, paths, args.events)


# --------------------------------------------------------------------------- #
# the installation
# --------------------------------------------------------------------------- #


def list_components(args: argparse.Namespace, paths: Paths) -> int:
    print(render.inventory(commands.inventory(paths)))
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


# --------------------------------------------------------------------------- #
# the shell
# --------------------------------------------------------------------------- #


def completion(args: argparse.Namespace, paths: Paths) -> int:
    """Print the snippet behind `eval "$(atf completion bash)"`.

    end="" because the snippet carries its own final newline. It is a file being reproduced,
    not a message, so what is printed is what the file holds.
    """
    print(snippet(args.shell), end="")
    return 0


def complete(args: argparse.Namespace, paths: Paths) -> int:
    """Candidate words for the shell, one per line, and never anything else.

    Two rules, both about where this output lands. Every failure is answered with no
    candidates: a traceback here would be painted over a command line someone is still
    typing, and the shell's own filename completion is a better answer than that. The except
    is broad on purpose, because an unreadable search root raising OSError is not worth
    telling apart from anything else here.

    And nothing at all is printed when there are no candidates. A blank line would reach
    bash as one empty candidate, which `complete -o default` reads as an answer and stops it
    falling back to filenames.
    """
    try:
        words = candidates(args.words, paths.workspace)
    except Exception:
        return 0
    for word in words:
        print(word)
    return 0
