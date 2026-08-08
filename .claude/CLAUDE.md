# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Arctic Flow (`atf`) is a code-first engine for agentic workflows. A flow is a YAML graph of
steps; each step declares where it *pushes* its result next, and the engine derives the
reverse edges. Read `../README.md` for the user-facing story and `../CONTRIBUTING.md` for the
design rationale; both are current and detailed.

## Commands

Run from a checkout, no install needed. `../src/main.py` puts `../src` on the import path and
hands over to `cli/`, so every `atf …` in the docs works as `python3 src/main.py …`.

```sh
python3 src/main.py --help
python3 src/main.py list                     # every name that resolves, and what shadows what
python3 src/main.py init                     # writes ~/.arctic: careful, this is your real home

# the examples are the test corpus until tests/ is filled in
python3 src/main.py --workspace examples/sign-release lint sign_release
ATF_VAULT_PASSWORD=demo python3 src/main.py --workspace examples/sign-release \
    run sign_release --input path=release-notes.md      # tool-only, deterministic, free
python3 src/main.py --workspace examples/file-review inspect flow review_file
```

`../examples/file-review`, `../examples/gated-summary` and `../examples/draft-review`
(which loops, so it pays for several turns) call models: they need the
`claude` CLI authenticated and cost a few cents per run. `../examples/sign-release` needs
nothing (vault password `demo`).

### The pre-push gate

CI runs exactly these; run them before pushing (`pip install ".[lint]"` for `ruff` and
`".[test]"` for `pytest`, neither of which is a runtime dependency):

```sh
ruff check src packaging tests
ruff format --check src packaging tests
shellcheck $(find . -name '*.sh' -not -path './dist/*' -not -path './build/*' -not -path './var/*')
pytest

for project in examples/*/; do
  python3 src/main.py --workspace "$project" lint
done
```

The flow-lint loop is the substantive check on the examples: `lint` runs the same validation
`run` does before its first step (graph, template references, component specs), so it catches
far more than ruff can. Named with no flow it checks every flow in the workspace and reports
all of them before exiting non-zero. Line length is 100, set in `pyproject.toml`.

### Tests

`tests/unit` covers every module under `../src`, function by function. `tests/integration`
covers what only appears once they are composed: whole commands through the CLI, which
stream each byte left on, the vault end to end, and the shipped examples. Together they run
in under half a minute. `tests/e2e` covers what only appears once it is *built*: a frozen
process spawning `openssl`, the binary re-invoking itself as a tool server, `atf` reached
through the symlink `install.sh` leaves, and the password prompt, which needs a controlling
terminal. It drives `dist/atf/atf`, takes about half a minute, and skips entirely when there
is no binary, so a plain `pytest` is unaffected.

**The rule differs by suite, and it is the thing to know before adding a test.** A unit test
uses **no doubles at all**: it writes a real tool directory and lets the engine spawn a real
process, uses real scrypt and AES-GCM, and opens a real pseudo-terminal via
`tests/support/terminal.py`. An integration test **may use a fake, a stub or a mock, in that
order of preference**, since a fake can still fail for a real reason and a mock only pins how
the code went about something.

An e2e test may use them too, and mostly does not have to: agent steps there name
`adapters.echo`, the shipped adapter that answers from the request, because `ADAPTERS` is
frozen into the binary and nothing outside it can register one.

`../.claude/rules/TESTING.md` has the whole convention: the taxonomy behind that order, the
two things a unit test may still do (environment control and testing a private helper
directly), and how to run each suite. Its `claude`-protocol program,
`tests/support/fake_claude.py`, is autouse in the last two, because a real `claude` on `PATH`
would otherwise be reached by a stray agent step.

`pytest` needs no install step: `[tool.pytest.ini_options]` prepends `src` and `tests` to the
path. `tests/conftest.py` points `$HOME` at `tmp_path`, because `~/.arctic` is a real search
root and the suite must not see what a developer has installed there.

### Build and release

```sh
docker build -f packaging/Dockerfile.build -t atf-build .
docker create --name atf-out atf-build
docker cp atf-out:/out/atf ./dist/ && docker rm atf-out
```

