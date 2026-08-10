"""What is installed, reported rather than run.

`inventory` answers it for the whole lookup: every name that resolves, and which of the
definitions behind it won.

The rest answer it for one component. A name resolving through a layered lookup means a
flow can name an agent whose prompt was written by someone else, on another machine, and
nothing in the flow shows what it says. `inventory` names the file that won; these read it.
"""

from __future__ import annotations

import adapters
from commands.results import (
    AdapterDetail,
    AgentDetail,
    ComponentEntry,
    Inventory,
    KindListing,
    PackEntry,
    RefusedComponent,
    ToolDetail,
)
from engine.executor import load_agent, load_component
from paths.resolver import COMPONENT_DIRS, Paths, available_packs

# Flows first, then the component kinds, so a listing reads top-down from what you run to
# what it is built from.
LIST_ORDER = ("flow", *[kind for kind in COMPONENT_DIRS if kind != "flow"])


def inventory(paths: Paths) -> Inventory:
    """What is available by name, where each definition is, and what is hidden by what.

    Adapters are registered in code, so they have no roots to search and nothing can
    shadow them. Hence a separate field rather than one more kind in the list.
    """
    kinds = []
    for kind in LIST_ORDER:
        entries = tuple(
            ComponentEntry(
                name=name,
                path=path,
                display=paths.display(path),
                # Everything after the winner. More than one match is shadowing, and it is
                # worth reporting: it is the usual reason an edit appears to do nothing.
                shadows=tuple(paths.display(other) for other in paths.find_all(kind, name)[1:]),
            )
            for name, path in paths.list(kind).items()
        )
        kinds.append(KindListing(kind=kind, entries=entries))

    return Inventory(
        adapters=_adapters(paths),
        kinds=tuple(kinds),
        refused=_refused(paths),
        packs=_packs(paths),
    )


def _packs(paths: Paths) -> tuple[PackEntry, ...]:
    """Every pack that shipped, switched on or not.

    The off ones are the reason this is here. Their components are absent from everything
    above, and a listing that showed only what resolves would leave a person to conclude
    the engine has no git tools rather than that this machine has not enabled them.
    """
    return tuple(
        PackEntry(
            name=pack.name,
            path=pack.path,
            display=paths.display(pack.path),
            description=pack.description,
            enabled=pack.name in paths.config.packs,
            requires=pack.requires,
        )
        for pack in available_packs().values()
    )


def _refused(paths: Paths) -> tuple[RefusedComponent, ...]:
    """Definitions in the engine's own namespace, which nothing can reach by name.

    Reported because the alternative is silence: the directory is there, `list` would not
    mention it, and the name it claims fails somewhere else entirely.
    """
    return tuple(
        RefusedComponent(kind=kind, name=name, path=path, display=paths.display(path))
        for kind in LIST_ORDER
        for name, paths_for_name in paths.all_intruders(kind).items()
        for path in paths_for_name
    )


def _adapters(paths: Paths) -> tuple[ComponentEntry, ...]:
    """The registered adapters, as entries like any other, so a listing reads one way.

    `shadows` stays empty and always will: they are static imports in `ADAPTERS`, not
    names resolved through the roots, so there is no second definition to hide behind one.
    """
    return tuple(
        ComponentEntry(
            name=name,
            path=adapters.locate(module),
            display=paths.display(adapters.locate(module)),
        )
        for name, module in sorted(adapters.ADAPTERS.items())
    )


def agent_detail(name: str, paths: Paths) -> AgentDetail:
    """An agent's spec, and the prompt a turn would actually be handed.

    Read through `load_agent` rather than off the disk here, so what this shows and what
    `run` sends cannot drift apart. A view of the prompt that is not the prompt is worse
    than no view at all, and its failures are already worded for a person: an agent
    pointing at a file that is missing, or at one that is empty.
    """
    # Located and loaded separately: `load_agent` returns the spec and the prompt but not
    # the directory they came from, and going through `load_component` for that reports a
    # missing agent the same way a missing tool is reported one function down.
    base, _ = load_component(paths, "agent", name)
    spec, prompt = load_agent(paths, name)
    return AgentDetail(
        name=name,
        path=base,
        display=paths.display(base),
        spec=spec,
        prompt=prompt,
    )


def adapter_detail(name: str, paths: Paths) -> AdapterDetail:
    """One adapter's description and the settings schema an agent spec is checked against.

    Looked up in `ADAPTERS` rather than through the roots, because that is the whole of
    where an adapter can come from. `adapters.get` raises the message that lists the ones
    there are, which is the useful answer to a typo.
    """
    module = adapters.get(name)
    path = adapters.locate(module)
    return AdapterDetail(
        name=name,
        path=path,
        display=paths.display(path),
        description=getattr(module, "DESCRIPTION", ""),
        input_schema=getattr(module, "INPUT_SCHEMA", {}),
    )


def tool_detail(name: str, paths: Paths) -> ToolDetail:
    """A tool's spec, and its doc when it names one that is there.

    The name is the one it was looked up by, not `spec["name"]`, which for a namespaced
    tool carries only the leaf. That is the same rule an in-turn grant follows.
    """
    base, spec = load_component(paths, "tool", name)
    doc = base / spec["doc"] if spec.get("doc") else None
    return ToolDetail(
        name=name,
        path=base,
        display=paths.display(base),
        spec=spec,
        doc=doc.read_text().strip() if doc is not None and doc.is_file() else "",
    )
