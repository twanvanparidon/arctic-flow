"""The command-line interface: arguments, help, dispatch, and how errors read.

The shape of the interface lives here: which commands exist, what their help says, which
flags they take, and the single place a failure turns into an exit code. What the commands
*do* is `commands/`, called by `cli/dispatch.py`.

Help text is written here rather than beside the commands on purpose. It documents flags,
streams and pipes: things a command line has and another front end does not.
"""

from __future__ import annotations

import argparse
import inspect
import re
import sys
from pathlib import Path

import commands
from cli import branding, colour, dispatch
from engine.executor import VARIABLE_PREFIX
from paths.resolver import Paths
from vault.vault import PASSWORD_ENV, PASSWORD_FILE_ENV

# Python 3.14 colours argparse help itself, and nothing before it does. That made the CLI's
# appearance a property of the interpreter: coloured from a checkout on 3.14, plain from the
# compiled binary, which embeds 3.13. Turning it off and painting the help ourselves puts
# every install on the same footing and uses the same five colours as the rest of the CLI.
ARGPARSE_COLOURS = "color" in inspect.signature(argparse.ArgumentParser).parameters

DESCRIPTION = """\
Run agentic workflows. A flow is a graph of tool and agent steps: each step declares
where it pushes its result next, so the engine reads forwards.

Flows, tools, agents and adapters are named, not pathed. Names resolve
working-directory-first, so a project overrides what it inherits. `paths` shows the
order, `list` shows what wins.
"""

PROG = branding.COMMAND

FLOW_HELP = "flow name (resolved through the lookup) or a path to a .yaml file"


# A section heading argparse emits: "options:", "positional arguments:".
HEADING = re.compile(r"^[a-z][a-z ]*:$")

# A flag, anywhere. Written to need a leading dash so it cannot match inside an escape
# sequence we have already inserted.
FLAG = re.compile(r"--?[A-Za-z][\w-]*")

# Entries sit at indent 2, or 4 inside the commands section. Their wrapped help text starts
# at argparse's help column, far to the right, and must not be painted as if it were a name.
ENTRY_INDENT = 8

USAGE = "usage: "


def paint_invocation(text: str, paint: colour.Painter) -> str:
    """`-q, --quiet` or `--vault FILE` or `run`: the left-hand side of a help entry.

    Flags and the entry's own name are things you can type, so they are green. A metavar
    is a placeholder for something you supply, so it recedes.
    """
    painted = []
    for index, token in enumerate(text.split(" ")):
        bare, comma = (token[:-1], ",") if token.endswith(",") else (token, "")
        if not bare:
            painted.append(token)
        elif bare.startswith("-") or index == 0:
            painted.append(paint(bare, "green") + comma)
        else:
            painted.append(paint(bare, "dim") + comma)
    return " ".join(painted)


def colourise_help(text: str, paint: colour.Painter, prog: str) -> str:
    """Colour argparse's rendered help.

    Applied to the finished text rather than through a HelpFormatter subclass. A formatter
    returning coloured strings breaks argparse's column arithmetic, which measures `len()`
    with escape sequences included. Painting afterwards cannot move anything.
    """
    if not paint.on:
        return text

    lines, in_usage, in_entries = [], False, False

    for line in text.split("\n"):
        if line.startswith(USAGE):
            in_usage, in_entries = True, False
            rest = line[len(USAGE) :].replace(prog, paint(prog, "cyan"), 1)
            lines.append(paint(USAGE.rstrip(), "bold") + " " + _paint_flags(rest, paint))
        elif not line.strip():
            in_usage = in_entries = False
            lines.append(line)
        elif in_usage:
            lines.append(_paint_flags(line, paint))
        elif HEADING.match(line):
            in_entries = True
            lines.append(paint(line, "bold"))
        elif in_entries and (indent := len(line) - len(line.lstrip())) <= ENTRY_INDENT:
            # Two or more spaces separate the invocation from its help text; an entry whose
            # invocation is too long to share the line has no help text on it at all.
            invocation, gap, help_text = line[indent:].partition("  ")
            lines.append(" " * indent + paint_invocation(invocation, paint) + gap + help_text)
        else:
            lines.append(line)

    return "\n".join(lines)


def _paint_flags(text: str, paint: colour.Painter) -> str:
    return FLAG.sub(lambda match: paint(match.group(), "green"), text)


