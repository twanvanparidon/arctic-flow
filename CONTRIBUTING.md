# Contributing to Arctic Flow

```
   *  .  *
    \ | /       A R C T I C   F L O W
  .-- * --.     atf: push-based agentic workflows
    / | \
   *  .  *
```

## What this is

A flow is a graph of steps. Each step declares **where it hands its result next**, not
what it waits for, so a flow reads forwards. The engine derives the reverse edges, runs
whatever is ready, and delivers results onward.

Three ideas carry most of the design. If you understand these, the code will not surprise
you:

**A component is a directory with a contract.** A tool is a directory holding `spec.json`,
a markdown file and an executable `run.sh`; an agent is `spec.json` plus `agent.md`. Nothing
about one lives outside its own directory, so it can be added, replaced or deleted without
touching the engine.

Adapters are the deliberate exception: they are Python modules in `adapters/`,
registered in code. A tool is user-extensible and may be written in any language, so it
earns a subprocess and a JSON-on-stdin contract. An adapter exists to be called by the
engine, in-process, and paid that cost for nothing. It still declares an `INPUT_SCHEMA` the
engine validates against, so the guarantee is the same one. What went away is a file on
disk restating what the module already says, and a layer of shell quoting in between.

**Components are found by name, not by path.** `paths/resolver.py` searches roots in
precedence order and the first match wins: working directory first, then `~/.arctic`, then
what ships with the engine. A project overrides anything it inherits by defining
the same name. Adapters are not part of this: see above.

**A branch that is not taken skips its subtree.** `switch` picks one case; the edges to the
others are marked skipped, and skipping propagates. That is what lets a join downstream of
a branch run on both paths instead of waiting forever. A skipped step still resolves in
templates, as the literal `(not run)`.

## Running it

No install needed:

```sh
python3 src/main.py --help
python3 src/main.py --workspace examples/sign-release run sign_release --input path=release-notes.md
```

`src/main.py` puts `src/` on the import path and hands over to `cli/`. Installed
(`pip install .`) or frozen, the same CLI is reached as `atf`; the shim exists so a
checkout runs without either.

Start with the two examples. `examples/sign-release` is tool-only and demonstrates the
vault: no credentials, no network, deterministic. `examples/file-review` uses agents, so
it costs money and needs the `claude` CLI authenticated.

## Layout

```
src/main.py              development entry point; puts src/ on the path
src/commands/            what the engine can be asked to do, with no front end attached
src/cli/                 the terminal front end: arguments, help, output, progress
src/engine/              executor.py runs a flow, specs.py checks one first
src/paths/               layered component lookup
src/vault/               the encrypted secrets file
src/adapters/            model runtimes, as Python modules
src/builtin/             tools that ship with the engine
src/util/                ways of looking at a flow without running it
examples/                self-contained sample projects
packaging/               build recipe, PyInstaller spec, release and install scripts
tests/                   unit, integration, e2e (empty for now)
```

`cli/` renders; `engine/` decides. The engine emits events and never formats
anything. That is how progress output was added without touching it. Keep that
split.

### commands/, and why there is a layer between them

`commands/` is one function per command (`run`, `lint`, `diagram`, `vault set`), and it
knows nothing about a terminal. Each takes ordinary arguments, returns a dataclass from
`commands/results.py`, and raises on failure. Nothing there prints, reads stdin, prompts,
or touches `argparse`.

That leaves `cli/` with three files and a clear job:

```
cli/app.py         the shape of the interface: commands, flags, help text, exit codes
cli/dispatch.py    Namespace in, command called, result printed, exit code out
cli/render.py      a result in, a string out: pure, no stream, no colour
```

The point is a second front end. A TUI reimplements `dispatch.py`, reuses whichever of
`render.py`'s strings still read well in a pane, and calls the same commands the CLI
calls. No command reimplemented, no behaviour to keep in sync. The two interactive parts
are injected rather than assumed: a vault password arrives as a string or a callable
(`commands.Password`), and live progress as an observer (`commands.EventObserver`), which
is the same event stream the engine already emitted.

Two habits keep it usable from both:

- **A `print` in `commands/` breaks it.** A command that writes to a stream has decided
  something that is not its to decide. Return the fact; let the front end place it.
- **Human wording lives in the front end.** `--help` prose documents flags and pipes,
  which is a command line's vocabulary. A menu label is a TUI's. Neither belongs beside
  the command.

`run` is deliberately two calls, `prepare` then `run`, because a front end wants every
early failure *before* it puts a progress display on screen: an unknown flow, a bad input,
a locked vault. Folded into one call, a mistyped input arrives under a spinner with a
"failed after 0ms" over the top of it.

