"""Render a flow as a Mermaid diagram plus a static resolution report.

Nothing is executed: this reads the flow YAML and works out what the engine *would* do, so
a diff of the diagram is a diff of the graph.

Three things it derives that the flow does not write down:

  waves       Steps that can run at the same time. A step's wave is one past its deepest
              inbound step, so a join sits after everything it waits on.

  guaranteed  Whether a step runs on every path. Transitive, not from direct branch
              targets: a switch guarantees a step when every case *eventually* reaches it,
              however far downstream. This is what a reader tracing a branchy flow by eye
              most often gets wrong, and what a first cut of this file got wrong too.

  joins       Steps with more than one inbound edge, which is what skip propagation is for.
"""

from __future__ import annotations

from typing import Any

from engine.executor import build_graph, load_agent, outbound_targets
from paths.resolver import Paths


def node_ids(step_ids: list[str]) -> dict[str, str]:
    """Map step ids to Mermaid identifiers.

    Positional rather than derived from the name: sanitising characters would collapse
    `read-target` and `read_target` onto one node, silently merging two steps. The real
    id still appears in the label.
    """
    return {sid: f"n{index}" for index, sid in enumerate(step_ids)}


def topological_order(ids: list[str], inbound: dict[str, set[str]]) -> list[str]:
    """Assumes the flow already passed validation, so there are no cycles."""
    remaining = {sid: set(inbound.get(sid, set())) for sid in ids}
    order: list[str] = []
    while remaining:
        ready = sorted(sid for sid, preds in remaining.items() if not preds - set(order))
        if not ready:  # pragma: no cover (validate() rejects cycles first)
            order.extend(sorted(remaining))
            break
        order.extend(ready)
        for sid in ready:
            del remaining[sid]
    return order


def waves(ids: list[str], inbound: dict[str, set[str]]) -> dict[str, int]:
    depth: dict[str, int] = {}
    for sid in topological_order(ids, inbound):
        preds = inbound.get(sid, set())
        depth[sid] = 1 + max((depth.get(p, 0) for p in preds), default=0)
    return depth


def always_reaches(
    target: str, by_id: dict[str, dict[str, Any]], order: list[str]
) -> dict[str, bool]:
    """For each step, whether every execution from there eventually reaches `target`.

    Computed over successors first, so the answer is transitive: a branch leading
    somewhere that always reaches the target counts, not just one naming it directly.

    The two step kinds combine differently. A `push` runs all of its targets, so the
    target is reached if *any* of them always reaches it. A `switch` runs exactly one
    case, so *every* case must reach it.
    """
    reaches: dict[str, bool] = {}
    for sid in reversed(order):
        if sid == target:
            reaches[sid] = True
            continue
        step = by_id[sid]
        if "push" in step:
            reaches[sid] = any(reaches.get(t, False) for t in step["push"] or [])
        elif "switch" in step:
            branches = [list(b or []) for b in (step.get("cases") or {}).values()]
            if "default" in step:
                branches.append(list(step["default"] or []))
            reaches[sid] = bool(branches) and all(
                any(reaches.get(t, False) for t in branch) for branch in branches
            )
        else:
            reaches[sid] = False  # terminal, and not the target
    return reaches


def guaranteed_steps(
    flow: dict[str, Any], by_id: dict[str, dict[str, Any]], inbound: dict[str, set[str]]
) -> set[str]:
    """Which steps run no matter which branches are taken."""
    order = topological_order(list(by_id), inbound)
    return {sid for sid in by_id if always_reaches(sid, by_id, order).get(flow["start"], False)}


