"""Commands that report on the installation rather than on a flow.

Neither runs anything or touches a flow file. They answer the two questions that come up
when a name does not resolve to what you expected: what is available, and where the engine
looked for it.
"""

from __future__ import annotations

import adapters
from commands.results import (
    ComponentEntry,
    Inventory,
    KindListing,
    PathsReport,
    RootReport,
)
from paths.resolver import COMPONENT_DIRS, Paths

# Flows first, then the component kinds, so a listing reads top-down from what you run to
# what it is built from.
LIST_ORDER = ("flow", *[kind for kind in COMPONENT_DIRS if kind != "flow"])


def inventory(paths: Paths) -> Inventory:
    """What is available by name, and what a higher-precedence root is hiding.

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

    return Inventory(adapters=adapters.describe(), kinds=tuple(kinds))


def search_paths(paths: Paths) -> PathsReport:
    """Where the engine looks, in the order it looks. First match wins.

    `subdirs` is what each root actually contains, not what it could. A root listed with
    nothing in it is the answer to "why is my tool not found".
    """
    roots = tuple(
        RootReport(
            path=root,
            display=paths.display(root),
            subdirs=tuple(
                sorted(
                    {
                        subdir
                        for kind in COMPONENT_DIRS
                        for subdir in COMPONENT_DIRS[kind]
                        if (root / subdir).is_dir()
                    }
                )
            ),
        )
        # One pass over the property: `roots` re-derives the list on every access.
        for root in paths.roots
    )
    return PathsReport(roots=roots, workspace=paths.workspace)
