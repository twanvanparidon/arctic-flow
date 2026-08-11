#!/usr/bin/env python3
"""Flow execution: a push-based graph of tool and agent steps.

The engine proper. Its sibling `specs.py` holds the checks that run before any of this.

Push, not pull. A step declares where its result goes next (`push`, or `switch` to choose
one branch from its own output); nothing declares what it depends on. The engine derives
the reverse edges and delivers results forward.

The interesting part is a branch that is *not* taken. Its edge is marked skipped, and
skipping propagates: a step whose every inbound edge is skipped is itself skipped, which
skips its outbound edges in turn. That is what lets a downstream join run on both paths
instead of waiting forever for a branch the flow never entered.

A skipped step still resolves in templates, as the literal "(not run)", so a prompt can
acknowledge the gap rather than silently omitting it.

A `switch` is a step's own result choosing a branch, and a tool step has one as readily as
an agent step does. That is how a check is written: the tool answers a verdict, exits 0
because answering *is* its job, and the flow switches on what it said. A non-zero exit
stays what it always was, a tool that could not do its job, and fails the step.

A `switch` case naming a step that is already upstream is a *loop*, and the only cycle a
flow may have. Its steps go back to waiting and run again, bounded by `max_loops` on the
step that sends the work back. The edge is real, so the step that sent the work back is a
step of its own and what it said is in `steps` for the next pass to read.

Two loops may share a body only when one contains the other. `max_loops` then counts per
step over the whole run and is never reset, so two bounds of three are six passes and not
sixteen.

A loop also makes its steps ancestors of each other, including of themselves, so a step in
one may read its own previous result. That is what lets a pass edit the last answer rather
than replace it.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from jsonschema import Draft202012Validator

import adapters
from engine import specs
from paths.resolver import REFUSED_SEGMENTS, SEPARATOR, LookupError_, Paths, flat_name
from vault.vault import Vault, VaultError

TEMPLATE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")

# A conditional tag. Everything between the braces is captured and then matched against the
# four forms below, so `{% iff x %}` is refused rather than left in the prompt as prose.
# That is the opposite of TEMPLATE's rule, which lets `{{ a-b }}` through untouched, and
# the difference is deliberate: `{{ a-b }}` is plausible English and `{% ... %}` is not, so
# the narrow pattern that protects prose there would only hide a typo here.
TAG = re.compile(r"\{%(.*?)%\}")

IF_TAG = re.compile(r"^if\s+(not\s+)?([a-zA-Z0-9_.]+)$")

# A tag alone on its line takes the whole line with it, indentation and trailing newline
# included. Without this every conditional leaves a blank line in the prompt it was added
# to tidy up. A tag with anything else on its line is left where it is, so a value can
# still be guarded mid-sentence.
STANDALONE_TAG = re.compile(r"^[ \t]*(\{%.*?%\})[ \t]*\n?", re.MULTILINE)

# The virtual source of the flow's first push.
START = "__start__"

SKIPPED_RESULT = {"skipped": True, "text": "(not run)", "json": None}

# What an input's environment variable is called. See variable_name() for why it is not
# bare `ATF_`.
VARIABLE_PREFIX = "ATF_VAR_"

# Where a step's `prompt_file` is read from, relative to the flow file that names it, and
# what such a file is called. Markdown because a prompt is prose and the two other files
# the engine reads as prose are `agent.md` and a tool's doc; nothing parses the markup.
PROMPTS_DIR = "prompts"
PROMPT_SUFFIX = ".md"

# How often a running component looks at its cancel event. Invisible to a model, and a
# component given no event does not poll at all: it waits in one call, as it always has.
CANCEL_POLL_SECONDS = 0.05

# How long a component gets between TERM and KILL.
KILL_GRACE_SECONDS = 2.0


class FlowError(RuntimeError):
    """A flow is malformed, or one of its steps failed."""


class Cancelled(FlowError):
    """A component was stopped because its caller stopped waiting for it.

    A FlowError, so anything not told to care about the difference still catches it: to
    `execute()` a cancelled step would simply be a failed one. Only `commands.call_tool`
    separates it out, because a withdrawn request is answered with nothing at all rather
    than with a failure.
    """


# --------------------------------------------------------------------------- #
# templating
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Conditional:
    """One `{% if %}` and the two branches it chooses between.

    `otherwise` is empty when the template had no `{% else %}`, which renders as nothing.
    """

    path: str
    negated: bool
    then: tuple[Node, ...]
    otherwise: tuple[Node, ...]


# A template is a sequence of literal text and conditionals, and a conditional's branches
# are the same thing again, so a nested `{% if %}` needs no separate case.
Node = str | Conditional


@dataclass
class _Frame:
    """A `{% if %}` whose `{% endif %}` has not been reached yet."""

    path: str
    negated: bool
    then: list[Node]
    otherwise: list[Node]
    in_else: bool = False

    @property
    def branch(self) -> list[Node]:
        return self.otherwise if self.in_else else self.then


def parse_template(text: str) -> tuple[Node, ...]:
    """Split a template into literal text and conditionals, refusing a malformed tag.

    Called by `render` and by `template_refs`, which is what makes an unclosed `{% if %}`
    or an unknown tag a `lint` failure rather than something that waits for the step to
    run and costs a turn to discover.
    """
    body = STANDALONE_TAG.sub(r"\1", text)
    root: list[Node] = []
    stack: list[_Frame] = []

    def add(node: Node) -> None:
        (stack[-1].branch if stack else root).append(node)

    cursor = 0
    for match in TAG.finditer(body):
        literal = body[cursor : match.start()]
        if literal:
            _check_no_stray_tag(literal)
            add(literal)
        cursor = match.end()
        tag = match.group(1).strip()
        opening = IF_TAG.match(tag)
        if opening:
            stack.append(_Frame(opening.group(2), bool(opening.group(1)), [], []))
        elif tag == "else":
            if not stack:
                raise FlowError("template has an '{% else %}' with no '{% if %}' above it")
            if stack[-1].in_else:
                raise FlowError(
                    f"template has two '{{% else %}}' inside one '{{% if {stack[-1].path} %}}'"
                )
            stack[-1].in_else = True
        elif tag == "endif":
            if not stack:
                raise FlowError("template has an '{% endif %}' with no '{% if %}' above it")
            done = stack.pop()
            add(Conditional(done.path, done.negated, tuple(done.then), tuple(done.otherwise)))
        else:
            raise FlowError(
                f"template has an unknown tag '{{% {tag} %}}'. The tags are "
                "'{% if path %}', '{% if not path %}', '{% else %}' and '{% endif %}'"
            )

    trailing = body[cursor:]
    if trailing:
        _check_no_stray_tag(trailing)
        add(trailing)
    if stack:
        raise FlowError(f"template has an '{{% if {stack[-1].path} %}}' with no '{{% endif %}}'")
    return tuple(root)


def _check_no_stray_tag(literal: str) -> None:
    """Refuse half a tag, which `TAG` cannot match and would otherwise reach a model.

    A tag has to open and close on one line. So `{% if steps.a` is not an unknown tag, it
    is text, and without this it would be sent as part of the prompt with the body it was
    meant to guard rendered unconditionally underneath it.

    `%}` on its own is refused for the same reason and not for symmetry: `{ % if x %}` is
    the typo that produces one, and it fails exactly this way round.
    """
    for stray in ("{%", "%}"):
        if stray in literal:
            raise FlowError(
                f"template has a '{stray}' that is not part of a tag. A conditional tag "
                "opens and closes on one line, as '{% if path %}' ... '{% endif %}'"
            )


def truthy(value: Any) -> bool:
    """Whether a conditional's path counts as present.

    A step that did not run is false. That is the case conditionals exist for: a branch the
    flow skipped, and the first pass of a loop. Its result is a mapping, so it would be
    true on emptiness alone, which is why the marker is checked rather than the shape.

    Everything else is Python's own emptiness, which is also JSON's: null, false, 0, "",
    [] and {} are false. A string is never parsed, so the *text* "false" is true. Read
    `.json.<field>` where that distinction matters.
    """
    if isinstance(value, dict) and value.get("skipped") is True:
        return False
    return bool(value)


def render(text: str, context: dict[str, Any]) -> str:
    """Substitute {{ dotted.path }} against context, and take the {% if %} branches.

    Non-string values are inserted as indented JSON, so a template can reference a
    typed step result directly. An unresolvable path is an error rather than an
    empty string, because a silently blank prompt is far more expensive to debug.
    """
    return _render_nodes(parse_template(text), context)


def _render_nodes(nodes: tuple[Node, ...], context: dict[str, Any]) -> str:
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            parts.append(TEMPLATE.sub(lambda match: _substitute(match, context), node))
            continue
        # Only the branch that is taken is rendered, so a reference that is unresolvable
        # until the step has run is safe inside a guard: `{% if steps.scan %}` around
        # `{{ steps.scan.json.severity }}` is the point of the whole feature.
        form = f"{{% if {'not ' if node.negated else ''}{node.path} %}}"
        taken = truthy(_lookup(node.path, context, form)) != node.negated
        parts.append(_render_nodes(node.then if taken else node.otherwise, context))
    return "".join(parts)


def _substitute(match: re.Match[str], context: dict[str, Any]) -> str:
    value = _lookup(match.group(1), context, f"{{{{ {match.group(1)} }}}}")
    return value if isinstance(value, str) else json.dumps(value, indent=2)


def _lookup(path: str, context: dict[str, Any], form: str) -> Any:
    """Walk a dotted path, naming the reference as it was written when it does not resolve."""
    cursor: Any = context
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            raise FlowError(f"template references unknown value {form}")
        cursor = cursor[part]
    return cursor


def template_refs(value: Any) -> list[str]:
    """Every path a nested structure references, from both sides of every conditional.

    Both sides, because `validate` decides whether a reference is *allowed*, not which
    branch will run. A reference that would only be legal when its branch is skipped is
    not legal, and a guard is not a way to read a step that is not upstream.
    """
    if isinstance(value, str):
        return _node_refs(parse_template(value))
    if isinstance(value, dict):
        return [ref for item in value.values() for ref in template_refs(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in template_refs(item)]
    return []


def _node_refs(nodes: tuple[Node, ...]) -> list[str]:
    refs: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            refs += TEMPLATE.findall(node)
        else:
            refs.append(node.path)
            refs += _node_refs(node.then)
            refs += _node_refs(node.otherwise)
    return refs


# --------------------------------------------------------------------------- #
# components
# --------------------------------------------------------------------------- #


def load_component(paths: Paths, kind: str, name: str) -> tuple[Path, dict[str, Any]]:
    try:
        base = paths.find(kind, str(name))
    except LookupError_ as exc:
        raise FlowError(str(exc)) from exc
    spec_path = base / "spec.json"
    try:
        return base, json.loads(spec_path.read_text())
    except json.JSONDecodeError as exc:
        raise FlowError(f"{paths.display(spec_path)} is not valid JSON: {exc}") from exc


def load_agent(paths: Paths, name: str) -> tuple[dict[str, Any], str]:
    """An agent is config plus a prompt: spec.json and the file it points at.

    A separate markdown file keeps prompts editable and reviewable as prose, instead of
    escaped into a JSON string literal.
    """
    base, spec = load_component(paths, "agent", name)
    prompt_file = base / spec.get("system_prompt", "agent.md")
    if not prompt_file.is_file():
        raise FlowError(
            f"agent '{name}' points at {prompt_file.name} for its system prompt, which is missing"
        )
    system = prompt_file.read_text().strip()
    if not system:
        raise FlowError(f"agent '{name}' has an empty system prompt ({prompt_file.name})")
    return spec, system


def check_payload(schema: dict[str, Any], payload: dict[str, Any], who: str) -> None:
    """Validate a payload against a component's declared schema.

    Shared by tools and adapters. An adapter has no spec.json but still declares an
    INPUT_SCHEMA, so both get the same guarantee from the same validator.
    """
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path)
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors
        )
        raise FlowError(f"input rejected by {who}: {detail}")


def _stop(proc: subprocess.Popen[str], grouped: bool) -> None:
    """End a component, and everything it started. TERM first, KILL after the grace.

    The group rather than the child, because a tool is a program that runs other programs:
    `read_file` is bash running jq, awk and realpath. Killing bash alone leaves those
    behind, holding the pipe nothing is draining any more.

    TERM first so a tool with a trap can unwind, which matters because `write_file`
    truncates in place. KILL after, because a stop that leaves the process running is worse
    than none: nothing is waiting for its answer, and it still holds the workspace.

    Ungrouped, only the direct child is signalled, so a step whose tool backgrounds
    something and then times out can leave that behind. It is the price of a step's tool
    staying in the caller's session, which is what keeps Ctrl-C on `atf run` reaching it.
    Grouping every spawn would fix the orphan and break the interrupt; a cancellable call
    has no terminal to answer to, so it groups and gets the whole tree.
    """
    for number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, number) if grouped else proc.send_signal(number)
        except (ProcessLookupError, PermissionError):
            # It exited between the last look and the signal, which is the ordinary race.
            return
        try:
            proc.wait(timeout=KILL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def _collect(
    proc: subprocess.Popen[str],
    payload: str,
    name: str,
    timeout: float,
    cancel: threading.Event | None,
    grouped: bool,
) -> tuple[str, str]:
    """Send the payload, read both streams, and stop early if the caller gave up.

    `communicate` rather than `wait`: a tool that fills the pipe buffer blocks until
    something drains it, and only this writes stdin and reads both streams at once. It is
    *resumed* after a TimeoutExpired rather than restarted, which is what lets a short
    slice be used to look at `cancel`. The payload goes once; sending it again raises.

    Without a `cancel` there is no slicing at all: one call, the tool's own timeout,
    exactly as `subprocess.run` did it.
    """
    deadline = time.monotonic() + timeout
    sending: str | None = payload
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            _stop(proc, grouped)
            raise FlowError(f"{name} exceeded its {timeout}s timeout")
        try:
            return proc.communicate(
                sending, timeout=left if cancel is None else min(left, CANCEL_POLL_SECONDS)
            )
        except subprocess.TimeoutExpired:
            sending = None
            if cancel is not None and cancel.is_set():
                _stop(proc, grouped)
                raise Cancelled(f"{name} was cancelled") from None


def spawn(
    base: Path,
    spec: dict[str, Any],
    payload: dict[str, Any],
    paths: Paths,
    secrets: dict[str, str] | None = None,
    cancel: threading.Event | None = None,
    grouped: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Validate the payload against the component's own schema, then run it.

    The exit code is handed back rather than judged here. `invoke` is the one caller and is
    what turns anything but 0 into a failed step.

    `secrets` go into the child's environment and nowhere else, and only the names the
    step declared are present. A component cannot read a secret it was not granted.

    `cancel` is how a caller that stopped waiting stops the work: set it and the tool is
    signalled and `Cancelled` is raised. Both callers pass one. An in-turn call passes the
    client's cancellation; a step passes the run's ceiling.

    `grouped` is a separate question from `cancel`, and conflating the two is a bug worth
    naming. It asks whether this call has a terminal to answer to. An in-turn call has
    none, so it gets its own session and the whole process tree is signalled at once. A
    step stays in the caller's session, because a new session has no controlling terminal
    and Ctrl-C on `atf run` would stop reaching the tool. The price of that is the one in
    `_stop`: a step's tool that backgrounded something can leave it behind.
    """
    check_payload(spec["input_schema"], payload, f"{spec['name']}/spec.json")

    run = spec["run"]
    timeout = run.get("timeout_seconds", 60)
    command = [str((base / run["command"][0]).resolve()), *run["command"][1:]]

    # Before the fork, so a call cancelled while it waited for a free worker never starts a
    # process at all.
    if cancel is not None and cancel.is_set():
        raise Cancelled(f"{spec['name']} was cancelled")

    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=paths.workspace,
        env=child_environment(secrets),
        start_new_session=grouped,
    ) as proc:
        stdout, stderr = _collect(proc, json.dumps(payload), spec["name"], timeout, cancel, grouped)
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def exit_summary(spec: dict[str, Any], proc: subprocess.CompletedProcess[str]) -> str:
    """What a non-zero exit meant, in the component's own words where it has them."""
    meaning = spec.get("exit_codes", {}).get(str(proc.returncode), "unspecified exit code")
    stderr = proc.stderr.strip().splitlines()
    return f"{spec['name']} failed (exit {proc.returncode}: {meaning})" + (
        f". {stderr[-1]}" if stderr else ""
    )


