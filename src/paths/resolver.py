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

Overriding is per *name*, not per directory: a project-level `read_file` replaces the
built-in and inherits nothing from it.

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


class LookupError_(RuntimeError):
    """A component could not be found, or a kind is not a component kind."""


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
    return Path(__file__).resolve().parents[1] / "builtin"


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
        return candidate.is_file() if kind == "flow" else (candidate / "spec.json").is_file()

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
        """Every available name of this kind, mapped to the definition that wins."""
        available: dict[str, Path] = {}
        for root in self.roots:
            for subdir in COMPONENT_DIRS[kind]:
                base = root / subdir
                if not base.is_dir():
                    continue
                entries = sorted(base.iterdir())
                for entry in entries:
                    name = entry.stem if kind == "flow" else entry.name
                    if name not in available and self._exists(kind, entry):
                        available[name] = entry
        return dict(sorted(available.items()))

    def _display(self, path: Path) -> str:
        """Shorten a path for messages: ./x inside the project, ~/x inside home."""
        for base, prefix in ((self.workspace, "."), (self.home, "~")):
            if path == base:
                return prefix
            try:
                return f"{prefix}/{path.relative_to(base)}"
            except ValueError:
                continue
        return str(path)

    def display(self, path: Path) -> str:
        return self._display(path)