class BrandedParser(argparse.ArgumentParser):
    """Puts the banner above help output, and colours the rest of it.

    Both happen in `format_help` rather than when the parser is built, so the terminal
    check happens as the text is written. `atf --help | cat` stays plain.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        if ARGPARSE_COLOURS:
            kwargs.setdefault("color", False)
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def format_help(self) -> str:
        return branding.banner(branding.__version__) + colourise_help(
            super().format_help(), colour.painter(sys.stdout), self.prog
        )


def build_parser() -> argparse.ArgumentParser:
    parser = BrandedParser(
        prog=PROG,
        description=DESCRIPTION,
        # Raw, so the description's paragraphs survive instead of being reflowed into one.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # -v is version here rather than verbose, as asked. Worth knowing if a --verbose is
    # ever wanted: it will need a different short flag.
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=branding.version_line(branding.__version__).rstrip("\n"),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="project root: the top search layer, and the directory components run in "
        "(default: current directory)",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    def add_password_flag(command: argparse.ArgumentParser) -> None:
        # No --vault-password flag on purpose: it would be recorded in shell history and
        # visible in the process list to anyone on the machine.
        command.add_argument(
            "--vault-password-file",
            type=Path,
            metavar="FILE",
            help=f"read the password from FILE; otherwise ${PASSWORD_ENV}, "
            f"${PASSWORD_FILE_ENV}, or a prompt",
        )

    def add(name: str, handler, help_text: str, epilog: str | None = None):
        command = sub.add_parser(
            name,
            help=help_text,
            description=help_text[0].upper() + help_text[1:] + ".",
            epilog=epilog,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        command.set_defaults(handler=handler)
        return command

    run = add(
        "run",
        dispatch.run,
        "execute a flow",
        "Inputs are declared by the flow; unknown or missing ones are rejected before\n"
        "anything runs. Each one also reads from the environment: input `depth` from\n"
        f"${VARIABLE_PREFIX}DEPTH. --input wins where both are set, and a variable named for\n"
        "an input the flow does not declare is ignored.\n\n"
        "Progress is written to stderr as steps start and finish, so the flow's own\n"
        "output on stdout stays pipeable. Use --quiet to silence it, or --trace to add\n"
        "a machine-readable summary at the end.\n",
    )
    run.add_argument("flow", help=FLOW_HELP)
    run.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=f"a flow input; repeat for several. Beats ${VARIABLE_PREFIX}KEY",
    )
    run.add_argument("--trace", action="store_true", help="write a per-step JSON trace to stderr")
    run.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="no live progress; only the flow's output",
    )
    run.add_argument(
        "--vault",
        metavar="FILE",
        help="encrypted secrets file; overrides a 'vault' set by the flow. Only opened "
        "if something needs it",
    )
    add_password_flag(run)

    lint = add(
        "lint",
        dispatch.lint,
        "validate a flow without running it",
        "Checks the graph (cycles, unreachable steps, branch targets) and every template\n"
        "reference, then each component the flow names: that its spec.json is one the\n"
        "engine can execute, that a tool's run.command exists and is executable, that\n"
        "declared schemas are valid schemas, that an agent's settings are ones its adapter\n"
        "accepts, and that the inputs a step passes match what the tool declares.\n\n"
        "These are the same checks `run` performs before its first step, so a clean lint\n"
        "means a flow will not fail on its own definitions.\n",
    )
    lint.add_argument("flow", help=FLOW_HELP)

    graph = add("graph", dispatch.graph, "print a flow's push edges as text")
    graph.add_argument("flow", help=FLOW_HELP)

    diagram = add(
        "diagram",
        dispatch.diagram,
        "render a flow as Mermaid markdown (static, no model)",
        "Also reports how the flow resolves: which steps run concurrently, which may\n"
        "be skipped by a branch, and where the joins are.\n",
    )
    diagram.add_argument("flow", help=FLOW_HELP)
    diagram.add_argument(
        "-o", "--out", type=Path, metavar="FILE", help="write to a file instead of stdout"
    )

    add(
        "list",
        dispatch.list_components,
        "show installed flows, tools, agents and adapters",
        "Marks anything a higher-precedence root is shadowing.\n",
    )
    add(
        "paths",
        dispatch.show_paths,
        "show the search roots and their precedence",
        "Set ATF_PATH to prepend roots, for tests and one-off overrides.\n",
    )

    vault = sub.add_parser(
        "vault",
        help="manage an encrypted secrets file",
        description="Manage an encrypted secrets file.",
        epilog="A flow's steps declare which secrets they take; the engine passes each\n"
        "step only those, as environment variables. See `run --help`.\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    vault_sub = vault.add_subparsers(dest="vault_command", metavar="<action>", required=True)

    def add_vault(name: str, handler, help_text: str, epilog: str | None = None):
        command = vault_sub.add_parser(
            name,
            help=help_text,
            description=help_text[0].upper() + help_text[1:] + ".",
            epilog=epilog,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        command.add_argument("file", type=Path, help="the vault file")
        command.set_defaults(handler=handler)
        add_password_flag(command)
        return command

    create = add_vault(
        "create",
        dispatch.vault_create,
        "create a vault from a YAML mapping on stdin",
    )
    create.add_argument("--force", action="store_true", help="replace the file if it exists")

    set_command = add_vault(
        "set",
        dispatch.vault_set,
        "add or replace one secret",
        "The value is read from stdin, or prompted for when there is a terminal. Never\n"
        "from a flag, so it stays out of shell history.\n",
    )
    set_command.add_argument("name", help="the secret's name")

    add_vault("list", dispatch.vault_list, "list secret names, without their values")
    add_vault("view", dispatch.vault_view, "decrypt to stdout: this prints secrets")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()

    # Bare `atf` prints help instead of a usage error. Someone typing the command with
    # nothing after it is asking what it does, and argparse's default answer to that is a
    # single usage line and exit 2.
    if not (argv if argv is not None else sys.argv[1:]):
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    try:
        return args.handler(args, Paths(args.workspace.resolve()))
    except commands.EXPECTED_ERRORS as exc:
        # Every expected failure surfaces here, one line, prefixed, on stderr. The set is
        # the command layer's, so a second front end catches exactly the same things. An
        # unexpected exception is deliberately left to raise with its traceback.
        print(f"engine: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("engine: interrupted", file=sys.stderr)
        return 130
