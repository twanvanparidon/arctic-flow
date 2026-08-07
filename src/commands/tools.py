"""Tools as something other than a step: described, and called one at a time by name.

`commands.run` executes a tool because a flow's graph said to. These two exist because an
*agent* can also call one mid-turn, so something has to describe the tools it was granted
and dispatch them individually. The dispatch is still `engine.executor.invoke`, so an
in-turn call gets the same schema check, the same workspace cwd and the same timeout as a
step. There is no second way to run a tool.

Secrets reach an in-turn call by design, and the design is that they do not. `invoke` is
called without a grant, and `validate()` refuses a step that both declares `secrets` and
runs an agent granted tools, so no vault secret is in this process's environment to
inherit in the first place. Scoping a grant per in-turn call is the follow-up that would
lift that restriction.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from commands.results import ToolCall, ToolDescription
from engine.executor import Cancelled, FlowError, invoke, load_component
from paths.resolver import Paths


def describe_tools(names: list[str], paths: Paths) -> list[ToolDescription]:
    """The named tools, in the order given. Raises if one cannot be resolved.

    Unlike `call_tool` this does raise, because it runs during the handshake: a name that
    does not resolve is a flow that should never have started, not something a model can
    work around.
    """
    described = []
    for name in names:
        base, spec = load_component(paths, "tool", name)
        described.append(
            ToolDescription(
                name=spec["name"],
                description=_description(base, spec),
                input_schema=spec["input_schema"],
            )
        )
    return described


def _description(base: Path, spec: dict[str, Any]) -> str:
    """The spec's description, then its doc file when it names one that exists.

    A missing doc is not an error: `check_tool_spec` does not require one, so refusing here
    would reject a tool the engine is otherwise happy to run.
    """
    parts = [spec["description"]]
    doc = base / spec["doc"] if spec.get("doc") else None
    if doc is not None and doc.is_file():
        parts.append(doc.read_text().strip())
    return "\n\n".join(part for part in parts if part)


def call_tool(
    name: str,
    arguments: dict[str, Any],
    paths: Paths,
    cancel: threading.Event | None = None,
) -> ToolCall:
    """Run one tool and report what happened, without raising on its failure.

    `FlowError` is what becomes a reported failure, and it covers more than a bad call: a
    tool that vanished and a spec.json that stopped parsing arrive the same way. Only the
    bad call is something the model can act on, but reporting the other two costs nothing
    and ending a paid-for turn over them would.

    `load_component` runs again even though `validate()` already resolved every granted
    tool, because this is a different process and the two can disagree.

    `cancel` is the caller withdrawing the request. Set it and the tool's process tree is
    stopped, and the answer says so rather than reading as a tool that broke.
    """
    started = time.monotonic()
    try:
        base, spec = load_component(paths, "tool", name)
        text = invoke(base, spec, arguments, paths, cancel=cancel)
    except Cancelled as exc:
        # Before FlowError, which it subclasses. The other order reports a withdrawn call as
        # an ordinary failure, and a caller that stopped waiting gets an answer anyway.
        return ToolCall(
            name=name, ok=False, text="", error=str(exc), ms=_since(started), cancelled=True
        )
    except FlowError as exc:
        return ToolCall(name=name, ok=False, text="", error=str(exc), ms=_since(started))
    return ToolCall(name=name, ok=True, text=text, ms=_since(started))


def _since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
