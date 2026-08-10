"""Layered lookup for tools, agents and flows.

Adapters are not here: they are Python modules registered in code. See that package's
docstring for why the two conventions differ.

A component is found by name, not by path. Roots are searched in precedence order and the
first match wins, so a project overrides what it inherits without a config file:

  1. $ATF_PATH     colon-separated roots, for tests and one-off overrides
  2. ./.arctic     this project, in a dot-directory
  3. ./            this project, at the top level
  4. ~/.arctic     you, across every project
  5. sources       extra roots named by ~/.arctic/config.yaml (see `paths/config.py`)
  6. packs         the shipped packs that config.yaml switched on (see `PACKS_DIR`)
  7. builtin/      what ships with the engine

A source sits below your own home directory on purpose. A shared library of tools is
something you opted into, and it may not quietly replace what this project or your own
`~/.arctic` defines, because then reading a flow would no longer tell you which definition
it runs. It cannot replace what shipped with the engine either, for the stronger version of
the same reason: see `ENGINE_NAMESPACE` below.

A pack sits below a source and above the built-ins, and where exactly makes no difference:
everything in one is named under `arctic/`, which no other root may define, so nothing it
holds is in competition with anything.

`display()` names a path by the layer it came out of, which is what `atf list` prints
beside every name: `./x` for the project, `$HOME/.arctic/x` for yours, `$ATF_ROOT/x` for
the engine's own.

A name may carry a namespace: `arctic/read_file` is `tools/arctic/read_file` under
whichever root wins. There is no depth limit and no declaration anywhere. A directory
holding a `spec.json` is a component, and any other directory is a namespace, so grouping
tools by purpose is done by moving the directories.

Overriding is per *name*, not per directory: a project-level `read_file` replaces the
built-in and inherits nothing from it. `arctic/read_file` and `read_file` are two names, so
neither one overrides or falls back to the other.

**One namespace is not overridable, and that is a security property rather than a
convenience.** The engine owns `arctic/`, and nothing outside `builtin/` may define a name
inside it. Without that, a flow reading `tool: arctic/read_file` says nothing about what
runs: whoever controls a higher root, including a repository you cloned, could put anything
there under a name that reads as the contained, no-network tool that ships. See
`ENGINE_NAMESPACE` and `Paths.intruders`.

Where a component is found does not change where it runs. Everything executes with the
working directory set to the project root, so a tool installed in your home directory
still acts on the project in front of it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from paths.config import CONFIG_FILE, Config, ConfigError, load

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

# Where the packs live under `builtin/`, and the file that makes a directory one of them.
#
# A pack is a set of components that ships with the engine and is switched off until
# `config.yaml` names it. Two things follow from where it sits, and both are the reason it
# sits there rather than being a source somebody clones.
#
# It is inside `builtin_root()`, so `arctic/` is a namespace it may define. That is the
# whole point: `tool: arctic/git/commit` in a flow has to mean the tool that shipped under
# that name, and a root outside the engine can never promise that (see `ENGINE_NAMESPACE`).
# So the pack mechanism costs the resolver nothing: the reservation, the refusal of an
# intruder, and the listing rules all already say "inside the built-in root or nowhere".
#
# And it ships in the binary, so enabling one is a line in a file rather than a download
# with a version, a checksum and a way to be absent. What "opt in" buys is not distribution
# but consent: a pack holds tools that write, and an install nobody configured has none of
# them, so nothing can be talked into a commit by a flow that was merely run.
PACKS_DIR = "packs"
PACK_FILE = "pack.json"

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

# The namespace the engine owns. A name whose first segment is this one resolves inside
# `builtin/` or nowhere, so `tool: arctic/read_file` in a flow is the shipped tool and
# cannot be anything else.
#
# It is a *vendor* segment, in the sense Composer's `vendor/package` and Java's reverse
# domain root are: the first segment says who a component came from, and who it came from
# is who may define it. So it is the project's own name and not a word about what is inside
# it: `common/` is what someone reaches for to group their own tools, and reserving that
# would take a word they had a use for. The whole namespace is reserved
# rather than only the names that ship today, for two reasons. A near miss like
# `arctic/read_files` would otherwise read as shipped while being anyone's, and reserving
# only what ships would mean each new built-in could collide with a name somebody had.
ENGINE_NAMESPACE = "arctic"


def reserved(name: str) -> bool:
    """Whether this name is the engine's to define.

    The first segment, so the whole namespace is covered at any depth. A bare name never
    is: `read_file` of your own is a different name from `arctic/read_file` and always was.
    """
    return name.split(SEPARATOR)[0] == ENGINE_NAMESPACE


def _inside(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


class LookupError_(RuntimeError):
    """A component could not be found, a kind is not a component kind, or a name is not one."""


def check_kind(kind: str) -> None:
    """Refuse anything that is not one of the kinds found by name.

    Beside `check_name` and called from the same place, so a lookup and a `create` refuse
    an unknown kind with the same sentence, which names the three there are.
    """
    if kind not in COMPONENT_DIRS:
        raise LookupError_(f"'{kind}' is not a component kind ({', '.join(COMPONENT_DIRS)})")


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


def packs_root() -> Path:
    """Where the packs that ship with the engine live, enabled or not."""
    return builtin_root() / PACKS_DIR


@dataclass(frozen=True)
class Pack:
    """One opt-in set of components that ships with the engine.

    A pack is an ordinary search root: `tools/`, `agents/`, `flows/`, laid out the way
    every other root is. What makes it one is a `pack.json` beside them, which is the same
    rule a component follows with its `spec.json`, so there is nothing to register.

    `name` is the directory name and not a field in the manifest. A name that could
    disagree with the directory it is written in is a name with two answers, and the
    directory is the one `config.yaml` has to spell.

    `requires` is what has to be on `PATH` for the pack to work, the way a tool spec's own
    `requires` is. Nothing enforces it. It is what a listing shows so that "the pack is
    on and the tool still fails" has somewhere to be answered.
    """

    name: str
    path: Path
    description: str = ""
    requires: tuple[str, ...] = ()


def available_packs() -> dict[str, Pack]:
    """Every pack that shipped, by name, whether or not it is switched on.

    Read off the filesystem rather than from a list in this module, so adding a pack is
    adding a directory. A pack whose `pack.json` cannot be read is skipped rather than
    raised: nothing here is user-written, so a broken one is a build that went wrong, and
    the failure worth showing is the tool that is then missing rather than a listing that
    will not print.
    """
    base = packs_root()
    if not base.is_dir():  # pragma: no cover (a build that dropped the data files)
        return {}

    found: dict[str, Pack] = {}
    for entry in sorted(base.iterdir()):
        manifest = entry / PACK_FILE
        if not manifest.is_file():
            continue
        try:
            document = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):  # pragma: no cover (a corrupt install)
            continue
        found[entry.name] = Pack(
            name=entry.name,
            path=entry,
            description=str(document.get("description", "")),
            requires=tuple(document.get("requires") or ()),
        )
    return found


def _in_root(kind: str, name: str, root: Path) -> list[Path]:
    """Where a component of this kind and name would sit under one root.

    A list rather than a path because a flow is a file and carries a suffix, and two
    spellings of it are legitimate. Taking a root as an argument rather than reading
    `Paths.roots` is what lets a pack that is *not* a search root be asked the same
    question: see `Paths._disabled_pack`.
    """
    found: list[Path] = []
    for subdir in COMPONENT_DIRS[kind]:
        if kind == "flow":
            found += [root / subdir / f"{name}{suffix}" for suffix in FLOW_SUFFIXES]
            # The bundle spelling: `flows/review/review.yaml` is the flow `review`, which
            # gives its prompts a directory that belongs to it. The file carries the leaf
            # and not the whole name, the way a namespaced spec.json does, so
            # `release/sign` is `flows/release/sign/sign.yaml`.
            #
            # After the flat candidates, not before, and `_collect` reads files before
            # directories to agree with it. A flow that is both spellings at once resolves
            # to the one that already worked, and `find_all` reports the other as
            # shadowing, exactly as it does for a `.yaml` and a `.yml` side by side.
            leaf = name.split(SEPARATOR)[-1]
            found += [root / subdir / name / f"{leaf}{suffix}" for suffix in FLOW_SUFFIXES]
        else:
            found.append(root / subdir / name)
    return found


def _bundle_file(directory: Path) -> Path | None:
    """The flow a directory is named for, when it holds one.

    What makes a directory under `flows/` a bundle is that it contains a flow of its own
    name. Any other one is a namespace, as it always was, and a bundle is *also* one:
    `flows/review/helper.yaml` stays `review/helper`, so nothing that resolved before
    stops resolving.
    """
    for suffix in FLOW_SUFFIXES:
        candidate = directory / f"{directory.name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _under(path: Path, base: Path, prefix: str) -> str | None:
    """`path` written against `base`, or None when it is not inside it."""
    if path == base:
        return prefix
    try:
        return f"{prefix}/{path.relative_to(base)}"
    except ValueError:
        return None


@dataclass
class Paths:
    """Resolves component names against the layered roots.

    `workspace` is the project root: the top search layer, and the working directory
    every component executes in.

    `config` is what `~/.arctic/config.yaml` said, read once here and handed around with
    the rest of the run's ambient context. The engine reads it for the run ceiling.
    """

    workspace: Path
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    home: Path | None = None
    config: Config = field(init=False)

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.home = Path(self.home).resolve() if self.home else Path.home()
        # Eagerly, not on first use. `roots` is read from worker threads, so a lazy load
        # would be a race for no gain, and a config that cannot be parsed should stop the
        # command rather than surface halfway through a run as a missing component.
        self.config = load(self.home / DOT_DIR)
        self._check_packs()

    def _check_packs(self) -> None:
        """Refuse a `packs:` entry that is not a pack that shipped.

        `Paths.roots` would otherwise turn a misspelled `gti` into a root that does not
        exist, which it drops, so the whole of what a typo does is make every tool in the
        pack quietly missing. That is the failure this exists to prevent, and it is worth
        stopping every command for: an unknown key in the same file already does.

        Here rather than in `paths/config.py` because knowing which packs exist means
        reading the built-in root, and that module deliberately imports nothing from here.
        """
        available = available_packs()
        unknown = [name for name in self.config.packs if name not in available]
        if unknown:
            offered = ", ".join(available) or "none ship with this build"
            raise ConfigError(
                f"{self.home / DOT_DIR / CONFIG_FILE}: no pack named "
                f"{', '.join(repr(name) for name in unknown)}. Packs: {offered}"
            )

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
            *self.config.sources,
            # Built by name rather than by scanning `packs/`, so the property that is read
            # from worker threads on every lookup touches the filesystem no more than it
            # did before. `_check_packs` has already refused a name that is not there.
            *(packs_root() / name for name in self.config.packs),
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
        check_kind(kind)
        check_name(name)
        return [place for root in self.roots for place in _in_root(kind, name, root)]

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
        """Every match that may be used, in precedence order. More than one is shadowing.

        A reserved name is matched inside the engine's root or not at all, so a definition
        of one anywhere else is absent from this rather than shadowing what ships. It does
        not silently lose either: `find` refuses outright. This one does not raise, because
        `commands.inventory` calls it for every listed name and a listing has to survive
        the thing it exists to report.
        """
        return [c for c in self._eligible(kind, name) if self._exists(kind, c)]

    def _eligible(self, kind: str, name: str) -> list[Path]:
        """The locations this name may legitimately come from, in precedence order.

        Every candidate for an ordinary name, and only the engine's own for a reserved one.
        `find` reports these when nothing matched, so a reserved name that is simply
        misspelled is not answered with a list of roots it would never have been taken from.
        """
        candidates = self._candidates(kind, name)
        if reserved(name):
            return [c for c in candidates if _inside(c, builtin_root())]
        return candidates

    def intruders(self, kind: str, name: str) -> list[Path]:
        """Definitions of a reserved name that are not the engine's, in precedence order.

        Empty for every ordinary name, so nothing but a reserved one pays for this.
        """
        if not reserved(name):
            return []
        return [
            candidate
            for candidate in self._candidates(kind, name)
            if self._exists(kind, candidate) and not _inside(candidate, builtin_root())
        ]

    def all_intruders(self, kind: str) -> dict[str, list[Path]]:
        """Every definition of a reserved name outside the engine, by name.

        For reporting, not resolving. `create` refuses to write one, so the only ways to
        have one are by hand and out of a source somebody else wrote. The second is the
        case worth naming out loud, and it is the one nobody would think to look for.
        """
        found: dict[str, list[Path]] = {}
        for root in self.roots:
            if _inside(root, builtin_root()):
                continue
            for subdir in COMPONENT_DIRS[kind]:
                base = root / subdir / ENGINE_NAMESPACE
                if not base.is_dir():
                    continue
                # A fresh mapping per root: `_collect` keeps the first of a name it sees,
                # and here every one of them is being collected rather than resolved.
                under_root: dict[str, Path] = {}
                self._collect(kind, base, f"{ENGINE_NAMESPACE}{SEPARATOR}", under_root)
                for name, path in under_root.items():
                    found.setdefault(name, []).append(path)
        return dict(sorted(found.items()))

    def find(self, kind: str, name: str) -> Path:
        # Before the match, and refusing even when the built-in exists and would have won.
        # Quietly preferring the shipped one would leave someone editing a directory that
        # does nothing, and the whole point is that this is said rather than assumed.
        if trespassing := self.intruders(kind, name):
            raise LookupError_(
                f"'{name}' is in '{ENGINE_NAMESPACE}{SEPARATOR}', which belongs to the "
                f"engine, so {self._display(trespassing[0])} may not define it. Give yours "
                f"a name of its own and change what names it"
            )
        matches = self.find_all(kind, name)
        if matches:
            return matches[0]

        # Before the list of places that were searched, because a pack that is off is not
        # one of them and the list would therefore be an answer to a question nobody asked.
        # A pack costs this scan only on a miss, which is already the failing path.
        if pack := self._disabled_pack(kind, name):
            config = self._display(self.home / DOT_DIR / CONFIG_FILE)
            raise LookupError_(
                f"{kind} '{name}' is in the '{pack.name}' pack, which is not enabled. "
                f"Add it to {config}:  packs: [{pack.name}]"
            )

        looked = ", ".join(self._display(c) for c in self._eligible(kind, name))
        raise LookupError_(f"unknown {kind} '{name}', looked in {looked}")

    def _disabled_pack(self, kind: str, name: str) -> Pack | None:
        """The pack that defines this name and is switched off, if one does.

        The reason a shipped tool can be missing and the ordinary message would not say
        so: every root it names was searched and the tool really is in none of them. What
        is wrong is one line in a config file, and nothing else was ever going to say that.
        """
        for pack in available_packs().values():
            if pack.name in self.config.packs:
                continue
            if any(self._exists(kind, place) for place in _in_root(kind, name, pack.path)):
                return pack
        return None

    def list(self, kind: str) -> dict[str, Path]:
        """Every available name of this kind, mapped to the definition that wins.

        Namespaced names are qualified, so a listing reads the way a flow spells them:
        `arctic/read_file`, not `read_file` in some directory the listing does not name.
        """
        available: dict[str, Path] = {}
        for root in self.roots:
            for subdir in COMPONENT_DIRS[kind]:
                base = root / subdir
                if base.is_dir():
                    # Reserved names are collected from the engine's root and nowhere else,
                    # and at insert time rather than afterwards: the roots are walked in
                    # precedence order, so an intruder would claim the name first and the
                    # built-in would never be reached.
                    self._collect(
                        kind,
                        base,
                        "",
                        available,
                        skip_reserved=not _inside(root, builtin_root()),
                    )

        # A reserved name somebody else also defines resolves to nothing until that
        # directory is renamed, so it is not offered here either. Listing it as available
        # while `find` refuses it would make the listing the one thing it must not be,
        # which is wrong about what a name does.
        for name in self.all_intruders(kind):
            available.pop(name, None)
        return dict(sorted(available.items()))

    def _collect(
        self,
        kind: str,
        base: Path,
        prefix: str,
        available: dict[str, Path],
        *,
        skip: Path | None = None,
        skip_reserved: bool = False,
    ) -> None:
        """Add every component under `base`, descending into namespaces.

        A directory holding a spec.json is a component, and the walk goes into it anyway.
        `find` resolves a name by joining it onto a root, so it would place a component
        nested inside another one; skipping those here would leave a listing that does not
        show everything the engine can be asked for.

        A dotted entry is not a namespace. It is a `.git`, an editor's cache or a
        `.DS_Store`, and descending into one lists whatever happens to be inside it.

        `skip` is a bundle's own flow file, which the caller has already listed under the
        bundle's name. Without it `flows/review/review.yaml` would be listed twice, once as
        `review` and once as `review/review`, and only one of those is a name anyone wrote.
        """
        # Files before directories, so a flat flow beats a bundle of the same name and this
        # listing agrees with what `find` returns. See the candidate order in `_in_root`.
        for entry in sorted(base.iterdir(), key=lambda item: (item.is_dir(), item.name)):
            if entry.name.startswith(".") or entry == skip:
                continue
            if self._exists(kind, entry):
                name = f"{prefix}{entry.stem if kind == 'flow' else entry.name}"
                # First one wins: the roots are walked in precedence order.
                if not (skip_reserved and reserved(name)):
                    available.setdefault(name, entry)
            if entry.is_dir():
                bundle = _bundle_file(entry) if kind == "flow" else None
                if bundle is not None:
                    name = f"{prefix}{entry.name}"
                    if not (skip_reserved and reserved(name)):
                        available.setdefault(name, bundle)
                self._collect(
                    kind,
                    entry,
                    f"{prefix}{entry.name}{SEPARATOR}",
                    available,
                    skip=bundle,
                    skip_reserved=skip_reserved,
                )

    def _display(self, path: Path) -> str:
        """Shorten a path for messages, by which layer it came out of.

        The order is the whole of this function, because these bases contain one another.
        A layer matched against the wrong base is named as the wrong layer, and the name is
        what the reader acts on.
        """
        # The built-in root first, because it sits inside one of the others in every
        # install: under the workspace from a checkout, under home from `install.sh`.
        # Matched later it would read as an ordinary project file, which is the one thing
        # it is not: nothing under it is yours and nothing under it is edited.
        #
        # Then the engine root it sits inside, so a shipped tool reads
        # `$ATF_ROOT/tools/read_file` rather than gaining a `builtin/` segment that means
        # nothing to a reader. `$ATF_ROOT` is a label for "this came with the engine", not
        # a directory, so what hangs off it is the vocabulary a person already has.
        for base, prefix in ((builtin_root(), ENGINE_SYMBOL), (engine_root(), ENGINE_SYMBOL)):
            if (shortened := _under(path, base, prefix)) is not None:
                return shortened

        # A source, before the two it commonly sits inside. `~/work/components` is under
        # home and a source named inside the project is under the workspace, so shortening
        # it against either would print a sourced component as `$HOME/...` or, worse, as
        # `./...`: the project's own, which is exactly what it is not. Left absolute
        # rather than given a symbol, because there can be several and one symbol could
        # not say which.
        if any(_inside(path, source) for source in self.config.sources):
            return str(path)

        for base, prefix in ((self.workspace, "."), (self.home, HOME_SYMBOL)):
            if (shortened := _under(path, base, prefix)) is not None:
                return shortened
        return str(path)

    def display(self, path: Path) -> str:
        return self._display(path)
