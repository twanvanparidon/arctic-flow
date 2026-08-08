"""Creating `~/.arctic`: the layer that is yours across every project.

The resolver already searches it (see `paths/resolver.py`), and has since before this
command existed. What it cannot do is search a directory that is not there, so this writes
the tree and the config file that layer reads.

Unlike `create`, an existing directory is not a failure. `init` is run again after an
upgrade, or on a machine where half of it is already present, and the useful answer is
"here is what was missing" rather than a refusal. Nothing that exists is touched or read,
so running it over a config you have edited cannot lose it.

The config file is copied out of `builtin/scaffolds/`, for the same reason a tool's
`run.sh` is: what ships is a real YAML file, so its comments are the documentation and
they cannot drift from a string in this module.
"""

from __future__ import annotations

from commands.results import HomeInitialised
from paths.config import CONFIG_FILE
from paths.resolver import COMPONENT_DIRS, DOT_DIR, Paths, builtin_root

# Where the shipped config.yaml lives. `home` is not a component kind and nothing resolves
# under it; it sits beside the three that are because the rule it follows is theirs.
SCAFFOLD = builtin_root() / "scaffolds" / "home" / CONFIG_FILE


def initialise(paths: Paths) -> HomeInitialised:
    """Create `~/.arctic`, its component directories, and its config file.

    Reports what it wrote and what was already in place, as names relative to the
    directory, so a front end can say which of the two happened without diffing.
    """
    root = paths.home / DOT_DIR
    created: list[str] = []
    existing: list[str] = []

    # One entry per kind, in the order the resolver declares them, so the listing reads
    # the same way `atf list` does.
    for subdirs in COMPONENT_DIRS.values():
        for subdir in subdirs:
            target = root / subdir
            (created if not target.is_dir() else existing).append(f"{subdir}/")
            target.mkdir(parents=True, exist_ok=True)

    config = root / CONFIG_FILE
    if config.exists():
        existing.append(CONFIG_FILE)
    else:
        # Read before the write, so a scaffold that cannot be read leaves nothing behind.
        config.write_text(SCAFFOLD.read_text())
        created.append(CONFIG_FILE)

    return HomeInitialised(
        path=root,
        display=paths.display(root),
        created=tuple(created),
        existing=tuple(existing),
    )
