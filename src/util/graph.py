"""A flow's push edges as text.

Reading a graph out of nested YAML is hard: edges are spread across `push`, `switch` and
`cases` at three indentation levels, and the file's step order says nothing about execution
order. This prints one block per step with every outgoing edge under it.

It renders what `validate()` already accepted, so there is nothing to check here. A step
with no outgoing edge is marked `(terminal)` rather than left blank, because "this step
ends the flow" and "I forgot to draw the rest" should not look the same.

A gate is printed above the edges it guards, in the order the engine takes them: the step
runs, the gate accepts, and only then does anything leave.
"""

from __future__ import annotations

from typing import Any

from engine.executor import DEFAULT_GATE_ATTEMPTS

CASE_WIDTH = 10


def render(flow: dict[str, Any], steps: list[dict[str, Any]]) -> str:
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
                lines.append(f"      {value:<{CASE_WIDTH}} -> {_targets(branch)}")
            if "default" in step:
                lines.append(f"      {'default':<{CASE_WIDTH}} -> {_targets(step['default'])}")
        else:
            lines.append("    (terminal)")

    return "\n".join(lines)


def _targets(branch: list[str] | None) -> str:
    """A branch that goes nowhere is a valid ending, and says so."""
    return ", ".join(branch or []) or "(ends)"
