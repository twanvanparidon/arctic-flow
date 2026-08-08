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
from typing import Any

import yaml

from cli import branding
from commands.results import (
    AdapterDetail,
    AgentDetail,
    ComponentCreated,
    ComponentEntry,
    Inventory,
    LintReport,
    LintResult,
    RunResult,
    SecretListing,
    SecretSet,
    ToolDetail,
    VaultContents,
    VaultCreated,
)

# Wide enough for the longest built-in name with a gap after it, so a listing's second
# column lines up without being measured per run.
NAME_WIDTH = 18

# The same, for the settings tables in the two detail views. Wide enough for the longest
# label either of them shows, `timeout_seconds`, with a gap after it.
FIELD_WIDTH = 16

# What `inspect` shows of a spec, and the order it shows it in. A field the engine reads
# and this does not is invisible to the person deciding whether to name the component, so
# adding one to `engine/specs.py` means adding it here too.
AGENT_FIELDS = (
    "description",
    "adapter",
    "model",
    "effort",
    "max_budget_usd",
    "timeout_seconds",
    "tools",
    "unattended",
)

# `command`, `timeout_seconds`, `filesystem` and `network` are lifted out of the nested
# `run` and `permissions` objects by `_tool_fields`.
TOOL_FIELDS = (
    "description",
    "command",
    "timeout_seconds",
    "filesystem",
    "network",
    "secrets",
    "requires",
)

# What to do with a component that was just scaffolded. One line each, and each names the
# file that is about to be edited rather than telling someone to go and edit something.
NEXT = {
    "flow": f"next: {branding.COMMAND} lint {{name}}",
    "agent": "next: write {path}/agent.md, which is the system prompt",
    "tool": "next: write {path}/run.sh, which is what the tool does",
}


def count(n: int, noun: str) -> str:
    """`1 step`, `2 steps`. Three commands were printing "1 steps"."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def lint(result: LintResult) -> str:
    """A flow that validated. Saying what was checked is the reassurance being asked for."""
    return f"{result.display}: ok, {count(len(result.steps), 'step')}, no issues found"


def lint_report(result: LintReport) -> str:
    """A sweep: one line per flow, failures last, then what it adds up to.

    Failures last because this is read in a pipeline log, where the end is what is on
    screen. The pass lines are `lint`'s own, so one flow checked on its own and the same
    flow inside a sweep report the same sentence.
    """
    lines = [lint(item) for item in result.checked]
    lines += [f"{issue.display}: failed, {issue.error}" for issue in result.issues]

    total = len(result.checked) + len(result.issues)
    if not total:
        return "no flows found"
    summary = "no issues found" if result.ok else f"{len(result.issues)} failed"
    return "\n".join(lines + ["", f"{count(total, 'flow')} checked, {summary}"])


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
    """Every name that resolved, by kind, and where each one came from.

    The layer a name came out of is the answer to most of what this is read for: which of
    two definitions won, whether the one you edited is the one that runs, what is yours and
    what shipped with the engine. `display` names the layer rather than spelling out a
    path, so the column stays short enough to sit beside the name.
    """
    lines = ["adapters:"]
    lines += [f"  {_entry(entry)}" for entry in result.adapters]
    lines.append("")

    for listing in result.kinds:
        # "agents: none" on one line rather than a heading over nothing.
        lines.append(f"{listing.kind}s:" if listing.entries else f"{listing.kind}s: none")
        lines += [f"  {_entry(entry)}" for entry in listing.entries]
        lines.append("")

    return "\n".join(lines)


def _entry(entry: ComponentEntry) -> str:
    """One available name, where it was found, and what it is hiding behind it."""
    note = f"  (shadows {', '.join(entry.shadows)})" if entry.shadows else ""
    return f"{entry.name:<{NAME_WIDTH}} {entry.display}{note}"


def adapter_detail(result: AdapterDetail) -> str:
    """One adapter: what it runs, and what an agent spec naming it may ask for.

    The schema is the answer to "which settings does this take", so it is the body rather
    than a footnote: `engine.specs` validates an agent's settings against exactly this.
    """
    lines = _heading(result.name, result.display)
    lines += _fields({"description": result.description}, ("description",))
    return "\n".join(lines + _schema("settings", result.input_schema)).rstrip("\n")


def agent_detail(result: AgentDetail) -> str:
    """One agent: what it is configured as, then the prompt itself, verbatim.

    The prompt is last and unindented because it is the thing being read. Anything after
    it would be scrolled past, and indenting prose to line up with a settings table makes
    it harder to read for no gain.
    """
    lines = _heading(result.name, result.display)
    lines += _fields(result.spec, AGENT_FIELDS)
    lines += _schema("output schema", result.spec.get("output_schema"))
    return "\n".join(lines + ["system prompt:", "", result.prompt])


def tool_detail(result: ToolDetail) -> str:
    """One tool: what it is allowed to do, what it takes, and how it can fail."""
    lines = _heading(result.name, result.display)
    lines += _fields(_tool_fields(result.spec), TOOL_FIELDS)
    lines += _schema("input schema", result.spec.get("input_schema"))
    lines += _schema("output schema", result.spec.get("output_schema"))

    codes = result.spec.get("exit_codes") or {}
    if codes:
        lines.append("exit codes:")
        lines += [f"  {code:<4} {meaning}" for code, meaning in sorted(codes.items())]
        lines.append("")

    return "\n".join(lines + ([result.doc] if result.doc else [])).rstrip("\n")


def _heading(name: str, display: str) -> list[str]:
    return [f"{name}  {display}", ""]


def _fields(values: dict[str, Any], order: tuple[str, ...]) -> list[str]:
    """The named values that are present, in the order given, one per line.

    Absent ones are skipped rather than printed empty: a spec that leaves `effort` out is
    not an agent with no effort, it is one whose adapter decides. Ordered by a list here
    rather than by the document, so two specs with the same fields read the same way and a
    field the engine reads is shown only once someone adds it.
    """
    lines = [
        f"  {field:<{FIELD_WIDTH}} {_value(values[field])}"
        for field in order
        if values.get(field) is not None
    ]
    return lines + [""] if lines else lines


def _value(value: Any) -> str:
    """A scalar as itself, a list joined, an empty list named rather than left blank."""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "(none)"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _tool_fields(spec: dict[str, Any]) -> dict[str, Any]:
    """A tool's spec flattened to the names `TOOL_FIELDS` shows.

    `permissions` and `run` are nested in the document and are the two things a reader is
    checking, so they are lifted rather than left a level down.
    """
    run, permissions = spec.get("run") or {}, spec.get("permissions") or {}
    return {
        **spec,
        "command": run.get("command"),
        "timeout_seconds": run.get("timeout_seconds"),
        "filesystem": permissions.get("filesystem"),
        "network": permissions.get("network"),
    }


def _schema(label: str, schema: dict[str, Any] | None) -> list[str]:
    """A declared schema, indented under its label. Absent is not an empty block."""
    if not schema:
        return []
    body = json.dumps(schema, indent=2).splitlines()
    return [f"{label}:"] + [f"  {line}" for line in body] + [""]


def component_created(result: ComponentCreated) -> str:
    """What was written, and the one thing to do with it next.

    A scaffold runs as it is, so the useful next step is not "finish it" but whichever
    file the reader is about to edit: an agent is its prompt, a tool is its script, and a
    flow is already something `lint` can answer for.
    """
    lines = [f"created {result.kind} {result.name}  {result.display}", ""]
    if result.files:
        lines += [f"  {name}" for name in result.files] + [""]
    return "\n".join(lines + [NEXT[result.kind].format(name=result.name, path=result.display)])


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
