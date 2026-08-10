"""Writing a new component, from the scaffold that ships with the engine.

The one command that creates a component rather than reading one. What it writes is data
under `builtin/scaffolds/`, not strings in this module, for the same reason the built-in
tools are data: a scaffolded `run.sh` is shell, and shell in this repository is read by
`shellcheck` and edited as shell.

Where it writes is the top of the lookup's own precedence: `./.arctic` when the project
has one, and the project root otherwise, so what is created is what `run` then resolves.
The two roots below those are deliberately not offered. `$ATF_PATH` is for tests and
one-off overrides, and `~/.arctic` is not this project.

Nothing is overwritten, and a name that already resolves elsewhere is still allowed:
writing a component into the project to override one it inherits is what the layered
lookup is for.
"""

from __future__ import annotations

import stat
from pathlib import Path

from commands.results import ComponentCreated
from paths.resolver import (
    COMPONENT_DIRS,
    DOT_DIR,
    ENGINE_NAMESPACE,
    FLOW_SUFFIXES,
    SEPARATOR,
    LookupError_,
    Paths,
    builtin_root,
    check_kind,
    check_name,
    reserved,
)

SCAFFOLDS = builtin_root() / "scaffolds"

# The flow scaffold is one file and lands under the flow's own name, so its source is
# named here rather than found by listing the directory.
FLOW_SCAFFOLD = "flow.yaml"

# What stands in for the component's name inside a scaffold. Not `{{ name }}`: a flow
# scaffold is full of real templates that the engine resolves at run time, and a
# placeholder spelled like one of those is a placeholder someone will try to run.
PLACEHOLDER = "__NAME__"

# Scaffold files that have to end up executable. Set on the way out rather than copied
# from the packaged scaffold, so a copy that arrived through a wheel or a frozen bundle
# without its mode still produces a tool the engine can run.
EXECUTABLE = ("run.sh",)


def create(kind: str, name: str, paths: Paths) -> ComponentCreated:
    """Write a new component of `kind` under `name`, and report what it is made of."""
    check_kind(kind)
    check_name(name)
    if reserved(name):
        # Refused here as well as in the resolver, so the answer arrives before the
        # directory exists rather than the first time something tries to run it.
        leaf = name.rsplit(SEPARATOR, 1)[-1]
        raise LookupError_(
            f"'{ENGINE_NAMESPACE}{SEPARATOR}' belongs to the engine, so '{name}' is not a "
            f"name to create. Put yours in a namespace of your own: "
            f"'{kind} <yours>{SEPARATOR}{leaf}'"
        )
    if kind == "flow" and name.endswith(FLOW_SUFFIXES):
        # Otherwise flows/review.yaml.yaml, resolved under a name nobody would type. Split
        # off the suffix rather than taking Path.stem, which would drop the namespace too.
        bare = name.rsplit(".", 1)[0]
        raise LookupError_(
            f"a flow is named rather than pathed: 'create flow {bare}' writes "
            f"{COMPONENT_DIRS['flow'][0]}/{bare}{FLOW_SUFFIXES[0]}"
        )

    # A workspace that is not there is a mistyped one far more often than it is a project
    # about to exist, and this is the only command that would answer it by creating the
    # whole tree somewhere nobody meant.
    if not paths.workspace.is_dir():
        raise NotADirectoryError(f"{paths.workspace} is not a directory, so there is no project")

    target = _target(kind, name, paths)
    if target.exists():
        # A FileExistsError, so a front end that already catches OSError catches this too.
        # One argument rather than two, because errno formatting would put "[Errno 17]" in
        # front of a sentence that reads better without it.
        raise FileExistsError(f"{paths.display(target)} already exists, so nothing was written")

    if kind == "flow":
        # One file, and `target` is it. Nothing to list beside the path it was written to.
        text = _read(SCAFFOLDS / kind / FLOW_SCAFFOLD, kind, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write(target, text)
        files: tuple[str, ...] = ()
    else:
        # Every file read and substituted before the directory exists, so a scaffold that
        # cannot be read leaves no half-written component behind.
        contents = {
            source.name: _read(source, kind, name)
            for source in sorted((SCAFFOLDS / kind).iterdir())
        }
        target.mkdir(parents=True)
        for filename, body in contents.items():
            _write(target / filename, body)
        files = tuple(contents)

    return ComponentCreated(
        kind=kind,
        name=name,
        path=target,
        display=paths.display(target),
        files=files,
    )


def _target(kind: str, name: str, paths: Paths) -> Path:
    """Where the component goes: a file for a flow, a directory for everything else.

    The same split the resolver makes when it looks one up, so a namespaced name lands in
    the directory the lookup would search for it.
    """
    base = _destination(paths) / COMPONENT_DIRS[kind][0]
    if kind == "flow":
        return base / f"{name}{FLOW_SUFFIXES[0]}"
    return base / name


def _destination(paths: Paths) -> Path:
    """The project's dot-directory, or the project root when there is no dot-directory.

    The first two search roots, in the order the resolver reads them. A project that keeps
    a `.arctic` keeps its components in it, and one that does not gets `./flows` beside
    what is already there.
    """
    dot = paths.workspace / DOT_DIR
    return dot if dot.is_dir() else paths.workspace


def _read(source: Path, kind: str, name: str) -> str:
    return source.read_text().replace(PLACEHOLDER, _declared_name(kind, name))


def _declared_name(kind: str, name: str) -> str:
    """What the scaffold should call itself.

    A flow is named by the whole thing, since that is what `atf run` is handed. A tool or
    an agent carries only the leaf: its namespace is which directory it sits in, which a
    spec.json has no way of knowing.
    """
    return name if kind == "flow" else name.rsplit(SEPARATOR, 1)[-1]


def _write(path: Path, text: str) -> None:
    path.write_text(text)
    if path.name in EXECUTABLE:
        # What `chmod +x` does, rather than a flat 0o755: the umask decided who may read
        # the file, and a scaffold is not the place to widen that. S_IMODE drops the file
        # type bits, which chmod(2) is not defined to be handed.
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | (mode & 0o444) >> 2)
