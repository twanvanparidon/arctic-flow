"""A flow's push edges as text.

Reading a graph out of nested YAML is hard: edges are spread across `push`, `switch` and
`cases` at three indentation levels, and the file's step order says nothing about execution
order. This prints one block per step with every outgoing edge under it.

It renders what `validate()` already accepted, so there is nothing to check here. A step
with no outgoing edge is marked `(terminal)` rather than left blank, because "this step
ends the flow" and "I forgot to draw the rest" should not look the same.

A gate is printed above the edges it guards, in the order the engine takes them: the step
runs, the gate accepts, and only then does anything leave.

A case going back upstream is marked as a loop. Read as an ordinary edge it looks like a
step running twice for no reason, and which case that is cannot be seen from the YAML: it
depends on where the target sits in the rest of the graph.
"""

from __future__ import annotations

from typing import Any

from engine.executor import DEFAULT_GATE_ATTEMPTS, back_edges, build_graph

CASE_WIDTH = 10


def render(flow: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    outbound, _ = build_graph(steps)
    back = back_edges(outbound, flow["start"])
    lines = [f"{flow['flow']}: start -> {flow['start']}"]

    for step in steps:
        kind = f"tool:{step['tool']}" if "tool" in step else f"agent:{step['agent']}"
        lines += ["", f"  {step['id']}  ({kind})"]

        if "gate" in step:
            gate = step["gate"]
            allowed = gate.get("max_attempts", DEFAULT_GATE_ATTEMPTS)
            lines.append(f"    gate {gate['tool']}  (up to {allowed} attempts)")

        if "push" in step:
            lines += [f"    -> {target}" for target in step["push"] or []]
        elif "switch" in step:
            lines.append(f"    switch {step['switch']}")
            for value, branch in (step.get("cases") or {}).items():
                note = _loop_note(step, branch, back)
                lines.append(f"      {value:<{CASE_WIDTH}} -> {_targets(branch)}{note}")
            if "default" in step:
                note = _loop_note(step, step["default"], back)
                lines.append(
                    f"      {'default':<{CASE_WIDTH}} -> {_targets(step['default'])}{note}"
                )
        else:
            lines.append("    (terminal)")

    return "\n".join(lines)


def _targets(branch: list[str] | None) -> str:
    """A branch that goes nowhere is a valid ending, and says so."""
    return ", ".join(branch or []) or "(ends)"


def _loop_note(step: dict[str, Any], branch: list[str] | None, back: set[tuple[str, str]]) -> str:
    """Marks the case that goes back upstream, and how many times it may."""
    if not any((step["id"], target) in back for target in branch or []):
        return ""
    return f"  (loops back, max {step.get('max_loops')})"
