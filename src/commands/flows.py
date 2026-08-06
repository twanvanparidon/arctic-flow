"""Commands that act on a flow: run it, check it, or look at it without running it.

Running is deliberately two calls. `prepare` does everything that can fail before the
first step, so a front end paints its progress display knowing a step starts next. Folded
into one call, a mistyped input arrives under a spinner with "failed after 0ms" on top.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from commands.results import DiagramResult, FlowPlan, GraphResult, LintResult, RunResult
from commands.secrets import Password, open_vault
from engine.executor import (
    FlowError,
    check_inputs,
    inputs_from_environment,
    load_flow,
    run_flow,
    validate,
)
from paths.resolver import Paths

# The engine's observer: called with one event dict as steps start, finish, skip and fail.
# Events arrive from worker threads, so anything passed here must be safe to call
# concurrently. `cli/progress.py` is the terminal's implementation of it.
EventObserver = Callable[[dict[str, Any]], None]


def resolve_flow(reference: str, paths: Paths) -> Path:
    """Accept either a name to look up or a path to a file.

    A name is tried first, so `run review_file` works from any directory. Anything that
    looks like a path, or that the lookup cannot place, is read as one, which keeps
    ad-hoc flows outside the search roots usable.
    """
    candidate = Path(reference)
    if candidate.suffix in (".yaml", ".yml") or candidate.exists():
        if not candidate.is_file():
            raise FlowError(f"no such flow file: {reference}")
        return candidate
    return paths.find("flow", reference)


def prepare(
    flow_ref: str,
    paths: Paths,
    inputs: dict[str, Any] | None = None,
    *,
    vault_ref: str | None = None,
    password: Password | None = None,
) -> FlowPlan:
    """Resolve a flow, check its inputs, and open the vault it needs.

    `inputs` beats what the environment supplies, since it was passed for this run and a
    variable was exported for the shell. `vault_ref` overrides a `vault` the flow set for
    itself, so a caller told which vault to use beats the file. `password` is only
    consulted if one of the two named a vault.
    """
    path = resolve_flow(flow_ref, paths)
    definition = load_flow(path)
    # `paths.env` rather than os.environ: one environment per run, already injectable, and
    # already what decides the search roots. A caller isolating one isolates both.
    supplied = {**inputs_from_environment(definition, paths.env), **(inputs or {})}
    # Checked on its own line, before the vault is touched, because the order is the
    # point: a mistyped input should be answered with the mistake, not with a password
    # prompt. Leaving it to argument evaluation order would make that an accident.
    checked = check_inputs(definition, supplied)
    return FlowPlan(
        paths=paths,
        definition=definition,
        path=path,
        display=paths.display(path),
        inputs=checked,
        vault=open_vault(vault_ref or definition.get("vault"), paths, password),
    )


def run(plan: FlowPlan, *, on_event: EventObserver | None = None) -> RunResult:
    """Execute a prepared flow and return its output and per-step trace.

    `on_event` goes straight to the engine, so progress is reported without the engine
    deciding what progress looks like. The output comes back as a string rather than
    being printed: where it goes is the caller's business, and stdout is load-bearing.
    """
    output, trace = run_flow(
        plan.definition, plan.inputs, plan.paths, plan.vault, on_event=on_event
    )
    return RunResult(
        flow=plan.name, path=plan.path, display=plan.display, output=output, trace=trace
    )


def lint(flow_ref: str, paths: Paths) -> LintResult:
    """Validate a flow without running it. Raises on the first problem it finds.

    A returned result therefore means "no issues", and `steps` is the same validated
    list `run` would execute.
    """
    path = resolve_flow(flow_ref, paths)
    definition = load_flow(path)
    # Validated on its own line, before any key is read. Keyword arguments evaluate left
    # to right, so a `flow=definition["flow"]` above the validate() call reports a missing
    # name as a KeyError traceback instead of the flow error that explains it.
    steps = validate(definition, paths)
    return LintResult(
        flow=str(definition["flow"]),
        path=path,
        display=paths.display(path),
        steps=steps,
    )


def graph(flow_ref: str, paths: Paths) -> GraphResult:
    """A flow's push edges as text. Validates first, so the edges are real ones."""
    from util.graph import render  # noqa: PLC0415 (as with diagram, `run` never needs it)

    path = resolve_flow(flow_ref, paths)
    definition = load_flow(path)
    # Validated first, for the reason given in lint().
    text = render(definition, validate(definition, paths))
    return GraphResult(
        flow=str(definition["flow"]),
        path=path,
        display=paths.display(path),
        text=text,
    )


def diagram(flow_ref: str, paths: Paths, out: Path | None = None) -> DiagramResult:
    """Mermaid markdown plus the static resolution report. No model, nothing run.

    Writes to `out` when one is given, since saving it is the same operation from any
    front end, and returns the markdown either way so a caller can show what it wrote.
    """
    from util.mermaid import render  # noqa: PLC0415 (keeps `run` free of it)

    path = resolve_flow(flow_ref, paths)
    definition = load_flow(path)
    markdown = render(definition, validate(definition, paths), paths)
    if out:
        out.write_text(markdown)
    return DiagramResult(
        flow=str(definition["flow"]),
        path=path,
        display=paths.display(path),
        markdown=markdown,
        written_to=out,
    )
