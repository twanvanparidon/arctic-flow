"""What the installation says about itself: `~/.arctic/config.yaml`.

Beside the resolver rather than in a package of its own, because `Paths` already carries
the ambient knowledge of one run: which workspace, which environment, which home
directory. A setting read out of the home directory is the same kind of fact, and the
engine reads `paths.config` exactly as it reads `paths.workspace`.

Three settings, and they are deliberately few:

  run.max_minutes   a ceiling on a whole run. A safeguard, not a tuning knob, which is
                    why no flow can raise it. `engine.executor.execute` enforces it.
                    Minutes because the useful values are hours: 240 reads as four of
                    them and 14400 reads as nothing.
  sources           extra roots to search, each laid out as `tools/`, `agents/`, `flows/`.
                    `Paths.roots` splices them in below `~/.arctic` and above the
                    built-ins, so a sourced library never replaces what the project or
                    your own home directory defines. It cannot replace a shipped
                    component either: see `ENGINE_NAMESPACE` in the resolver.
  packs             which of the shipped tool packs are switched on. A pack is the
                    engine's own, so unlike a source it may define names under
                    `arctic/`, and unlike a source it arrives with the binary rather
                    than being cloned. See `PACKS_DIR` in the resolver.

A name here is checked, not just parsed: `Paths` refuses a pack that does not ship. That
check is in the resolver rather than here, because it needs to read the built-in root and
this module deliberately knows nothing about where that is.

Anything a flow should decide belongs in the flow, and anything a component should decide
belongs in its spec. What is left is the per-machine policy neither of those can hold.

**An unknown key is refused rather than ignored.** This file is written by hand and read
by nothing else, so a mistyped `max_second` that silently does nothing is exactly the
failure it exists to prevent. Absent is not the same as unknown: no file at all is the
ordinary case and loads as the defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

CONFIG_FILE = "config.yaml"

# `additionalProperties: false` at both levels is the point of having a schema here at
# all. Everything is optional, so a config holding only `sources` is a complete one.
CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "run": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                # No floor beyond "positive". A fraction is a legitimate thing to write:
                # it is how a test, or anyone checking the ceiling works, asks for one
                # that fires in seconds.
                "max_minutes": {"type": "number", "exclusiveMinimum": 0},
            },
        },
        "sources": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        # No enum of the packs that ship. The list is a directory on disk, and repeating
        # it here would be a second copy to keep in step with a `mkdir`. `Paths` checks
        # the name against what is actually there and names the alternatives when it is
        # not, which is the better error anyway.
        "packs": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
}


class ConfigError(RuntimeError):
    """The config file exists but is not one the engine can read."""


@dataclass(frozen=True)
class Config:
    """The settings, already parsed. The defaults are what no config file means.

    `max_run_minutes` is `None` for "no ceiling", rather than a very large number: the
    engine skips the whole deadline path when there is nothing to enforce, so a run
    without a config behaves exactly as it did before there was one.

    Kept in the unit the file was written in rather than converted to seconds here, so
    the failure the engine raises can name the number someone typed. `execute` does the
    one multiplication.
    """

    max_run_minutes: float | None = None
    sources: tuple[Path, ...] = ()
    # Names rather than paths, and not de-duplicated or sorted: `Paths` turns each one
    # into a root under the built-ins, and listing a pack twice costs a root that is
    # dropped as a duplicate there.
    packs: tuple[str, ...] = ()


def load(directory: Path) -> Config:
    """Read `config.yaml` out of `directory`, which is a `.arctic`.

    Takes the directory rather than the home path, so this module needs nothing from the
    resolver and the import runs one way.
    """
    path = directory / CONFIG_FILE
    if not path.is_file():
        return Config()

    try:
        document = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise ConfigError(f"{path} should hold a mapping of settings")

    _check(document, path)
    return Config(
        max_run_minutes=(document.get("run") or {}).get("max_minutes"),
        sources=tuple(_source(entry, path) for entry in document.get("sources") or []),
        packs=tuple(document.get("packs") or []),
    )


def _check(document: dict[str, Any], path: Path) -> None:
    """Every problem with the document, not just the first.

    Someone who mistyped two keys should get both, since the file is edited once and read
    again on the next command. Same shape as `engine.executor.check_payload`, for the same
    reason: an error naming where it happened is the difference between fixing it and
    hunting for it.
    """
    errors = sorted(
        Draft202012Validator(CONFIG_SCHEMA).iter_errors(document), key=lambda e: list(e.path)
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors
        )
        raise ConfigError(f"{path}: {detail}")


def _source(entry: str, path: Path) -> Path:
    """One extra search root. `~` expands; anything relative is refused.

    A relative root would be resolved against the working directory, which is wherever
    `atf` happened to be run from. That makes the same config mean a different thing in
    every directory, and the failure it produces is a component that cannot be found.

    Existence is deliberately not checked. A root that is not there is skipped by
    `Paths.roots` the way an absent `~/.arctic` already is, so a source on a drive that is
    not mounted costs a missing component rather than every command refusing to start.
    """
    resolved = Path(entry).expanduser()
    if not resolved.is_absolute():
        raise ConfigError(
            f"{path}: source '{entry}' is relative, and a search root has to name one "
            "place. Write it from / or from ~"
        )
    return resolved