def invoke(
    base: Path,
    spec: dict[str, Any],
    payload: dict[str, Any],
    paths: Paths,
    secrets: dict[str, str] | None = None,
    cancel: threading.Event | None = None,
    grouped: bool = False,
) -> str:
    """Run a component and return its stdout. Any exit but 0 fails the step."""
    proc = spawn(base, spec, payload, paths, secrets, cancel, grouped)
    if proc.returncode != 0:
        raise FlowError(exit_summary(spec, proc))
    return proc.stdout


def child_environment(secrets: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a component runs in: ours, plus its granted secrets.

    With one correction that only matters in a frozen build. PyInstaller prepends its
    bundle to the dynamic-library search path, and everything the engine spawns inherits
    it, so a system binary linking the same library loads *our* copy:

        openssl: .../_internal/libcrypto.so.3: version `OPENSSL_3.2.0' not found

    The engine exists to spawn subprocesses, so this breaks tools on any machine whose
    OpenSSL is newer than the build's. PyInstaller saves each variable it rewrites as
    <NAME>_ORIG; restore those, and drop the ones that had no original value.
    """
    env = dict(os.environ)

    if getattr(sys, "frozen", False):
        for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "LIBPATH"):
            original = env.pop(f"{name}_ORIG", None)
            if original is None:
                env.pop(name, None)
            else:
                env[name] = original

    env.update(secrets or {})
    return env


def maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# graph
# --------------------------------------------------------------------------- #


def outbound_targets(step: dict[str, Any]) -> list[str]:
    """Every step this one could hand to, whichever branch is taken."""
    if "push" in step:
        return list(step["push"] or [])
    if "switch" in step:
        targets = [t for branch in (step.get("cases") or {}).values() for t in (branch or [])]
        targets += list(step.get("default") or [])
        return list(dict.fromkeys(targets))
    return []


def build_graph(steps: list[dict[str, Any]]) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    outbound = {step["id"]: outbound_targets(step) for step in steps}
    inbound: dict[str, set[str]] = defaultdict(set)
    for sid, targets in outbound.items():
        for target in targets:
            inbound[target].add(sid)
    return outbound, inbound


def ancestors_of(sid: str, inbound: dict[str, set[str]]) -> set[str]:
    """Everything transitively upstream: what a step's templates may read.

    The `seen` set is what makes this safe across a loop, where two steps are upstream of
    each other. That is also the answer a loop wants: a writer may read the review that
    sends work back to it.
    """
    seen: set[str] = set()
    queue = list(inbound.get(sid, set()))
    while queue:
        current = queue.pop()
        if current in seen or current == START:
            continue
        seen.add(current)
        queue.extend(inbound.get(current, set()))
    return seen


def descendants_of(sid: str, outbound: dict[str, list[str]]) -> set[str]:
    """Everything transitively downstream. The mirror of ancestors_of."""
    seen: set[str] = set()
    queue = list(outbound.get(sid, []))
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(outbound.get(current, []))
    return seen


def back_edges(outbound: dict[str, list[str]], start: str) -> set[tuple[str, str]]:
    """The edges that close a cycle, found by a depth-first walk from the start.

    Which edge of a cycle counts as the one closing it depends on the order the walk
    reaches them, so it follows declaration order. That has to be stable: `lint` and `run`
    both ask this, and a flow accepted by one and refused by the other would be worse than
    either answer.
    """
    found: set[tuple[str, str]] = set()
    on_path: set[str] = set()
    settled: set[str] = set()

    def walk(sid: str) -> None:
        on_path.add(sid)
        for target in outbound.get(sid, []):
            # On the current path, so this edge points back the way we came.
            if target in on_path:
                found.add((sid, target))
            elif target not in settled:
                walk(target)
        on_path.discard(sid)
        settled.add(sid)

    walk(start)
    return found


def without_back_edges(
    outbound: dict[str, list[str]], back: set[tuple[str, str]]
) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """The same graph with its loops opened, which is a DAG again.

    Anything that assumes an ordering is derived from this and not from the real graph:
    the cycle check, a loop's own body, and the waves and guarantees `util/` reports. A
    back-edge has no place in an ordering, being the edge that goes the other way.
    """
    forward: dict[str, list[str]] = {
        sid: [target for target in targets if (sid, target) not in back]
        for sid, targets in outbound.items()
    }
    reverse: dict[str, set[str]] = defaultdict(set)
    for sid, targets in forward.items():
        for target in targets:
            reverse[target].add(sid)
    return forward, reverse


def loop_body(
    source: str, head: str, forward: dict[str, list[str]], reverse: dict[str, set[str]]
) -> set[str]:
    """Every step that runs again when `source` sends its result back to `head`.

    The steps between the two, both ends included. Bounded by what reaches `source` and
    not merely by what `head` reaches, and that is what makes the reset safe: every member
    is upstream of `source`, so all of them have finished by the time the back-edge fires
    and none is running when its state goes back to waiting.
    """
    downstream = {head} | descendants_of(head, forward)
    upstream = {source} | ancestors_of(source, reverse)
    return downstream & upstream


# --------------------------------------------------------------------------- #
# validation: everything checkable without running a step
# --------------------------------------------------------------------------- #


def load_flow(path: Path) -> dict[str, Any]:
    flow = yaml.safe_load(path.read_text())
    if not isinstance(flow, dict):
        raise FlowError(f"{path}: flow file must contain a YAML mapping")
    inline_prompts(flow, path.parent)
    return flow


def inline_prompts(flow: dict[str, Any], directory: Path) -> None:
    """Read every step's `prompt_file` into its `prompt`.

    Here rather than in `run_agent`, so there is one kind of prompt by the time anything
    looks at a step: `validate`, `template_refs`, `inspect` and the engine are unchanged,
    and a prompt file that is missing or unreadable fails `lint` instead of waiting for the
    step to run. The price is that a template error names the step rather than the file,
    and the step is what a reader opens to find the file anyway.
    """
    steps = flow.get("steps")
    if not isinstance(steps, list):
        # The shape is validate()'s to report. Guessing at it here would produce a second
        # sentence about the same mistake, ahead of the one that explains it.
        return
    for step in steps:
        if not isinstance(step, dict) or "prompt_file" not in step:
            continue
        sid = step.get("id") or "?"
        reference = step["prompt_file"]
        if not isinstance(reference, str) or not reference.strip():
            raise FlowError(f"step '{sid}' prompt_file must be a name, as in 'prompt_file: review'")
        if "prompt" in step:
            raise FlowError(
                f"step '{sid}' sets both 'prompt' and 'prompt_file'. One of them would be "
                "the prompt and the other would be dead text, so say which"
            )
        step["prompt"] = _read_prompt(sid, reference, directory)


def _read_prompt(sid: str, reference: str, directory: Path) -> str:
    """A prompt file's text, from `prompts/` beside the flow that named it.

    Beside the flow file and not under a search root, because a prompt belongs to the flow
    that names it rather than being a component other flows look up. That is also what
    makes `flows/review/review.yaml` worth having: the bundle gives the prompts a directory
    of their own, where a flat flow shares `flows/prompts/` with its siblings.

    The name is checked the way a component name is, and for the same reason: `directory`
    joined with `../../etc/passwd` resolves, and a flow can arrive by clone.
    """
    if Path(reference).is_absolute() or any(
        part in REFUSED_SEGMENTS for part in reference.split(SEPARATOR)
    ):
        raise FlowError(
            f"step '{sid}' prompt_file '{reference}' is not a name. A prompt is read from "
            f"{PROMPTS_DIR}/ beside the flow, so an absolute path, an empty part, '.' and "
            "'..' are refused"
        )
    target = directory / PROMPTS_DIR / f"{reference}{PROMPT_SUFFIX}"
    try:
        return target.read_text()
    except OSError as exc:
        # Names the path as the flow spells it. The absolute one is inside whatever
        # directory the flow was resolved out of, which is not what anyone would edit.
        raise FlowError(
            f"step '{sid}' prompt_file '{reference}' cannot be read: expected "
            f"{PROMPTS_DIR}/{reference}{PROMPT_SUFFIX} beside the flow"
        ) from exc


def check_loop_shape(sid: str, step: dict[str, Any]) -> None:
    """A step's `max_loops`, before the graph has said whether it loops at all."""
    allowed = step["max_loops"]
    # YAML 1.1 reads `yes` as True and a bool is an int, so `max_loops: yes` would pass as
    # 1. One is a legal bound, so the bool has to be refused in its own right rather than
    # falling out of a minimum above it.
    if isinstance(allowed, bool) or not isinstance(allowed, int) or allowed < 1:
        raise FlowError(
            f"step '{sid}' max_loops must be an integer of 1 or more. It counts how many "
            "times the step may send its result back upstream"
        )


def validate(flow: dict[str, Any], paths: Paths) -> list[dict[str, Any]]:
    for field in ("flow", "start", "steps"):
        if field not in flow:
            raise FlowError(f"flow is missing required field '{field}'")

    steps = flow["steps"]
    if not isinstance(steps, list) or not steps:
        raise FlowError("'steps' must be a non-empty list")

    by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        sid = step.get("id")
        if not sid:
            raise FlowError("every step needs an 'id'")
        if sid in by_id:
            raise FlowError(f"duplicate step id '{sid}'")
        if ("tool" in step) == ("agent" in step):
            raise FlowError(f"step '{sid}' must set exactly one of 'tool' or 'agent'")
        if "agent" in step and not step.get("prompt"):
            raise FlowError(f"agent step '{sid}' needs a 'prompt'")
        if step.get("secrets") is not None:
            declared = step["secrets"]
            if not isinstance(declared, list) or not all(isinstance(n, str) for n in declared):
                raise FlowError(f"step '{sid}' secrets must be a list of names")
            if len(set(declared)) != len(declared):
                raise FlowError(f"step '{sid}' lists a secret more than once")
        if "push" in step and "switch" in step:
            raise FlowError(
                f"step '{sid}' sets both 'push' and 'switch'. A step either hands its result "
                "onward unconditionally or chooses one branch, not both"
            )
        if "max_loops" in step:
            check_loop_shape(sid, step)
        by_id[sid] = step

    if flow["start"] not in by_id:
        raise FlowError(f"start references unknown step '{flow['start']}'")

    for sid, step in by_id.items():
        if "switch" not in step:
            if "cases" in step or "default" in step:
                raise FlowError(
                    f"step '{sid}' has 'cases' or 'default' but no 'switch' to select them"
                )
            continue

        if not isinstance(step["switch"], str) or not step["switch"].strip():
            raise FlowError(
                f"step '{sid}' needs a 'switch' expression, e.g. \"{{{{ this.json.verdict }}}}\""
            )
        cases = step.get("cases")
        if not isinstance(cases, dict) or not cases:
            raise FlowError(f"step '{sid}' has a switch but no 'cases'")
        for key, branch in cases.items():
            if not isinstance(key, str):
                # YAML 1.1 reads bare on/off/yes/no/true/false as booleans, which
                # would never match the rendered string. Say so instead of silently
                # falling through to default.
                raise FlowError(
                    f"step '{sid}' case key {key!r} is not a string. YAML reads bare "
                    "on/off/yes/no/true/false as booleans, so quote it"
                )
            if branch is not None and not isinstance(branch, list):
                raise FlowError(f"step '{sid}' case '{key}' must be a list of step ids")
        if step.get("default") is not None and not isinstance(step["default"], list):
            raise FlowError(f"step '{sid}' default must be a list of step ids")

    outbound, inbound = build_graph(steps)
    for sid, targets in outbound.items():
        for target in targets:
            if target not in by_id:
                raise FlowError(f"step '{sid}' pushes to unknown step '{target}'")
            if target == sid:
                raise FlowError(f"step '{sid}' pushes to itself")

    for sid in by_id:
        if sid != flow["start"] and not inbound.get(sid):
            raise FlowError(
                f"step '{sid}' is unreachable: nothing pushes to it, and it is not the start"
            )

    # A case naming a step that is already upstream is a loop, and the only cycle a flow
    # may have. Everything below decides whether it is one the flow meant.
    back = back_edges(outbound, flow["start"])
    forward, forward_in = without_back_edges(outbound, back)
    looping = {source for source, _ in back}

    for source, head in sorted(back):
        if "switch" not in by_id[source]:
            raise FlowError(
                f"step '{source}' pushes back to '{head}', which is upstream of it. A loop "
                "needs a 'switch' that can leave it: a step that always sends its result "
                "back has no way to stop"
            )
        if "max_loops" not in by_id[source]:
            raise FlowError(
                f"step '{source}' sends its result back to '{head}', which is upstream of "
                f"it. That is a loop, so it needs bounding: add 'max_loops' to '{source}', "
                "or switch to a step that is not upstream"
            )

    for sid, step in by_id.items():
        if "max_loops" in step and sid not in looping:
            raise FlowError(
                f"step '{sid}' has 'max_loops' but no case naming a step upstream of it, "
                "so it never loops"
            )

    bodies = {pair: loop_body(pair[0], pair[1], forward, forward_in) for pair in sorted(back)}

    for (source, head), body in bodies.items():
        # A step the head reaches that does not lead back to the source would run on the
        # first pass and then sit done while the rest went round again. Refusing it is also
        # what keeps the reset safe: every body member is upstream of the source, so none
        # is still running when the back-edge fires.
        stranded = descendants_of(head, forward) - body - descendants_of(source, forward)
        if stranded:
            raise FlowError(
                f"step '{sorted(stranded)[0]}' is reached from '{head}' inside its loop but "
                f"does not lead back to '{source}', so it would run on the first pass and "
                f"never again. Have it push to '{source}', or move it after the loop"
            )

    # Two loops may share steps only when one body contains the other, which is the shape
    # a cheap check inside an expensive review has: {write, check} inside
    # {write, check, review}. Nesting is well defined because the inner loop's whole body
    # goes round again when the outer one fires, and each count is that step's own.
    #
    # Partial overlap is not. Two bodies that cross leave a step that one loop re-runs and
    # the other does not, so the crossing loop reads a result from a pass that no longer
    # exists, and nothing says which count a pass belongs to.
    pairs = list(bodies)
    for index, first in enumerate(pairs):
        for second in pairs[index + 1 :]:
            one, other = bodies[first], bodies[second]
            if not one & other or one <= other or other <= one:
                continue
            raise FlowError(
                f"the loop back from '{first[0]}' and the loop back from '{second[0]}' "
                f"both re-run '{sorted(one & other)[0]}', and neither loop contains the "
                f"other: '{sorted(one - other)[0]}' runs again in only one of them. "
                "Nest them, or have the inner loop end where the outer one starts"
            )

    # Kahn's algorithm over the push direction, with the declared loops opened. What
    # cannot be settled is a cycle the walk from `start` never entered, so no back-edge was
    # found to open it: a ring of steps with nothing leading in.
    remaining = {sid: set(forward_in.get(sid, set())) - {START} for sid in by_id}
    settled: set[str] = set()
    while True:
        ready = {sid for sid, sources in remaining.items() if sources <= settled}
        if not ready:
            break
        settled |= ready
        for sid in ready:
            del remaining[sid]
    if remaining:
        raise FlowError(f"steps form a cycle nothing enters: {', '.join(sorted(remaining))}")

    # A template may read inputs, or a step that is genuinely upstream of it. `this` is the
    # running step's own result, which exists only in its switch.
    declared_inputs = set((flow.get("inputs") or {}).keys())

    def check_refs(
        sid: str,
        refs: list[str],
        *,
        allow_this: bool = False,
        to_model: bool = False,
    ) -> None:
        upstream = ancestors_of(sid, inbound)
        for ref in refs:
            root, _, rest = ref.partition(".")
            if root == "this":
                if not allow_this:
                    raise FlowError(
                        f"step '{sid}' uses {{{{ this.* }}}} outside its switch. 'this' is the "
                        "step's own result, so it exists only where that result already does"
                    )
            elif root == "inputs":
                if rest.split(".")[0] not in declared_inputs:
                    raise FlowError(f"step '{sid}' references undeclared input '{rest}'")
            elif root == "steps":
                target = rest.split(".")[0]
                if target not in by_id:
                    raise FlowError(f"step '{sid}' references unknown step '{target}'")
                if target not in upstream:
                    raise FlowError(
                        f"step '{sid}' reads from '{target}', which is not upstream of it. "
                        f"'{target}' may not have run when '{sid}' does"
                    )
            elif root == "secrets":
                name = rest.split(".")[0]
                if to_model:
                    # Templating a secret into a prompt sends it to the model, and it is
                    # then in the conversation for the rest of the session. A step's
                    # secrets still reach its adapter through the environment.
                    raise FlowError(
                        f"step '{sid}' puts {{{{ secrets.{name} }}}} in an agent prompt. That "
                        "sends the secret to the model. Declare it in 'secrets' and let the "
                        "adapter read it from the environment instead"
                    )
                if name not in (by_id[sid].get("secrets") or []):
                    raise FlowError(
                        f"step '{sid}' uses {{{{ secrets.{name} }}}} without declaring it. "
                        "Add it to that step's 'secrets' list, so what a step can read is "
                        "visible where the step is defined"
                    )
            else:
                raise FlowError(f"step '{sid}' references unknown namespace '{root}'")

    for sid, step in by_id.items():
        # An agent step's body is model-facing: its prompt is in there. The switch is
        # checked apart from it, because that is the one place `this` resolves.
        to_model = "agent" in step
        body = {k: v for k, v in step.items() if k not in ("id", "switch")}
        check_refs(sid, template_refs(body), to_model=to_model)
        if step.get("switch"):
            check_refs(sid, template_refs(step["switch"]), allow_this=True, to_model=to_model)

    # Shape before contents. `output: "{{ steps.x.text }}"` is the natural typo for
    # `output: {template: ...}`, and without this it reached .get() on a str and came out as
    # an AttributeError traceback rather than a flow error.
    declared_output = flow.get("output") or {}
    if not isinstance(declared_output, dict):
        raise FlowError(
            "'output' must be a mapping with a 'template' key, not "
            f"{type(declared_output).__name__}. Write `output:` then `  template: ...`"
        )

    output_template = declared_output.get("template")
    if output_template:
        for ref in template_refs(output_template):
            root, _, rest = ref.partition(".")
            if root == "inputs":
                if rest.split(".")[0] not in declared_inputs:
                    raise FlowError(f"output references undeclared input '{rest}'")
            elif root == "steps":
                if rest.split(".")[0] not in by_id:
                    raise FlowError(f"output references unknown step '{rest.split('.')[0]}'")
            else:
                raise FlowError(f"output references unknown namespace '{root}'")

    # Every component the flow names is loaded and checked as far as it can be without
    # running: that its spec is one the engine can execute, and that the flow's own use of
    # it agrees with what it declares. Everything found here would otherwise have surfaced
    # mid-run, after earlier steps had already spent time and money.
    for step in by_id.values():
        try:
            if "tool" in step:
                base, spec = load_component(paths, "tool", step["tool"])
                where = paths.display(base / "spec.json")
                specs.check_tool_spec(spec, base, where)
                specs.check_step_input(step, spec, where)
                continue

            base, spec = load_component(paths, "agent", step["agent"])
            # load_agent rather than the spec alone, so a missing or empty agent.md is
            # reported here too.
            load_agent(paths, step["agent"])
            specs.check_agent_spec(spec, paths.display(base / "spec.json"))
        except specs.SpecError as exc:
            raise FlowError(str(exc)) from exc

        # An agent's tool grant is checked here because the agent declares it, not the
        # flow. Resolving the names first means a typo is reported as a typo, before the
        # rules below talk about permissions the reader has not got to yet.
        writes = []
        credentialled = []
        flattened: dict[str, str] = {}
        for tool in spec.get("tools") or []:
            tool_base, tool_spec = load_component(paths, "tool", tool)
            try:
                specs.check_tool_spec(tool_spec, tool_base, paths.display(tool_base / "spec.json"))
            except specs.SpecError as exc:
                raise FlowError(str(exc)) from exc
            if (tool_spec.get("permissions") or {}).get("filesystem") == "write":
                writes.append(tool)
            if tool_spec.get("secrets"):
                credentialled.append(tool)
            # An in-turn call arrives by a name with no separator in it, so two grants that
            # flatten onto one are a single name for two tools and the server cannot tell
            # which was asked for. Refused here rather than mid-turn, where the model would
            # be told a tool it can see does not exist.
            clash = flattened.setdefault(flat_name(tool), tool)
            if clash != tool:
                raise FlowError(
                    f"agent '{step['agent']}' grants both '{clash}' and '{tool}', which a model "
                    f"sees as one tool called '{flat_name(tool)}'. Rename one of them"
                )

        # A tool an agent calls itself runs without anyone approving the call, so granting
        # one that writes has to be said out loud rather than inferred from the tool list.
        if writes and not spec.get("unattended"):
            raise FlowError(
                f"agent '{step['agent']}' grants {', '.join(sorted(writes))}, which "
                f"{'change' if len(writes) > 1 else 'changes'} the workspace. Set "
                '"unattended": true in its spec to say that is intended'
            )

        # Nothing scopes a secret to one in-turn call yet, so a tool that needs one cannot
        # be reached this way. Refused where the grant is, rather than mid-turn when the
        # tool finds its environment empty and the model starts reasoning about it.
        if credentialled:
            raise FlowError(
                f"agent '{step['agent']}' grants {', '.join(sorted(credentialled))}, which "
                "expects a secret in its environment. An agent's tools are not granted one. "
                "Run it as a tool step, which declares its own 'secrets'"
            )

        # Same rule from the other side: the adapter is given the step's secrets, so a
        # turn that can also call tools would hand every one of them the whole grant.
        if spec.get("tools") and step.get("secrets"):
            raise FlowError(
                f"step '{step['id']}' declares secrets and runs agent '{step['agent']}', which is "
                "granted tools. A tool the agent calls would inherit them, so the two "
                "cannot be combined. Move the secret to a tool step"
            )

    return steps


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


def tool_server_command(paths: Paths, tools: list[str], events: Path) -> list[str]:
    """How to re-invoke this engine as a tool server, for an adapter to spawn.

    An agent's tools reach its runtime over a protocol, and the server is this program
    again. The argv has to work from all three ways the engine runs: a frozen build *is*
    the executable, while a checkout and an installed wheel are the interpreter plus an
    entry point, which `sys.argv[0]` names in both.

    That entry point is resolved here when it is relative. `python3 src/main.py` leaves
    `sys.argv[0]` as written, and the runtime starts the server from a directory of its
    own choosing, where it would not resolve.

    The assumption `sys.argv[0]` carries is that this program *is* the engine, which holds
    for all three of those. It does not hold when the engine is a library inside another
    program: the argv then names that program, which has no `mcp-serve` to run, and the
    server exits with an argument error the model reports as the tool not working. A
    second front end that keeps `atf` on the path has nothing to do; one that does not
    would need to say where the engine is, and there is no way to ask yet.

    `--workspace` precedes the subcommand because it is a global flag. Passing it
    explicitly rather than letting the child inherit a cwd keeps the tool lookup and the
    directory tools run in identical to this process's, wherever the runtime starts it.
    """
    entry = Path(sys.argv[0])
    launcher = (
        [sys.executable]
        if getattr(sys, "frozen", False)
        else [sys.executable, str(entry if entry.is_absolute() else entry.resolve())]
    )
    command = [*launcher, "--workspace", str(paths.workspace), "mcp-serve"]
    for tool in tools:
        command += ["--tool", tool]
    return [*command, "--events", str(events)]


class ToolCallReporter:
    """Forwards what the tool server appends to `on_event`, while a turn is running.

    Without this an in-turn call is invisible: the engine sees one agent step, and a turn
    that read nine files reports as one row. The server cannot notify directly because it
    is two processes away, spawned by the runtime rather than by the engine.

    A file rather than a pipe. The server is not this process's child, so there is no fd
    to hand it, and a FIFO would block on open when a turn never calls a tool.
    """

    def __init__(self, path: Path, notify: Callable[[dict[str, Any]], None], step: str) -> None:
        self._path = path
        self._notify = notify
        self._step = step
        self._read = 0

    def drain(self) -> None:
        """Forward whatever has appeared since the last call. Safe to call repeatedly."""
        try:
            lines = self._path.read_text().splitlines()
        except OSError:
            return
        for line in lines[self._read :]:
            self._read += 1
            try:
                call = json.loads(line)
            except json.JSONDecodeError:
                # A half-written line: the server appends and flushes per call, so the
                # rest arrives on the next drain. Rewind so it is read again whole.
                self._read -= 1
                return
            self._notify({"kind": "tool_call", "step": self._step, **call})


# How often the reporter looks for new calls. Short enough that a call appears while the
# turn is still running, which is the whole point of reporting it at all.
TOOL_EVENT_POLL_SECONDS = 0.05


@contextmanager
def tool_calls_reported(
    paths: Paths, tools: list[str], notify: Callable[[dict[str, Any]], None], step: str
) -> Iterator[list[str] | None]:
    """Yields the server command, forwarding each call it reports until the turn is done.

    Granting nothing yields None and starts neither a file nor a thread, so an ordinary
    turn pays nothing for this. The final drain matters on the other path: the last call
    usually lands between two polls.
    """
    if not tools:
        yield None
        return

    with tempfile.TemporaryDirectory(prefix="atf-tool-events-") as directory:
        events = Path(directory) / "calls.ndjson"
        events.touch()
        reporter = ToolCallReporter(events, notify, step)
        stop = threading.Event()

        def poll() -> None:
            while not stop.wait(TOOL_EVENT_POLL_SECONDS):
                reporter.drain()

        watcher = threading.Thread(target=poll, daemon=True, name=f"atf-tool-events-{step}")
        watcher.start()
        try:
            yield tool_server_command(paths, tools, events)
        finally:
            stop.set()
            watcher.join(timeout=1)
            reporter.drain()


def run_step(
    step: dict[str, Any],
    context: dict[str, Any],
    paths: Paths,
    vault: Vault | None = None,
    notify: Callable[[dict[str, Any]], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    granted: dict[str, str] = {}
    if step.get("secrets"):
        if vault is None:
            # execute() prefixes the step id onto step failures, so don't repeat it.
            raise FlowError(
                "wants secrets but no vault is open: pass --vault, or set it on the flow"
            )
        granted = vault.select(list(step["secrets"]))

    if "tool" in step:
        base, spec = load_component(paths, "tool", step["tool"])
        # A tool may also take a secret as an input value, so its own schema documents
        # what it expects. Agent steps deliberately cannot: see validate().
        step_context = {**context, "secrets": granted}
        payload = {
            key: render(value, step_context) if isinstance(value, str) else value
            for key, value in (step.get("input") or {}).items()
        }
        stdout = invoke(base, spec, payload, paths, secrets=granted, cancel=cancel)
        return {"text": stdout, "json": maybe_json(stdout)}

    return run_agent(step, context, paths, granted, notify or (lambda _event: None), cancel)


def run_agent(
    step: dict[str, Any],
    context: dict[str, Any],
    paths: Paths,
    secrets: dict[str, str],
    notify: Callable[[dict[str, Any]], None],
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """One agent turn, in a session of its own.

    A step that has to be reworked is a loop in the graph rather than a retry in here: the
    step that judged the answer is a step, and the next pass reads what it said out of
    `steps`. Each turn is a fresh session with no memory of the last, so the prompt is the
    only place that history can live, and a loop puts it somewhere a reader can see.

    `cancel` is checked before the turn rather than only between steps. A turn costs money,
    and an adapter cannot be interrupted once it has started, so the last chance to not
    spend it is here.
    """
    agent, system = load_agent(paths, step["agent"])
    try:
        adapter = adapters.get(agent["adapter"])
    except adapters.AdapterError as exc:
        raise FlowError(str(exc)) from exc

    # Yields None when the agent was granted nothing, which is the no-tools turn.
    with tool_calls_reported(paths, agent.get("tools") or [], notify, step["id"]) as server:
        if cancel is not None and cancel.is_set():
            raise Cancelled("the run stopped before this turn started")
        return agent_turn(adapter, agent, system, render(step["prompt"], context), secrets, server)


def agent_turn(
    adapter: ModuleType,
    agent: dict[str, Any],
    system: str,
    prompt: str,
    secrets: dict[str, str],
    tool_server: list[str] | None = None,
) -> dict[str, Any]:
    """One turn through the agent's adapter, as the normalised envelope plus `json`."""
    # The same builder `check_agent_spec` probes with, so what lint validated is what runs.
    payload: dict[str, Any] = {
        "prompt": prompt,
        "system": system,
        **specs.adapter_parameters(agent),
    }
    if tool_server is not None:
        # The names and a command that serves them, never a config in the adapter's own
        # shape. An adapter able to dispatch tools itself ignores the command and uses the
        # names, which is what keeps the agent spec runtime-neutral.
        payload["tool_server"] = tool_server

    check_payload(adapter.INPUT_SCHEMA, payload, f"adapter {adapter.NAME}")
    try:
        envelope = adapter.run(payload, child_environment(secrets))
    except adapters.AdapterError as exc:
        raise FlowError(f"{adapter.NAME}: {exc}") from exc

    result = dict(envelope)
    result["json"] = maybe_json(envelope.get("text", ""))
    return result


def chosen_targets(
    step: dict[str, Any], result: dict[str, Any], inputs: dict[str, Any], results: dict[str, Any]
) -> list[str]:
    """Which of a step's possible targets actually receive its result."""
    if "push" in step:
        return list(step["push"] or [])
    if "switch" not in step:
        return []

    context = {"inputs": inputs, "steps": results, "this": result}
    value = render(step["switch"], context).strip()
    cases = step.get("cases") or {}
    if value in cases:
        return list(cases[value] or [])
    if "default" in step:
        return list(step["default"] or [])
    # execute() prefixes the step id onto step failures, so don't repeat it here.
    raise FlowError(
        f"switched on '{value}', which matches no case ({', '.join(cases)}) and there is no default"
    )


def _check_ceiling(
    limit: float | None, deadline: float | None, stop: threading.Event | None
) -> None:
    """Fail the run once `run.max_minutes` has passed, and stop what it can on the way out.

    What it stops is worth knowing before relying on it. Setting `stop` reaches a tool
    subprocess within `CANCEL_POLL_SECONDS`. It cannot reach an agent turn:
    `adapter.run` is a synchronous call with no way in, so a
    turn already started runs until its own `timeout_seconds`, and the pool's shutdown
    waits for it. The ceiling is therefore a ceiling plus at most one agent turn.

    That gap is deliberate. Closing it means putting cancellation into the adapter
    contract, which every adapter would then have to implement, and it buys minutes on a
    limit measured in hours.
    """
    if deadline is None or time.monotonic() < deadline:
        return
    if stop is not None:
        stop.set()
    raise FlowError(f"the run exceeded its {limit} minute ceiling (run.max_minutes)")


def execute(
    flow: dict[str, Any],
    steps: list[dict[str, Any]],
    inputs: dict[str, Any],
    paths: Paths,
    vault: Vault | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the flow. `on_event` is told when steps start, finish, skip and fail.

    The observer emits facts and `cli/progress.py` renders them, so this module never
    decides what progress looks like. Events arrive from worker threads, so an observer
    has to be safe to call concurrently.

    `run.max_minutes` in the user's config is a ceiling on the whole of this, enforced by
    the wait below. See `_ceiling` for what it can and cannot stop.
    """
    notify = on_event or (lambda _event: None)
    by_id = {step["id"]: step for step in steps}
    outbound, inbound = build_graph(steps)

    back = back_edges(outbound, flow["start"])
    forward, forward_in = without_back_edges(outbound, back)
    bodies = {pair: loop_body(pair[0], pair[1], forward, forward_in) for pair in back}

    edge: dict[tuple[str, str], str] = {
        (source, target): "pending" for target, sources in inbound.items() for source in sources
    }
    # Skipped rather than pending, or a loop head waits on a step downstream of itself and
    # nothing in the flow ever becomes ready.
    for pair in back:
        edge[pair] = "skipped"
    edge[(START, flow["start"])] = "delivered"
    inbound[flow["start"]].add(START)

    state = {sid: "waiting" for sid in by_id}
    results: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    loops = {source: 0 for source, _ in back}
    runs: dict[str, int] = defaultdict(int)

    # A loop's steps are mutually upstream, so one may read another that has not run yet.
    # The value a skipped step already resolves to is the honest answer on a first pass:
    # this did not happen.
    for body in bodies.values():
        for sid in body:
            results.setdefault(sid, dict(SKIPPED_RESULT))

    def reenter(source: str, head: str) -> None:
        """Put a loop's steps back to waiting, so the next pass runs them again.

        `results` is deliberately left alone. The previous pass's values are what the next
        one reads, and that is how a writer sees the review that sent the work back.

        `loops` is left alone too, so a bound is what that step may do over the whole run
        rather than per pass of a loop around it. Two nested bounds of three are then six
        passes and not sixteen, which is the reading that keeps a flow's worst case
        something a reader can add up.
        """
        body = bodies[(source, head)]
        for sid in body:
            state[sid] = "waiting"
        for a, b in list(edge):
            # Only the edges inside the loop. One arriving from outside was delivered
            # before the loop began, and the back-edge itself has just been delivered, so
            # neither goes back to pending or the head would never become ready.
            if a not in body or b not in body or (a, b) == (source, head):
                continue
            # A nested loop's own back-edge goes back to skipped, for the reason every
            # back-edge starts that way: pending, it would have the inner head waiting on a
            # step downstream of itself, nothing in the body would ever become ready, and
            # the run would end quietly with the outer pass never actually run.
            edge[(a, b)] = "skipped" if (a, b) in back else "pending"

    def propagate_skips() -> None:
        """A step whose every inbound edge was skipped never runs, nor does its
        subtree. Without this, a join downstream of an untaken branch waits forever."""
        changed = True
        while changed:
            changed = False
            for sid in by_id:
                if state[sid] != "waiting":
                    continue
                sources = inbound.get(sid, set())
                if sources and all(edge[(s, sid)] == "skipped" for s in sources):
                    state[sid] = "skipped"
                    results[sid] = dict(SKIPPED_RESULT)
                    for target in outbound[sid]:
                        edge[(sid, target)] = "skipped"
                    trace.append({"step": sid, "skipped": True})
                    notify({"kind": "skipped", "step": sid})
                    changed = True

    # `limit` stays in minutes, the unit the config was written in, so the failure names
    # the number someone typed. The one conversion is here.
    limit = paths.config.max_run_minutes
    deadline = time.monotonic() + limit * 60 if limit else None
    # No ceiling means no cancel at all, so a step takes exactly the path it took before
    # there was one: a single blocking `communicate`, with nothing polling beside it.
    stop = threading.Event() if limit else None

    with ThreadPoolExecutor(max_workers=max(1, len(steps))) as pool:
        running: dict[Any, tuple[str, float]] = {}
        while True:
            _check_ceiling(limit, deadline, stop)
            propagate_skips()
            for sid in by_id:
                if state[sid] != "waiting":
                    continue
                sources = inbound.get(sid, set())
                if any(edge[(s, sid)] == "pending" for s in sources):
                    continue
                if not any(edge[(s, sid)] == "delivered" for s in sources):
                    continue
                state[sid] = "running"
                runs[sid] += 1
                step = by_id[sid]
                notify(
                    {
                        "kind": "started",
                        "step": sid,
                        "component": f"tool {step['tool']}"
                        if "tool" in step
                        else f"agent {step['agent']}",
                    }
                )
                # Snapshot at submit time: everything upstream has already resolved,
                # so a step never observes a partial result.
                context = {"inputs": inputs, "steps": dict(results)}
                running[pool.submit(run_step, by_id[sid], context, paths, vault, notify, stop)] = (
                    sid,
                    time.monotonic(),
                )

            if not running:
                break

            # Waiting with no deadline is the whole of what the ceiling changes here: a
            # run that has one cannot block in `wait` past it, or nothing would notice
            # until a step happened to finish.
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            done, _ = wait(running, return_when=FIRST_COMPLETED, timeout=left)
            if not done:
                # The only way `wait` returns nothing: the timeout was reached, and a
                # timeout only exists when there is a ceiling to reach.
                _check_ceiling(limit, deadline, stop)
            for future in done:
                sid, started = running.pop(future)
                elapsed = round((time.monotonic() - started) * 1000)
                try:
                    results[sid] = future.result()
                    state[sid] = "done"
                    # Resolving the push is inside the same guard on purpose. An
                    # unmatched switch fails *after* the step itself succeeded, and
                    # leaving it outside meant a step could start, never report a
                    # result either way, and vanish from the progress output.
                    targets = chosen_targets(by_id[sid], results[sid], inputs, results)
                    looped_to = [target for target in targets if (sid, target) in back]
                    if looped_to:
                        loops[sid] += 1
                        allowed = by_id[sid]["max_loops"]
                        if loops[sid] > allowed:
                            # execute() prefixes the step id onto step failures.
                            raise FlowError(
                                f"the loop back to '{looped_to[0]}' did not converge in "
                                f"{allowed} pass{'' if allowed == 1 else 'es'}"
                            )
                except (FlowError, VaultError) as exc:
                    # Scrub before the message travels: a failing component may echo a
                    # secret it was given, and this text reaches logs and terminals.
                    message = vault.scrub(str(exc)) if vault else str(exc)
                    trace.append({"step": sid, "ms": elapsed, "ok": False, "error": message})
                    notify({"kind": "failed", "step": sid, "ms": elapsed, "error": message})
                    raise FlowError(f"step '{sid}': {message}") from exc

                for target in outbound[sid]:
                    if target in targets:
                        edge[(sid, target)] = "delivered"
                    elif looped_to:
                        # Pending, not skipped. Marking the exit branch skipped while the
                        # loop is still going round propagates, so everything after the
                        # loop is skipped too and the run ends with no output.
                        edge[(sid, target)] = "pending"
                    else:
                        edge[(sid, target)] = "skipped"
                entry = {
                    "step": sid,
                    "ms": elapsed,
                    "ok": True,
                    "pushed_to": targets,
                    "cost_usd": results[sid].get("cost_usd"),
                }
                # Only where the step has run before: `null` on every step of every flow
                # without a loop in it is noise.
                if runs[sid] > 1:
                    entry["iteration"] = runs[sid]
                trace.append(entry)
                notify({"kind": "finished", "is_switch": "switch" in by_id[sid], **entry})

                if looped_to:
                    # One target, because validate() refuses overlapping loop bodies and
                    # two back-edges from one step would share it.
                    notify(
                        {
                            "kind": "looped",
                            "step": sid,
                            "to": looped_to[0],
                            "count": loops[sid],
                            "of": by_id[sid]["max_loops"],
                        }
                    )
                    reenter(sid, looped_to[0])

    return results, trace


def run_flow(
    flow: dict[str, Any],
    inputs: dict[str, Any],
    paths: Paths,
    vault: Vault | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Validate, execute, and render the flow's output."""
    steps = validate(flow, paths)
    results, trace = execute(flow, steps, inputs, paths, vault, on_event)
    template = (flow.get("output") or {}).get("template")
    rendered = (
        render(template, {"inputs": inputs, "steps": results})
        if template
        else json.dumps(results, indent=2)
    )
    return rendered.strip(), trace


def variable_name(name: str) -> str:
    """The environment variable an input reads from: `depth` comes from `$ATF_VAR_DEPTH`.

    Prefixed rather than bare, the way Terraform separates `TF_VAR_` from its own `TF_LOG`.
    `$ATF_PATH` is a search root and `$ATF_VAULT_PASSWORD` is a password. A bare `ATF_`
    would make an input named `path` collide with the first of those, and would spend a
    name for every flow ever written each time the engine claims another variable.
    """
    return f"{VARIABLE_PREFIX}{name.upper()}"


def inputs_from_environment(flow: dict[str, Any], env: Mapping[str, str]) -> dict[str, str]:
    """The inputs the environment supplies, one variable per input the flow declares.

    Driven by the declaration rather than by scanning `env` for the prefix, so a variable
    named for an input this flow has no declaration for is ignored instead of reaching
    check_inputs as an unknown input. A variable is ambient: it outlives the command that
    wanted it, so one exported for one flow would otherwise refuse every other flow run
    from that shell. check_inputs names the variable in its own error, which is what keeps
    a typo findable.
    """
    declared = flow.get("inputs") or {}
    return {name: env[variable_name(name)] for name in declared if variable_name(name) in env}


def check_inputs(flow: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    """Reject unknown and missing inputs before anything runs.

    Takes a mapping, not the CLI's `KEY=VALUE` strings. How inputs were typed is a front
    end's business; the engine only has an opinion about which names are allowed.
    """
    declared = flow.get("inputs") or {}
    for name, meta in declared.items():
        if (meta or {}).get("required") and name not in supplied:
            # Names the variable, not the flag. The variable is the engine's own contract;
            # a flag belongs to whichever front end has one.
            raise FlowError(f"missing required input '{name}' (or set ${variable_name(name)})")
    for name in supplied:
        if name not in declared:
            raise FlowError(f"unknown input '{name}' (declared: {', '.join(declared) or 'none'})")
    return dict(supplied)