The container is the build recipe both locally and in CI, because the binary embeds an
interpreter and pinned wheels. `docker build` smoke-tests itself and fails rather than
producing a binary that cannot see its own built-ins. After changing `pyproject.toml`
dependencies, regenerate the lock or `../packaging/verify_deps.py` fails the build:

```sh
docker run --rm atf-build pip freeze | grep -viE '^(pip|setuptools|wheel)==' > packaging/requirements-lock.txt
```

Releasing is tagging `vX.Y.Z`, or `vX.Y.Z-rc.N` for a candidate, which publishes as a
prerelease and so stays off `install.sh`'s default. The tag is the version: nothing is
bumped by hand. `../packaging/stamp_version.py` writes it into `../src/cli/branding.py`
before the build installs the project, and `pyproject.toml` reads it from there via
`tool.setuptools.dynamic`. A checkout carries `0.0.0.dev0`.

That stamp is the one step that can silently not happen: run after `pip install` rather
than before, it writes a source tree nothing reads again and the build still passes. So the
Docker smoke test compares the binary against the tag, and `../packaging/release.sh`
refuses to publish when they disagree.

## Architecture

### The layering, and the rules that keep it

```
src/main.py      dev entry point: src/ on sys.path, hand to cli/
src/cli/         the terminal: app.py (args/help) → dispatch.py (call+print) → render.py (pure)
src/commands/    one function per command, no terminal attached; returns results.py dataclasses
src/engine/      executor.py runs a flow; specs.py checks components before it does
src/paths/       layered component lookup, and `~/.arctic/config.yaml` (`config.py`)
src/adapters/    model runtimes as Python modules, registered in code
src/builtin/     components that ship with the engine, and `create`'s scaffolds (data, not code)
src/util/        ways of reading a flow without running it (graph text, Mermaid)
```

Four invariants hold this together. Breaking any of them is easy to do accidentally:

- **Nothing in `commands/` prints, prompts, or reads a stream.** It returns facts;
  `cli/render.py` turns them into strings and `cli/dispatch.py` decides where they go. No
  `argparse.Namespace` crosses into `commands/` either. Arguments are real types.
- **stdout carries the flow's output and nothing else.** Progress, the output frame,
  warnings and traces go to stderr, so `run … > file` yields the result byte for byte.
  (That is also why the frame has no left edge.)
- **`engine/` decides, `cli/` renders.** The engine emits event dicts through an `on_event`
  observer and formats nothing. Events arrive from worker threads, so an observer must be
  concurrency-safe.
- **`util/` is only for things the core could not import.** Validation looks like a sibling
  of `graph`/`diagram` but lives in `engine/specs.py`, because `run` depends on it. `run`
  never imports `util/` at all. The engine works with that directory deleted.

`commands.run` is deliberately two calls: `prepare()` (resolve the flow, check inputs,
unlock the vault) then `run()`, so a front end surfaces every early failure *before* it
paints a progress display.

### Execution model (`engine/executor.py`)

Push, not pull. A step declares `push: [ids]` or `switch:`/`cases:` (evaluated against
`this`, its own result); nothing declares dependencies. `build_graph` inverts the forward
edges, and `execute` runs a step once every inbound edge is `delivered` or `skipped`, on a
thread pool.

The subtle part is skip propagation: an untaken branch's edge is marked `skipped`, a step
whose every inbound edge is skipped is itself skipped, and that cascades. This is what
lets a join downstream of a branch run on both paths instead of waiting forever. A skipped
step still resolves in templates as the literal `(not run)`, so prompts can mention the gap.

An agent step may also carry a `gate`: a tool that has to exit 0 on the step's result
before any edge is delivered. A rejection is not a failure. The tool's output is appended
to the original prompt through the step's own `feedback` template and the agent answers
again, up to `max_attempts` (3 by default, minimum 2), after which the step fails carrying
what the gate said. The loop is inside `run_agent` rather than in the graph, because every
turn is a fresh session, so the retry has to carry its own history. A gated step reports
the cost of *all* its attempts, because the envelope only knows the last one. Gates are
refused on tool steps: same input, same result, no way out of the loop.

