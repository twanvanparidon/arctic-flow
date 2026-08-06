"""Command results as terminal text.

The other half of the split `commands/` makes: those functions decide, these describe.
Everything here is pure, so a second front end can reuse whichever of these still read
well in a pane and replace the rest one function at a time.

**Every function returns text with no trailing newline**, and the caller prints it. A
blank line ending a listing is part of that listing's shape and is an empty final element;
a newline terminating the last line is the printer's job. Mixing the two up is how output
grows a stray blank line.

No colour here either. Colour belongs where the stream is known (see `cli/colour.py`).
"""

from __future__ import annotations

import json

import yaml

from commands.results import (
    Inventory,
    LintResult,
    PathsReport,
    RunResult,
    SecretListing,
    SecretSet,
    VaultContents,
    VaultCreated,
)

# Wide enough for the longest built-in name with a gap after it, so a listing's second
# column lines up without being measured per run.
NAME_WIDTH = 18


def count(n: int, noun: str) -> str:
    """`1 step`, `2 steps`. Three commands were printing "1 steps"."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def lint(result: LintResult) -> str:
    """A flow that validated. Saying what was checked is the reassurance being asked for."""
    return f"{result.display}: ok, {count(len(result.steps), 'step')}, no issues found"


def trace(result: RunResult) -> str:
    """The machine-readable run summary behind `--trace`.

    JSON rather than prose: this one is for piping into something that adds up costs or
    diffs two runs.
    """
    return json.dumps(
        {"flow": result.flow, "cost_usd": round(result.cost_usd, 6), "steps": result.trace},
        indent=2,
    )


def inventory(result: Inventory) -> str:
    """Adapters first, then each kind, with what it shadows noted beside it."""
    lines = ["adapters:"]
    lines += [f"  {name:<{NAME_WIDTH}} {text}" for name, text in result.adapters.items()]
    lines.append("")

    for listing in result.kinds:
        # "agents: none" on one line rather than a heading over nothing.
        lines.append(f"{listing.kind}s:" if listing.entries else f"{listing.kind}s: none")
        for entry in listing.entries:
            note = f"  (shadows {', '.join(entry.shadows)})" if entry.shadows else ""
            lines.append(f"  {entry.name:<{NAME_WIDTH}} {entry.display}{note}")
        lines.append("")

    return "\n".join(lines)


def search_paths(result: PathsReport) -> str:
    """The roots in precedence order, each with what it actually contains."""
    lines = ["search roots, highest precedence first:", ""]
    for index, root in enumerate(result.roots, start=1):
        lines.append(f"  {index}. {root.display}")
        # Named rather than left blank, so a root with nothing in it reads as answered.
        lines.append(f"     {', '.join(root.subdirs) or '(nothing)'}")
    lines += [
        "",
        f"working directory: {result.workspace}",
        "components run with the working directory set here, wherever they were found",
    ]
    return "\n".join(lines)


def vault_created(result: VaultCreated) -> str:
    return f"wrote {result.display} ({count(result.count, 'secret')})"


def secret_set(result: SecretSet) -> str:
    """Which of the two things happened. The caller could not tell otherwise."""
    return f"{'replaced' if result.replaced else 'added'} {result.name} in {result.display}"


def secret_names(result: SecretListing) -> str:
    return "\n".join(
        [f"{result.display}: {count(len(result.names), 'secret')}"]
        + [f"  {name}" for name in result.names]
    )


def vault_contents(result: VaultContents) -> str:
    """Decrypted secrets, as the YAML `vault create` reads back in.

    Sorted and block-style, so two dumps of the same vault are the same bytes and safe to
    diff. The trailing newline is stripped because the printer adds one, and two would
    show as a blank line.
    """
    dumped = yaml.safe_dump(result.values, sort_keys=True, default_flow_style=False)
    return dumped.rstrip("\n")
