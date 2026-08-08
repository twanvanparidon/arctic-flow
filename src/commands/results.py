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
class FlowIssue:
    """One flow that did not validate, and the first thing that stopped it.

    `error` is the message the exception carried, kept as text: a sweep reports on flows
    it did not stop for, so there is nothing left to raise by the time this is built.
    """

    flow: str
    path: Path
    display: str
    error: str


@dataclass(frozen=True)
class LintReport:
    """Every flow in scope, checked. `issues` empty is the success case.

    Two tuples rather than one list of outcomes, so "did anything fail" is a field rather
    than a scan, and a caller that only wants the failures does not filter for them.
    """

    checked: tuple[LintResult, ...] = ()
    issues: tuple[FlowIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class AgentDetail:
    """One agent as it would be run: its spec, and the prompt a turn would be given.

    `spec` stays the parsed document rather than being exploded into fields here.
    `AGENT_SPEC_SCHEMA` is the contract for what may be in it, and a dataclass mirroring
    that schema is a second copy of it that goes stale the first time a field is added.
    """

    name: str
    path: Path
    display: str
    spec: dict[str, Any]
    prompt: str


@dataclass(frozen=True)
class ToolDetail:
    """One tool as it would be run: its spec, and its doc file when it has one.

    `doc` is empty rather than absent when there is none, because `TOOL_SPEC_SCHEMA` does
    not require one and a tool the engine will happily run is not a failure to report.
    """

    name: str
    path: Path
    display: str
    spec: dict[str, Any]
    doc: str = ""


@dataclass(frozen=True)
class AdapterDetail:
    """One adapter: what it runs, and the settings an agent spec may ask it for.

    `input_schema` is the contract `engine.specs` validates an agent's settings against,
    so this is the answer to "what may I put in a spec that names this adapter".
    """

    name: str
    path: Path
    display: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


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
    """Every name the lookup can resolve, by kind, each with where it was found.

    Adapters are separate from the rest because they are registered in code: they have no
    roots to be found under, and nothing can shadow one.
    """

    adapters: tuple[ComponentEntry, ...] = ()
    kinds: tuple[KindListing, ...] = ()


@dataclass(frozen=True)
class ComponentCreated:
    """A component that was scaffolded into the project, and the files it is made of.

    `files` are names relative to `path`. A flow is a single YAML file, so `path` is that
    file and this is empty rather than repeating it.
    """

    kind: str
    name: str
    path: Path
    display: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class HomeInitialised:
    """The home layer after `init`, split by what the command actually did.

    Two tuples rather than one list of names, because "already there" and "just written"
    are the whole of what someone re-running `init` is asking about. Names are relative to
    `path`, and a directory carries its trailing slash so a listing does not have to say
    which of the two each line is.
    """

    path: Path
    display: str
    created: tuple[str, ...] = ()
    existing: tuple[str, ...] = ()


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