A `switch` case naming a step that is already upstream is a **loop**, the only cycle a flow
may have. `back_edges` finds it by a depth-first walk from `start`, so declaration order
decides which edge closes a cycle, and `max_loops` on that step is then required. When it
fires, the body (`descendants_of(head) & ancestors_of(source)`) goes back to `waiting` and
the edges *inside* it go back to `pending`. `results` is deliberately not cleared, so the
next pass reads the last one, and a body step that has not run yet is seeded with
`SKIPPED_RESULT`. The subtle part is that a step which took a back-edge leaves its **other**
edges `pending` rather than `skipped`: marking the exit branch skipped propagates and skips
everything after the loop, so the run ends with no output. Anything derived from an
ordering (waves, guarantees, the cycle check) reads `without_back_edges` instead.

`run.max_minutes` from the user's config is a ceiling on the whole of `execute`, and the
one limit a flow cannot raise, because it is a safeguard rather than a setting. It is the
timeout on the pool's `wait`, so nothing blocks past it, and firing sets a run-wide cancel
event that reaches every tool subprocess: a step's, and a gate's. It cannot reach an agent
turn, because `adapter.run` is a synchronous call with no way in, so a turn already
started runs to its own `timeout_seconds` and the pool's shutdown waits for it. The
ceiling is therefore a ceiling plus at most one turn. Closing that gap means putting
cancellation into the adapter contract, which is why it is left open. `run_agent` checks
the event before each turn, so the gap costs time and never a second paid turn. No
ceiling means no event at all, so a run without a config takes the path it always did.