`util/` is the third category: **things that read a flow without running it.** The graph
listing and the Mermaid diagram live there, they are imported lazily by the commands that
need them, and `run` never touches the package at all. The engine works with the whole
directory deleted.

Validation deliberately does **not** live there, even though `atf lint` looks like a sibling
of `atf graph`. Its checks are the ones `run` performs before executing anything, so they sit
in `engine/specs.py` next to the code that depends on them. The test for whether something
belongs in `util/`: could the core import it? If yes, it is not a util.

**Stdout carries the flow's output and nothing else.** Progress, the frame around
the output, warnings and traces all go to stderr, so `run … > file` produces the result
alone. That is why the frame has no left edge. A marker on those lines would mean editing
bytes the flow produced.

### On the flat `src/`

There is no wrapping package: `cli`, `commands`, `engine`, `paths`, `vault`, `adapters`,
`builtin` and `util` are top-level, both here and in `site-packages` once installed. That
keeps imports short and makes an import that works from a checkout work identically when
installed.

The cost is real and worth knowing before you add a directory: those names are generic, so
`pip install arctic-flow` claims all eight of them, `commands` most of all. Another
distribution shipping its own top-level `engine/` or `util/` would collide with ours, and
`import util` in an unrelated project can pick ours up. The binary and from-source paths
are unaffected. Only the wheel shares a namespace. If that becomes a problem, the fix is
to nest everything under one distinct package (`arcticflow/`), which is a mechanical rename
of the import prefix plus four lines of `pyproject.toml`.

`[tool.setuptools.packages.find]` lists the packages explicitly rather than globbing, so a
new directory under `src/` ships only when someone adds it to that list on purpose.

## Before you push

The pipeline runs these, so run them first:

```sh
ruff check src packaging
ruff format --check src packaging
shellcheck $(find . -name '*.sh' -not -path './dist/*' -not -path './build/*' -not -path './var/*')

# the engine validates flows better than any generic linter
for flow in examples/*/flows/*.yaml; do
  project=$(dirname "$(dirname "$flow")")
  python3 src/main.py --workspace "$project" lint "$(basename "$flow" .yaml)"
done
```

`ruff` settings live in `pyproject.toml`. Line length is 100. The default 88 wanted 229
lines of churn against code written to a wider measure.

Tests are next; `tests/{unit,integration,e2e}` exist and the pipeline has a step waiting
for them.

## Adding a component

Copy the nearest existing one. That is the intended way in.

| Adding | Copy | Needs |
| ------ | ---- | ----- |
| A tool | `src/builtin/tools/read_file` | `spec.json`, `tool.md`, executable `run.sh` |
| An adapter | `src/adapters/claude_code.py` | one Python module, plus an entry in `ADAPTERS` |
| An agent | `examples/file-review/agents/summarizer` | `spec.json`, `agent.md` |
| A flow | `examples/sign-release/flows/sign_release.yaml` | one YAML file |

Conventions that are load-bearing rather than stylistic:

- **`run.sh` reads one JSON object on stdin** and writes its result to stdout. Errors go to
  stderr as a single line, with an exit code listed in the component's own `exit_codes`.
  The engine turns that code back into a message using your own text.
- **`spec.json` is checked before anything runs.** `engine/specs.py` requires what the
  runtime actually reads (`name`, `description`, `run.command`, `input_schema` for a tool;
  `name`, `description`, `adapter` for an agent) and verifies that `run.command` exists and
  is executable, that declared schemas are valid schemas, and that an agent's settings are
  ones its adapter accepts. Add a field the engine reads, and add it to the schema there.
- **`input_schema` is enforced twice.** Against the real payload at run time, and against the
  flow's static `input` at lint time: unknown keys, missing required keys, and literal
  values of the wrong type are caught without running. Set `additionalProperties: false` so
  the first of those works.
- **`agent.md` *is* the system prompt**, read verbatim. That keeps prompts editable and
  reviewable as prose instead of escaped into a JSON string.
- **A flow names the graph, nothing else.** Model, effort, tools and output shape belong to
  the agent, in `agents/<name>/spec.json`.
- **An agent cannot use tools inside a turn.** One declaring `tools` is refused with an
  explanation rather than quietly ignored, so the engine's loop stays the only loop. Feed it
  the output of a tool step instead.
- **Do not append a trailing newline to a single-value tool output.** A digest or an id gets
  templated mid-line, and a stray newline breaks the line it lands in.

## Gates

An agent step can name a tool that has to accept its result before the result goes
anywhere:

```yaml
- id: draft
  agent: brief_writer
  prompt: |
    Summarise this file in at most 60 words.
    ...
  gate:
    tool: word_limit
    input:
      text: "{{ this.text }}"      # `this` is the result being checked
      max_words: 60
    max_attempts: 3
    feedback: |
      Your last answer was rejected by word_limit:

      {{ gate.text }}

      Write it again, inside the limit.
```

