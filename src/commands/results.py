"""What a command hands back.

One dataclass per command, holding **facts rather than sentences**: a path and a count,
not `wrote ./secrets.vault (1 secret)`. A TUI can bind these fields to widgets without
parsing them back out of a formatted string.

Two conventions worth knowing before adding one:

**Every result carries a path twice**, as a `Path` and as the shortened `display` string.
Shortening needs to know where the workspace and home directory are, which is knowledge
`Paths` has. Asking a renderer to acquire it puts the resolver in the presentation layer.

**A result never carries a stream, a colour, or a width.** If a field only makes sense for
one front end, it belongs in that front end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paths.resolver import Paths
from vault.vault import Vault


@dataclass(frozen=True)
class FlowPlan:
    """A flow that is ready to run: resolved, its inputs checked, its vault unlocked.

    The one dataclass here that is an *input* rather than something to display. It is what
    passes between `prepare` and `run` (see `commands.flows`), and it carries `paths` so a
    plan describes the whole run and `run(plan)` needs nothing else.
    """

    paths: Paths
    definition: dict[str, Any]
    path: Path
    display: str
    inputs: dict[str, Any] = field(default_factory=dict)
    vault: Vault | None = None

    @property
    def name(self) -> str:
        """The flow's declared name, which need not match its filename."""
        return str(self.definition["flow"])


@dataclass(frozen=True)
class RunResult:
    """A flow that ran. `output` is the flow's own text, unchanged and unframed."""

    flow: str
    path: Path
    display: str
    output: str
    trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        """What the run spent, over the steps that reported a cost.

        Summed here rather than by each front end: a tool-only flow reports `None` per
        step, and the `or 0` that handles it gets forgotten in one place out of every few.
        """
        return sum(entry.get("cost_usd") or 0 for entry in self.trace)


@dataclass(frozen=True)
class LintResult:
    """A flow that passed validation. `steps` is what `validate` returned, in order."""

    flow: str
    path: Path
    display: str
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class GraphResult:
    """A flow's push edges, already rendered as text by `util.graph`."""

    flow: str
    path: Path
    display: str
    text: str


@dataclass(frozen=True)
class DiagramResult:
    """Mermaid markdown, already rendered by `util.mermaid`."""

    flow: str
    path: Path
    display: str
    markdown: str


@dataclass(frozen=True)
class ToolDescription:
    """One tool as a model needs to see it, rather than as a step needs to run it.

    `description` is the spec's own description followed by its `doc` file. A flow author
    picks a tool by reading the YAML around it; a model picks by reading this, and the doc
    is where "when not to use this" is written.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """One tool that ran, or tried to. `text` is its stdout, empty when it failed.

    `ok` false is an ordinary outcome here, not an exception, which is the one way this
    differs from a tool step. A tool called inside an agent's turn reports its failure to
    the model, which can pick different arguments and try again. Raising would end the turn
    over something recoverable, after it had already been paid for.

    `error` is not scrubbed, because the server that builds it has no vault. Nothing is
    granted to an in-turn call, so there is no secret in reach to appear in one.

    `cancelled` is not a third kind of failure. `ok` is false either way, but the caller
    withdrew the request rather than the tool going wrong, so a server answers it with
    nothing at all and a progress display must not call it failed.
    """

    name: str
    ok: bool
    text: str
    error: str | None = None
    ms: int = 0
    cancelled: bool = False


@dataclass(frozen=True)
class ComponentEntry:
    """One available name, and the definitions a higher-precedence root is hiding.

    `shadows` holds display strings rather than paths: a renderer that had to shorten
    them would need the resolver.
    """

    name: str
    path: Path
    display: str
    shadows: tuple[str, ...] = ()


@dataclass(frozen=True)
class KindListing:
    """Every available component of one kind. Empty is normal, not an error."""

    kind: str
    entries: tuple[ComponentEntry, ...] = ()


@dataclass(frozen=True)
class Inventory:
    """What is installed: adapters, which are registered in code, and the rest, which
    are found on disk. The two are separate because nothing can shadow an adapter."""

    adapters: dict[str, str] = field(default_factory=dict)
    kinds: tuple[KindListing, ...] = ()


@dataclass(frozen=True)
class RootReport:
    """One search root, and which component directories it actually has."""

    path: Path
    display: str
    subdirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class PathsReport:
    """Where the engine looks, in the order it looks. `roots` is that order."""

    roots: tuple[RootReport, ...] = ()
    workspace: Path = Path()


@dataclass(frozen=True)
class VaultCreated:
    """A vault that was written. `count` is how many secrets went into it."""

    path: Path
    display: str
    count: int


@dataclass(frozen=True)
class SecretSet:
    """One secret written. `replaced` distinguishes an overwrite from an addition.
    The caller cannot tell afterwards, and it is the one thing worth reporting."""

    path: Path
    display: str
    name: str
    replaced: bool


@dataclass(frozen=True)
class SecretListing:
    """Secret names, sorted. No values, which is the point of it."""

    path: Path
    display: str
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class VaultContents:
    """Decrypted secrets. This holds real credentials: log nothing from it by habit."""

    path: Path
    display: str
    values: dict[str, str] = field(default_factory=dict)