Templates are `{{ dotted.path }}` over five namespaces: `inputs`, `steps`, `secrets`,
`this` (the step's own result, in a switch or a gate) and `gate` (gate feedback only). An
unresolvable path is an error, never an empty string. `validate()` rejects reading from a
step that is not transitively upstream, an undeclared cycle, unreachable steps, self-pushes,
and both `push` and `switch` on one step.

An input comes from the caller's mapping or from `$ATF_VAR_<NAME>`, merged in
`commands.prepare` with the mapping winning. The prefix is `ATF_VAR_` and not a bare `ATF_`
because `$ATF_PATH` and `$ATF_VAULT_PASSWORD` are the engine's own, so an input named `path`
would have collided. Only names the flow declares are read, so an `ATF_VAR_` exported for
another flow is ignored rather than refused as an unknown input.

Anything spawning a subprocess must build its environment with `child_environment()`.
It undoes PyInstaller's `LD_LIBRARY_PATH` rewrite, without which spawned system binaries
load the bundle's OpenSSL and fail in frozen builds only.

### Components are directories with a contract, found by name

`paths/resolver.py` searches roots in precedence order and the first match wins:
`$ATF_PATH` → `./.arctic` → `..` → `~/.arctic` → `sources` → `../src/builtin`. Under any
root, components live in `tools/`, `agents/`, `flows/`. Overriding is per *name* and total: a
project-level `common/read_file` replaces the built-in and inherits nothing. Where a
component is *found* never changes where it *runs*: tools execute with cwd set to the
workspace root.

A name may carry a namespace, at any depth and for all three kinds: `common/read_file` is
`tools/common/read_file`, `release/sign` is `flows/release/sign.yaml`. A directory holding
a `spec.json` is a component and any other directory is a namespace, so there is nothing to
declare. Everything the engine ships is under `common/`, so overriding one means matching
that whole name. `common/read_file` and a bare `read_file` neither override nor fall back
to each other, and `spec.json` still carries only the leaf. `check_name`
refuses a name whose segments would leave the root (`..`, an absolute path, an empty
segment), which is why the check sits in the resolver and not in `lint`: one place covers
`run`, a grant and `mcp-serve` alike. Granted tools reach a turn under `flat_name`, where
the separator is `__`, because `mcp__atf__<tool>` cannot carry a slash.

`sources` are extra roots named by `~/.arctic/config.yaml`, which `atf init` writes and
`paths/config.py` reads. They sit below your own home layer and above the built-ins, so a
shared library may replace a shipped tool but never one the project or `~/.arctic` defines.
The same file carries `run.max_minutes`, a ceiling on a whole run that `execute` enforces
and no flow may raise; an unknown key in it is refused rather than ignored. `Paths` loads
it eagerly, so a broken config stops every command rather than one.

`atf create <kind> <name>` writes one, out of `../src/builtin/scaffolds/<kind>/`, into
`./.arctic` when the workspace has one and the workspace root otherwise: the top of that
same precedence list, so what is created is what then resolves. `$ATF_PATH` and `~/.arctic`
are deliberately not offered. The scaffolds are data rather than strings in
`commands/scaffold.py`, so a scaffolded `run.sh` is covered by `shellcheck` and read as
shell; `__NAME__` is the placeholder, and `_declared_name` decides whether it becomes the
whole name (a flow, which is what `run` is handed) or the leaf (a spec, whose namespace is
the directory it sits in). Everything written has to be runnable as it is, which only
`tests/unit/commands/test_scaffold.py` checks: **a new requirement in `engine/specs.py`
means updating the scaffold too**.

| Kind | Is | Contract |
| --- | --- | --- |
| tool | directory | `spec.json`, a markdown doc, executable `run.sh`; one JSON object on stdin, result on stdout, errors one line on stderr with a code listed in its own `exit_codes` |
| agent | directory | `spec.json` plus `agent.md`, which **is** the system prompt, read verbatim |
| flow | one YAML file | names the graph and nothing else |
| adapter | Python module | in `../src/adapters`, plus an entry in `ADAPTERS`; declares `NAME`, `DESCRIPTION`, `INPUT_SCHEMA`, `run(payload, env)` |

Adapters are the deliberate exception to name-based lookup: there is no
`~/.arctic/adapters/`. A tool is user-extensible in any language so it earns a subprocess;
an adapter is engine infrastructure called in-process. `ADAPTERS` is static imports on
purpose: a frozen build misses anything resolved dynamically.

Two ship. `claude_code` calls a model. `echo` answers from the request, so a flow's graph,
branches, gates and templates run with no runtime, no network and no cost, and its prompt
can carry `!fail`, `!json` or `!invocation` to steer or inspect a turn. That is also what
lets `tests/e2e` reach an agent step at all: nothing outside the binary can add to a
registry that was frozen into it.

A flow must not carry model, effort or output shape; those belong to
`agents/<name>/spec.json`. `engine/specs.py` checks a spec against what the runtime
actually reads, so **adding a field the engine reads means adding it to the schema there**;
it also verifies `run.command` exists and is executable, that declared schemas are valid
schemas, and that an agent's settings are ones its adapter accepts. That last check works
by building the payload and validating it against the adapter's own `INPUT_SCHEMA`.

Copy the nearest existing component rather than starting fresh: `../src/builtin/tools/common/read_file`,
`../src/adapters/claude_code.py`, `../examples/file-review/agents/summarizer`,
`../examples/sign-release/flows/sign_release.yaml`.

### Secrets

A step declares `secrets: [name]` and the engine passes that step **only** those, as
environment variables. `lint` enforces two rules: `{{ secrets.NAME }}` works in a tool's
`input` only for a name that step declared, and a secret in an **agent prompt** is refused
outright, because it would be sent to the model and persist in the session. Credentials reach
an adapter through the environment instead. Secrets are scrubbed from errors and traces but
**not** from step results, since a result is data the flow asked for. There is deliberately
no `--vault-password` flag; use `--vault-password-file`, `$ATF_VAULT_PASSWORD` or
`$ATF_VAULT_PASSWORD_FILE`.

## Constraints to know before changing things

- **An agent's `tools` are the engine's tools, not its runtime's.** They reach the turn
  over MCP, served by `atf mcp-serve`, and run through the same `invoke()` a tool step
  uses, so containment, schema check and timeout are unchanged. A tool spec is already an
  MCP tool definition (`description` plus its doc, `input_schema`); there is no second
  spec. The tool's *name* there is the one it was looked up by, not `spec["name"]`, which
  for a namespaced tool is only the leaf. Naming a runtime's own built-ins instead would
  tie one agent spec to one adapter.
- **A granted tool's MCP name is `flat_name` of its lookup name.** `common/read_file` is
  offered as `common__read_file`, because a client builds `mcp__atf__<tool>` and a slash is
  not legal in a tool name. `cli.mcp_server` decides it and `adapters.claude_code` writes
  the same string into `--allowedTools`; drift and every tool in the turn is unpermitted,
  which reaches a user as a model saying they do not work. Never undo it by string surgery:
  `git__commit` is a legal directory name, so `serve` keeps the mapping and `validate`
  refuses a grant where two names flatten onto one.
- **Granting a tool whose `permissions.filesystem` is `write` needs `unattended: true`**
  on the agent spec. Nothing approves a call an agent makes for itself. That gate is why
  `permissions` is required and `filesystem` is an enum: `"rw"` or a typo would read as
  "not write" and open it silently.
- **No secret reaches an in-turn tool.** Granting a tool that declares `secrets` is
  refused, and so is a step that declares `secrets` and runs a tool-granted agent, because
  the adapter's environment is what an in-turn call would inherit. Scoping a grant per call
  is the follow-up that would lift this.
- **In-turn calls are reported.** `mcp-serve --events` appends one line per call and the
  engine forwards it to `on_event`, so a turn that read nine files does not look like one
  silent step.
- **Tool calls run concurrently, up to `MAX_CONCURRENT_CALLS`.** Only `tools/call` leaves
  the read loop; `initialize`, `tools/list` and `ping` are answered where they are read,
  because a queued `ping` is the stall the pool exists to remove. Two things follow and
  both are load-bearing: writes are locked, or two replies interleave and the framing is
  gone, and a worker carries its own error guard, because an exception inside a future is
  kept by the future rather than raised. Replies arrive as calls finish, so anything
  reading them keys by request id.
- **A cancelled call is stopped, not just unanswered.** `notifications/cancelled` sets
  the call's event; `spawn` signals it, TERM then KILL, and no reply is sent. The cancel
  is handled on the read loop, because pooled it would queue behind the call it cancels.
- **`cancel` and `grouped` are separate arguments to `spawn`, and conflating them is a
  bug.** `cancel` is whether the work can be stopped; both callers pass one, an in-turn
  call from its client and a step from the run ceiling. `grouped` is whether the call has
  a terminal to answer to. An in-turn call has none, so it gets `start_new_session` and
  the whole process tree is signalled. A step stays in the caller's process group, so
  Ctrl-C on `atf run` still reaches its tool, and the price is that only the direct child
  is signalled: a step's tool that backgrounded something can leave it behind.
- **The `claude_code` adapter's flags are verified against CLI 2.1.224** and move between
  releases. Check `claude --help` before adding a parameter and move
  `VERIFIED_CLI_VERSION`. `model` is required, because the CLI's configured default is a
  per-machine dependency. `isolate` defaults true, but how it is spelled depends on the
  turn: `--safe-mode` without tools, and `--setting-sources "" --disable-slash-commands`
  with them, because `--safe-mode` disables MCP servers and would silently leave the agent
  with no tools at all. That substitute is narrower than the flag it replaces, and the
  gap is listed in `build_args`.
- **`../src` is flat**: `cli`, `commands`, `engine`, … are top-level packages here *and* in
  `site-packages`. `[tool.setuptools.packages.find]` lists them explicitly, so a new
  directory under `../src` ships only when someone adds it on purpose. `builtin` needs its
  `package-data` entry or the built-in search layer comes up empty in a wheel.
- Linux x86-64 binaries only (PyInstaller cannot cross-compile). One adapter calls a model:
  `claude_code`. `echo` answers from the request and is a dry run, not a second runtime, so
  the adapter interface still has one implementation to be shaped by.

## House style

Comments explain **why**, especially where the obvious approach is wrong. Several exist
because it was tried and failed. If you fix something subtle, leave the reason behind.

Prefer failing loudly over doing something plausible. The engine refuses a flow that reads
from a step it does not depend on, a switch value matching no case, an agent granted a tool
that writes without saying it is unattended, a release whose tag disagrees with its version.
Each could have been a default, a guess, or a silent no-op.