def reachable_from(seeds: list[str], outbound: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    queue = list(seeds)
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(outbound.get(current, []))
    return seen


def describe_step(step: dict[str, Any], paths: Paths) -> tuple[str, str]:
    """Label and mermaid class for a step."""
    if "tool" in step:
        return f"{step['id']}<br/><small>tool: {step['tool']}</small>", "tool"
    try:
        spec, _ = load_agent(paths, step["agent"])
        detail = f"{step['agent']} · {spec.get('model', '?')}/{spec.get('effort', '?')}"
    except Exception:  # noqa: BLE001 (a diagram of a broken flow is still useful)
        detail = f"{step['agent']} · <unreadable>"
    return f"{step['id']}<br/><small>{detail}</small>", "agent"


def render(flow: dict[str, Any], steps: list[dict[str, Any]], paths: Paths) -> str:
    by_id = {step["id"]: step for step in steps}
    outbound, inbound = build_graph(steps)
    ids = list(by_id)
    depth = waves(ids, inbound)
    guaranteed = guaranteed_steps(flow, by_id, inbound)
    joins = {sid for sid in ids if len(inbound.get(sid, set())) > 1}
    nid = node_ids(sorted(ids, key=lambda s: (depth[s], s)))

    lines: list[str] = [
        f"# {flow['flow']}",
        "",
        flow.get("description", "").strip() or "_no description_",
        "",
        "```mermaid",
        "flowchart TD",
        "  start([start])",
    ]

    for sid in sorted(ids, key=lambda s: (depth[s], s)):
        label, kind = describe_step(by_id[sid], paths)
        shape = f'["{label}"]' if kind == "tool" else f'("{label}")'
        lines.append(f"  {nid[sid]}{shape}")
        # One statement per class: in `class a,b c;` the comma separates *node ids*,
        # so "class n1 agent,skippable" would emit a single bogus class name and
        # silently match no rule at all.
        for class_name in [kind] + ([] if sid in guaranteed else ["skippable"]):
            lines.append(f"  class {nid[sid]} {class_name};")

    lines.append(f"  start --> {nid[flow['start']]}")
    for sid in sorted(ids, key=lambda s: (depth[s], s)):
        step = by_id[sid]
        for target in step.get("push") or []:
            lines.append(f"  {nid[sid]} --> {nid[target]}")
        if "switch" not in step:
            continue
        branches = list((step.get("cases") or {}).items())
        if "default" in step:
            branches.append(("default", step["default"]))
        for value, targets in branches:
            for target in targets or []:
                # Dotted, because whether this edge is taken is decided at run time.
                lines.append(f'  {nid[sid]} -.->|"{value}"| {nid[target]}')

    lines += [
        "  classDef tool stroke-width:1px;",
        "  classDef agent stroke-width:2px;",
        "  classDef skippable stroke-dasharray:4 3;",
        "```",
        "",
        "Dashed border: may be skipped, depending on a branch. "
        "Dotted edge: taken only when its switch selects that case.",
        "",
        "## Resolution",
        "",
        "| wave | runs concurrently |",
        "| ---- | ----------------- |",
    ]
    for wave in sorted(set(depth.values())):
        members = sorted(sid for sid in ids if depth[sid] == wave)
        lines.append(f"| {wave} | {', '.join(f'`{m}`' for m in members)} |")

    # Only carry the secrets column when something uses one, so flows without any are
    # not padded with an empty column. Names only: a diagram is meant to be shared.
    uses_secrets = any(by_id[sid].get("secrets") for sid in ids)
    header = ["step", "kind", "always runs", "waits on", "pushes to"]
    if uses_secrets:
        header.insert(2, "secrets")
    lines += [
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("-" * max(4, len(column)) for column in header) + " |",
    ]
    for sid in sorted(ids, key=lambda s: (depth[s], s)):
        step = by_id[sid]
        kind = f"tool `{step['tool']}`" if "tool" in step else f"agent `{step['agent']}`"
        waits = sorted(inbound.get(sid, set())) or ["start"]
        targets = outbound_targets(step)
        row = [
            f"`{sid}`",
            kind,
            "yes" if sid in guaranteed else "no",
            ", ".join(f"`{w}`" for w in waits),
            ", ".join(f"`{t}`" for t in targets) or "_terminal_",
        ]
        if uses_secrets:
            granted = step.get("secrets") or []
            row.insert(2, ", ".join(f"`{name}`" for name in granted) or "_none_")
        lines.append("| " + " | ".join(row) + " |")

    switches = [by_id[sid] for sid in ids if "switch" in by_id[sid]]
    if switches:
        lines += ["", "## Branches", ""]
        for step in switches:
            lines.append(f"`{step['id']}` switches on `{step['switch']}`:")
            branches = list((step.get("cases") or {}).items())
            if "default" in step:
                branches.append(("default", step["default"]))
            for value, targets in branches:
                only = sorted(reachable_from(list(targets or []), outbound) - guaranteed)
                suffix = (
                    f" (skipped otherwise: {', '.join(f'`{o}`' for o in only)})" if only else ""
                )
                arrow = ", ".join(f"`{t}`" for t in (targets or [])) or "_ends here_"
                lines.append(f"- `{value}` → {arrow}{suffix}")
            if "default" not in step:
                lines.append(
                    "- no default: a value outside these cases fails the run rather than "
                    "silently ending it"
                )
            lines.append("")

    if joins:
        lines += ["## Joins", ""]
        for sid in sorted(joins):
            sources = sorted(inbound[sid])
            optional = [s for s in sources if s not in guaranteed]
            note = (
                f" (`{'`, `'.join(optional)}` may be skipped, which unblocks rather than "
                "stalls this step)"
                if optional
                else ""
            )
            lines.append(f"- `{sid}` waits on {', '.join(f'`{s}`' for s in sources)}{note}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
