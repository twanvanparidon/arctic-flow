"""What could come next on a half-typed command line.

Completion is a property of the terminal, like help text, so it lives here rather than in
`commands/`. The shell uses both halves of it: `snippet` prints the function that asks the
questions, `candidates` answers them.

Everything offered is read off the parser and the component lookup, never from a list kept
here, so a new command, a new flag or a new flow completes without this file changing.

Flag *values* are answered with nothing, deliberately. A vault file and an
`--input KEY=VALUE` are better served by the shell's own filename completion than by a
half-guess from here, and the snippet asks for that fallback.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import adapters
from paths.resolver import Paths

# Commands whose first argument is a component name, and the kind to answer it from.
# Components are named rather than pathed, so the candidates come out of the lookup; a path
# to a .yaml file is left to the shell. Keyed by the command's own name, which for anything
# in a bucket is the leaf: the walk below descends before it asks what the command takes.
# So `inspect agent` arrives here as `agent`.
NAME_COMMANDS = {
    "run": "flow",
    "lint": "flow",
    "flow": "flow",
    "agent": "agent",
    "tool": "tool",
    "adapter": "adapter",
}

# One name per snippet in completions/. A second shell is a second file and a second entry.
SHELLS = ("bash",)

SNIPPETS = Path(__file__).resolve().parent / "completions"

WORKSPACE_FLAG = "--workspace"

# Commands that are never offered, though both are real and both still run. `__complete` is
# what the snippet types and nobody else. `completion` is typed once, into a startup file, so
# a prompt is the one place it is no use: `atf --help` is where it belongs.
UNOFFERED = ("__complete", "completion", "mcp-serve")


def snippet(shell: str) -> str:
    """The shell function that calls `__complete`.

    A file rather than a string in here, so `shellcheck` reads it with the rest of the shell
    in this repository. It ships as package data of `cli`, and lands in the same place
    relative to this module in a wheel and in a frozen build; see `packaging/atf.spec`.
    """
    return (SNIPPETS / f"{shell}.sh").read_text()


def candidates(words: list[str], workspace: Path) -> list[str]:
    """What could complete the last of `words`, given every word before it.

    The last element is the word under the cursor, and is empty when the cursor sits after a
    space. `workspace` is only the fallback: a `--workspace` among the words is the one being
    asked about, so it decides which flows are in scope.
    """
    if not words:
        return []
    typed, current = words[:-1], words[-1]

    parser, command, arguments = _reached(_parser(), typed)
    subcommands = _subcommands(parser)

    if current.startswith("-"):
        pool: list[str] = list(_flags(parser))
    elif subcommands:
        # A level with commands under it and none of them typed yet: `atf <TAB>`, `atf
        # vault <TAB>`. Filtered here rather than in `_subcommands`, which the walk above
        # uses: `atf completion <TAB>` still has to reach the parser it names.
        pool = [name for name in subcommands if name not in UNOFFERED]
    elif (kind := NAME_COMMANDS.get(command)) and not arguments:
        pool = _component_names(kind, _workspace(typed, workspace))
    else:
        # A value: a file, an input, a secret's name. Nothing, so the shell offers files.
        pool = []

    return sorted(word for word in pool if word.startswith(current))


def _parser() -> argparse.ArgumentParser:
    """A parser to read the interface off.

    Imported on use rather than at the top: `cli.app` imports `cli.dispatch`, which imports
    this module, so a module-level import here would close the circle.
    """
    from cli.app import build_parser  # noqa: PLC0415 (circular at module level, see above)

    return build_parser()


def _reached(
    parser: argparse.ArgumentParser, words: list[str], name: str = ""
) -> tuple[argparse.ArgumentParser, str, list[str]]:
    """The parser the words got to, the command that named it, and its arguments so far.

    One level per subcommand, so `vault set secrets.vault` ends at `set`'s own parser with
    one argument. Descending before the arguments are counted is what keeps a flag at the
    level that declares it: `--vault-password-file` belongs to `set`, not to `vault`.
    """
    flags, subcommands = _flags(parser), _subcommands(parser)
    arguments: list[str] = []
    expecting = False

    for index, word in enumerate(words):
        if expecting:
            # The value of the flag before it, so it fills no argument slot.
            expecting = False
        elif word.startswith("-"):
            expecting = flags.get(word, False)
        elif not arguments and word in subcommands:
            return _reached(subcommands[word], words[index + 1 :], word)
        else:
            arguments.append(word)

    return parser, name, arguments


def _flags(parser: argparse.ArgumentParser) -> dict[str, bool]:
    """Every flag a parser takes, each mapped to whether a value follows it.

    argparse publishes no accessor for what it was built with, so this and `_subcommands`
    read the action list. Both are tested directly, so a Python release moving it fails
    there rather than silently completing nothing.
    """
    return {
        option: action.nargs != 0 for action in parser._actions for option in action.option_strings
    }


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """Every command under a parser, including the ones never offered as candidates."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _component_names(kind: str, workspace: Path) -> list[str]:
    """The components of one kind in scope, by name: the lookup `run` resolves with.

    Adapters are the exception the rest of the engine makes for them too. They are a
    registry of static imports rather than names under a root, so there is no lookup to
    ask and the answer is the same from any workspace.
    """
    if kind == "adapter":
        return adapters.names()
    return list(Paths(workspace).list(kind))


def _workspace(typed: list[str], fallback: Path) -> Path:
    """The `--workspace` among the words, or where the shell is.

    Read off the line rather than from the invocation, because the flag that decides the
    answer is the one being typed: `atf --workspace examples/file-review run <TAB>` is asking
    about that project's flows, not this directory's. The last one wins, as argparse does it.
    """
    found = fallback
    for index, word in enumerate(typed):
        if word == WORKSPACE_FLAG and index + 1 < len(typed):
            found = Path(typed[index + 1]).expanduser()
        elif word.startswith(f"{WORKSPACE_FLAG}="):
            found = Path(word.split("=", 1)[1]).expanduser()
    return found