Exit 0 accepts. Any other exit rejects, and the tool's output becomes `{{ gate.text }}` in
the next prompt, appended to the original one. Every turn is a fresh session, so the retry
carries its own history or it has none. When the attempts run out the step fails with what
the gate last said, and nothing downstream ever sees a result the gate refused.

The loop is inside the step. There is no edge back to the agent, so a flow still has no
cycles, and `graph` and `diagram` report the gate rather than drawing one.

Four rules, all enforced by `lint`:

- **Gates are for agent steps.** A tool handed the same input returns the same result, so
  the retry could only spend the attempts and arrive back where it started.
- **`feedback` is required**, for the same reason. A retry that says nothing about what was
  wrong is the first attempt again.
- **`max_attempts` is 2 or more**, and 3 by default. One attempt leaves no turn to act on
  the feedback.
- **`{{ secrets.NAME }}` works in the gate's `input`**, which is a tool's input, and is
  refused in `feedback`, which becomes a prompt.

Any tool is a gate, with no second contract to write to. The cost of that is a gate that is
itself broken: it reports its own error the same way a rejection arrives, and spends the
attempts before the step fails.

## Secrets

A step declares what it may read:

```yaml
- id: sign
  tool: hmac_sign
  secrets: [signing_key]
```

The engine passes that step **only** those names, as environment variables. Two rules
follow, and `lint` enforces both:

- `{{ secrets.NAME }}` works in a tool's `input`, but only for a name that step declared.
- A secret in an **agent prompt** is refused outright. It would be sent to the model and
  stay in the session. Credentials reach an adapter through the environment instead.

Secret values are scrubbed from errors and traces, but **not** from step results: a result
is data the flow asked for, and scrubbing it would corrupt the workflow rather than
protect it. So never template a secret into something you would not print.

## Building

The build runs in a container, which is also what CI does. The binary embeds an interpreter
and pinned wheels, so the build environment is part of the artifact:

```sh
docker build -f packaging/Dockerfile.build -t atf-build .
docker create --name atf-out atf-build
docker cp atf-out:/out/atf ./dist/ && docker rm atf-out
```

`docker build` fails rather than producing a binary that cannot see its own built-in
components. That check has already caught a real regression.

If you change `pyproject.toml`'s dependencies, regenerate the lock:

```sh
docker run --rm atf-build pip freeze | grep -viE '^(pip|setuptools|wheel)==' > packaging/requirements-lock.txt
```

`packaging/verify_deps.py` fails the build when the lock and `pyproject.toml` disagree, so
forgetting this is loud rather than silent.

PyInstaller cannot cross-compile: this produces a Linux x86-64 binary. macOS and Windows
need runners on those platforms.

## Releasing

Tag it. `.github/workflows/ci.yml` runs lint, test and build on every pull request; a `v*`
tag runs them again and adds the release job, which publishes a GitHub Release.

```sh
# bump src/cli/branding.py first: the release refuses to publish if the tag disagrees
git tag v0.2.0 && git push origin v0.2.0
```

`packaging/release.sh` builds the tarball, a `sha256` that verifies with `sha256sum -c`,
and a wheel. The tarball is reproducible (`--sort`, `--mtime`, fixed ownership), so the
checksum means something across rebuilds.

Uploading needs no credential of its own. `gh release create` runs with the workflow's
`GITHUB_TOKEN`, which the release job grants `contents: write`.

`packaging/install.sh` is the other end of that. It reads the tag from the redirect off
`/releases/latest`, checks the `sha256` before unpacking anything, and installs the whole
directory with a link to the binary. Users fetch it raw from `main`, so a fix to it reaches
them without a release, and it names the asset the way `release.sh` writes it. Renaming an
artefact means changing both.

## House style

The code is commented more heavily than most, and deliberately: comments explain **why**,
especially where the obvious approach is wrong. Several exist because the obvious approach
was tried and failed: `$(...)` stripping the trailing newline off a payload being signed,
`class a,b c` in Mermaid meaning two node ids rather than two classes, `import build` being
satisfied by an empty `build/` directory. If you fix something subtle, leave the reason
behind.

Prefer failing loudly over doing something plausible. The engine refuses a flow that reads
from a step it does not depend on, a switch value matching no case, an agent granted a tool
the engine cannot dispatch, and a release whose tag disagrees with its version. Each of
those could have been a default, a guess, or a silent no-op instead.

## Licence

Arctic Flow is GPLv3 or later. By opening a pull request you agree that your contribution
is licensed under those terms, and that you have the right to submit it.

There is no CLA and nothing to sign. Copyright stays with whoever wrote the code.
