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
python3 src/main.py list                     # what the lookup can see, and what shadows what
python3 src/main.py paths                    # search roots in precedence order

# the examples are the test corpus until tests/ is filled in
python3 src/main.py --workspace examples/sign-release lint sign_release
ATF_VAULT_PASSWORD=demo python3 src/main.py --workspace examples/sign-release \
    run sign_release --input path=release-notes.md      # tool-only, deterministic, free
python3 src/main.py --workspace examples/file-review graph review_file
```

`../examples/file-review` and `../examples/gated-summary` call models: they need the
`claude` CLI authenticated and cost a few cents per run. `../examples/sign-release` needs
nothing (vault password `demo`).

### The pre-push gate

CI runs exactly these; run them before pushing (`pip install ".[lint]"` for `ruff`, which
is not a runtime dependency):

```sh
ruff check src packaging
ruff format --check src packaging
shellcheck $(find . -name '*.sh' -not -path './dist/*' -not -path './build/*' -not -path './var/*')

for flow in examples/*/flows/*.yaml; do
  project=$(dirname "$(dirname "$flow")")
  python3 src/main.py --workspace "$project" lint "$(basename "$flow" .yaml)"
done
```

The flow-lint loop is the substantive check: `lint` runs the same validation `run` does
before its first step (graph, template references, component specs), so it catches far
more than ruff can. Line length is 100, set in `pyproject.toml`.

**There are no tests.** `tests/{unit,integration,e2e}` are empty and the pipeline's test
step is a deliberate placeholder `echo`. Enabling it means adding pytest to the `lint`
extra in `pyproject.toml` and replacing that step.

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

Releasing is tagging `v*`. Bump `__version__` in `../src/cli/branding.py` first. It is the
single source of the version (`pyproject.toml` reads it via `tool.setuptools.dynamic`), and
`../packaging/release.sh` refuses to publish when the tag disagrees with the binary.

## Architecture

### The layering, and the rules that keep it

```
src/main.py      dev entry point: src/ on sys.path, hand to cli/
src/cli/         the terminal: app.py (args/help) → dispatch.py (call+print) → render.py (pure)
src/commands/    one function per command, no terminal attached; returns results.py dataclasses
src/engine/      executor.py runs a flow; specs.py checks components before it does
src/paths/       layered component lookup
src/adapters/    model runtimes as Python modules, registered in code
src/builtin/     components that ship with the engine (data, not code)
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
what the gate said. The loop is inside `run_agent`, not in the graph: the graph has no
cycles and every turn is a fresh session, so the retry has to carry its own history. A
gated step reports the cost of *all* its attempts, because the envelope only knows the
last one. Gates are refused on tool steps: same input, same result, no way out of the loop.

Templates are `{{ dotted.path }}` over five namespaces: `inputs`, `steps`, `secrets`,
`this` (the step's own result, in a switch or a gate) and `gate` (gate feedback only). An
unresolvable path is an error, never an empty string. `validate()` rejects reading from a
step that is not transitively upstream, cycles, unreachable steps, self-pushes, and both
`push` and `switch` on one step.

Anything spawning a subprocess must build its environment with `child_environment()`.
It undoes PyInstaller's `LD_LIBRARY_PATH` rewrite, without which spawned system binaries
load the bundle's OpenSSL and fail in frozen builds only.

### Components are directories with a contract, found by name

`paths/resolver.py` searches roots in precedence order and the first match wins:
`$ATF_PATH` → `./.arctic` → `..` → `~/.arctic` → `../src/builtin`. Under any root,
components live in `tools/`, `agents/`, `flows/`. Overriding is per *name* and total: a
project-level `read_file` replaces the built-in and inherits nothing. Where a component is
*found* never changes where it *runs*: tools execute with cwd set to the workspace root.

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

A flow must not carry model, effort or output shape; those belong to
`agents/<name>/spec.json`. `engine/specs.py` checks a spec against what the runtime
actually reads, so **adding a field the engine reads means adding it to the schema there**;
it also verifies `run.command` exists and is executable, that declared schemas are valid
schemas, and that an agent's settings are ones its adapter accepts. That last check works
by building the payload and validating it against the adapter's own `INPUT_SCHEMA`.

Copy the nearest existing component rather than starting fresh: `../src/builtin/tools/read_file`,
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

- **Agents cannot use tools inside a turn.** An agent declaring `tools` is refused with an
  explanation; the engine's loop stays the only loop. Feed it a tool step's output instead.
- **The `claude_code` adapter's flags are verified against CLI 2.1.222** and move between
  releases. Check `claude --help` before adding a parameter and move
  `VERIFIED_CLI_VERSION`. `isolate` defaults true (`--safe-mode`, so the host's CLAUDE.md,
  skills, hooks and MCP servers stay out of a turn) and `model` should always be set,
  because the CLI's configured default is a per-machine dependency.
- **`../src` is flat**: `cli`, `commands`, `engine`, … are top-level packages here *and* in
  `site-packages`. `[tool.setuptools.packages.find]` lists them explicitly, so a new
  directory under `../src` ships only when someone adds it on purpose. `builtin` needs its
  `package-data` entry or the built-in search layer comes up empty in a wheel.
- Linux x86-64 binaries only (PyInstaller cannot cross-compile); one adapter.

## House style

Comments explain **why**, especially where the obvious approach is wrong. Several exist
because it was tried and failed. If you fix something subtle, leave the reason behind.

Prefer failing loudly over doing something plausible. The engine refuses a flow that reads
from a step it does not depend on, a switch value matching no case, an agent granted a tool
it cannot dispatch, a release whose tag disagrees with its version. Each could have been a
default, a guess, or a silent no-op.
