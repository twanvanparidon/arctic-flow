"""Layered lookup for tools, agents and flows.

Adapters are not here: they are Python modules registered in code. See that package's
docstring for why the two conventions differ.

A component is found by name, not by path. Roots are searched in precedence order and the
first match wins, so a project overrides what it inherits without a config file:

  1. $ATF_PATH     colon-separated roots, for tests and one-off overrides
  2. ./.arctic     this project, in a dot-directory
  3. ./            this project, at the top level
  4. ~/.arctic     you, across every project
  5. builtin/      what ships with the engine

`display()` names a path by the layer it came out of, which is what `atf list` prints
beside every name: `./x` for the project, `$HOME/.arctic/x` for yours, `$ATF_ROOT/x` for
the engine's own.

A name may carry a namespace: `common/read_file` is `tools/common/read_file` under
whichever root wins. There is no depth limit and no declaration anywhere. A directory
holding a `spec.json` is a component, and any other directory is a namespace, so grouping
tools by purpose is done by moving the directories.

Overriding is per *name*, not per directory: a project-level `read_file` replaces the
built-in and inherits nothing from it. `common/read_file` and `read_file` are two names, so
neither one overrides or falls back to the other.

Where a component is found does not change where it runs. Everything executes with the
working directory set to the project root, so a tool installed in your home directory
still acts on the project in front of it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The subdirectory each kind lives in, under any root. One spelling per kind: a root
# is a project, a home directory, or the engine's own src/, and all three lay their
# components out the same way.
COMPONENT_DIRS: dict[str, tuple[str, ...]] = {
    "tool": ("tools",),
    "agent": ("agents",),
    "flow": ("flows",),
}

FLOW_SUFFIXES = (".yaml", ".yml")

DOT_DIR = ".arctic"
PATH_ENV = "ATF_PATH"

# How a shortened path names the layer it came from. `$HOME` is the real variable, so what
# is printed can be pasted into a shell and resolve.
#
# `$ATF_ROOT` is not a variable anything reads, and is deliberately not one: where the
# engine's own files live is decided by where it was installed, and `$ATF_PATH` already
# exists for putting a root in front of them. It is a label for "this came with the
# engine", which is the useful fact. The absolute path is inside a PyInstaller bundle in a
# release build, and is nothing anyone would open.
#
# Not `$ATF_BIN`, which names something else that exists: `install.sh` links the executable
# into `<prefix>/bin` and unpacks this directory under `<prefix>/lib`. `ROOT` is the
# `GOROOT` sense, "where the tool is installed", and does not read as `$HOME` on the line
# above it.
HOME_SYMBOL = "$HOME"
ENGINE_SYMBOL = "$ATF_ROOT"

# What separates a namespace from the name inside it, and how that is spelled where a
# slash is not allowed. The one place it is not allowed is a protocol name: an MCP tool
# goes to a model as `mcp__<server>__<tool>`, and a slash there is not a legal tool name.
# `cli.mcp_server` offers the flat spelling and `adapters.claude_code` allows the same
# string, so the rule lives here rather than being written out at both ends.
SEPARATOR = "/"
FLAT_SEPARATOR = "__"

# Segments that name something other than a directory inside the current one. Refused, so
# a name cannot walk out of the root it was resolved against.
REFUSED_SEGMENTS = ("", ".", "..")


class LookupError_(RuntimeError):
    """A component could not be found, a kind is not a component kind, or a name is not one."""


def check_name(name: str) -> None:
    """Refuse a name that would resolve to something outside the search roots.

    `root / subdir / name` resolves whatever it is handed, so `../../etc` would reach a
    spec.json no root contains. Namespaces make the slash part of a legitimate name, which
    is why the segments are what gets checked and not the separator.

    Called from `_candidates`, so every lookup goes through it. A name that `list()`
    produces came off the filesystem and cannot fail this.
    """
    if not name.strip():
        raise LookupError_("a component name cannot be empty")
    if any(segment in REFUSED_SEGMENTS for segment in name.split(SEPARATOR)):
        raise LookupError_(
            f"'{name}' is not a component name. A namespace is written 'group/name', and "
            "every part of it has to name a directory, so an empty part, '.' and '..' are "
            "refused"
        )


def flat_name(name: str) -> str:
    """A component name with the separator spelled for somewhere a slash cannot go.

    Not reversible by string surgery, because `git__commit` is itself a legal directory
    name and flattens to the same thing as `git/commit`. Whoever needs the way back keeps
    the mapping they built from the names they already had; `engine.executor.validate`
    refuses a grant where two of them collide, so there is one mapping to keep.
    """
    return name.replace(SEPARATOR, FLAT_SEPARATOR)


def builtin_root() -> Path:
    """Where the components that ship with the engine live.

    One expression for all three ways the engine runs, because the data sits in the same
    place relative to this package in each:

      from source   src/builtin, beside this package
      installed     site-packages/builtin, as package data of `builtin`
      frozen        <bundle>/builtin, via collect_data_files("builtin")

    An earlier version walked up to src/ and needed a separate frozen branch reading
    sys._MEIPASS. Making the bundle mirror the package removed the need for both.
    """
    return engine_root() / "builtin"


def engine_root() -> Path:
    """Everything that shipped with the engine: the built-in components and the adapters.

    One directory above the built-ins, which is the package directory from source and
    installed, and the bundle in a frozen build. It is what `$ATF_ROOT` stands for, so an
    adapter and a built-in tool are both reported as the engine's own rather than as two
    unrelated absolute paths.
    """
    return Path(__file__).resolve().parents[1]


@dataclass
class Paths:
    """Resolves component names against the layered roots.

    `workspace` is the project root: the top search layer, and the working directory
    every component executes in.
    """

    workspace: Path
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    home: Path | None = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.home = Path(self.home).resolve() if self.home else Path.home()

    @property
    def roots(self) -> list[Path]:
        """Search roots in precedence order, de-duplicated, existing ones only.

        Running from the engine's own checkout collapses several of these onto the
        same directory, so duplicates are dropped rather than searched twice.
        """
        candidates: list[Path] = []

        for entry in (self.env.get(PATH_ENV) or "").split(os.pathsep):
            if entry.strip():
                candidates.append(Path(entry.strip()).expanduser())

        candidates += [
            self.workspace / DOT_DIR,
            self.workspace,
            self.home / DOT_DIR,
            builtin_root(),
        ]

        ordered: list[Path] = []
        for candidate in candidates:
            resolved = candidate.expanduser()
            try:
                resolved = resolved.resolve()
            except OSError:  # pragma: no cover (unreadable path)
                continue
            if resolved not in ordered and resolved.is_dir():
                ordered.append(resolved)
        return ordered

    def _candidates(self, kind: str, name: str) -> list[Path]:
        """Every location a component of this kind and name could occupy."""
        if kind not in COMPONENT_DIRS:
            raise LookupError_(f"'{kind}' is not a component kind ({', '.join(COMPONENT_DIRS)})")
        check_name(name)

        found: list[Path] = []
        for root in self.roots:
            for subdir in COMPONENT_DIRS[kind]:
                if kind == "flow":
                    found += [root / subdir / f"{name}{suffix}" for suffix in FLOW_SUFFIXES]
                else:
                    found.append(root / subdir / name)
        return found

    @staticmethod
    def _exists(kind: str, candidate: Path) -> bool:
        # A flow is a file; everything else is a directory holding a spec.json. An
        # empty directory is not a component, so requiring the spec keeps a stray
        # folder from shadowing a real definition further down the precedence list.
        #
        # The suffix is checked as well as the file, which `_candidates` has already
        # decided for it. `list()` has not: it walks whatever is in the directory, and
        # without this a `notes.md` beside the flows would be listed as a flow.
        if kind == "flow":
            return candidate.suffix in FLOW_SUFFIXES and candidate.is_file()
        return (candidate / "spec.json").is_file()

    def find_all(self, kind: str, name: str) -> list[Path]:
        """Every match, in precedence order. More than one means shadowing."""
        return [c for c in self._candidates(kind, name) if self._exists(kind, c)]

    def find(self, kind: str, name: str) -> Path:
        matches = self.find_all(kind, name)
        if matches:
            return matches[0]
        looked = ", ".join(self._display(c) for c in self._candidates(kind, name))
        raise LookupError_(f"unknown {kind} '{name}', looked in {looked}")

    def list(self, kind: str) -> dict[str, Path]:
        """Every available name of this kind, mapped to the definition that wins.

        Namespaced names are qualified, so a listing reads the way a flow spells them:
        `common/read_file`, not `read_file` in some directory the listing does not name.
        """
        available: dict[str, Path] = {}
        for root in self.roots:
            for subdir in COMPONENT_DIRS[kind]:
                base = root / subdir
                if base.is_dir():
                    self._collect(kind, base, "", available)
        return dict(sorted(available.items()))

    def _collect(self, kind: str, base: Path, prefix: str, available: dict[str, Path]) -> None:
        """Add every component under `base`, descending into namespaces.

        A directory holding a spec.json is a component, and the walk goes into it anyway.
        `find` resolves a name by joining it onto a root, so it would place a component
        nested inside another one; skipping those here would leave a listing that does not
        show everything the engine can be asked for.

        A dotted entry is not a namespace. It is a `.git`, an editor's cache or a
        `.DS_Store`, and descending into one lists whatever happens to be inside it.
        """
        for entry in sorted(base.iterdir()):
            if entry.name.startswith("."):
                continue
            if self._exists(kind, entry):
                name = f"{prefix}{entry.stem if kind == 'flow' else entry.name}"
                # First one wins: the roots are walked in precedence order.
                available.setdefault(name, entry)
            if entry.is_dir():
                self._collect(kind, entry, f"{prefix}{entry.name}{SEPARATOR}", available)

    def _display(self, path: Path) -> str:
        """Shorten a path for messages, by which layer it came out of.

        The built-in root is tried first because it sits inside one of the other two in
        every install: under the workspace from a checkout, under home from `install.sh`.
        Matched later it would read as an ordinary project file, which is the one thing it
        is not: nothing under it is yours and nothing under it is edited.
        """
        # The built-in root before the engine root it sits inside, so a shipped tool reads
        # `$ATF_ROOT/tools/read_file` rather than gaining a `builtin/` segment that means
        # nothing to a reader. `$ATF_ROOT` is a label for "this came with the engine", not
        # a directory, so what hangs off it is the vocabulary a person already has: its
        # tools, its agents, its adapters.
        for base, prefix in (
            (builtin_root(), ENGINE_SYMBOL),
            (engine_root(), ENGINE_SYMBOL),
            (self.workspace, "."),
            (self.home, HOME_SYMBOL),
        ):
            if path == base:
                return prefix
            try:
                return f"{prefix}/{path.relative_to(base)}"
            except ValueError:
                continue
        return str(path)

    def display(self, path: Path) -> str:
        return self._display(path)
