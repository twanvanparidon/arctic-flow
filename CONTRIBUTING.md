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

### A virtual environment

Only the tools need one. The engine runs on its three runtime dependencies, which a system
Python often already has. `pytest` and `ruff` are neither runtime dependencies nor usually
installed, so the gate has nowhere to get them.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test,lint]"
```

`-e` puts `atf` on your PATH pointing at the checkout, so every `atf …` in this file works
without the `python3 src/main.py` prefix.

On Debian and Ubuntu this fails until `sudo apt install python3-venv python3-pip`: the
distro ships the standard library without `ensurepip`. Add `--system-site-packages` to reuse
what apt already installed, which saves building a wheel from source on a new Python.

It is also how your versions come to match CI's. A distro `jsonschema` can be several minor
versions behind what `pip` resolves, and the engine's validation messages come out of that
library.

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
tests/                   unit, integration, and e2e against the built binary
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
ruff check src packaging tests
ruff format --check src packaging tests
shellcheck $(find . -name '*.sh' -not -path './dist/*' -not -path './build/*' -not -path './var/*')
pytest

# the engine validates flows better than any generic linter
for flow in examples/*/flows/*.yaml; do
  project=$(dirname "$(dirname "$flow")")
  python3 src/main.py --workspace "$project" lint "$(basename "$flow" .yaml)"
done
```

`ruff` settings live in `pyproject.toml`. Line length is 100. The default 88 wanted 229
lines of churn against code written to a wider measure.

`ruff`, `pytest` and `coverage` come from the two extras, installed together by the
`pip install -e` above. None is a runtime dependency, so none of them ships.

The pipeline runs the same commands, plus `coverage` and `--junitxml`. It puts the counts
and the coverage table on the run page, and uploads the JUnit XML and an HTML coverage
report as artifacts. Coverage never fails the build.

## Tests

`tests/unit` and `tests/integration` are written and run in under half a minute.
`tests/e2e` drives the built binary and takes about as long again, but only once there is
one to drive: without a build it skips, so a plain `pytest` on a checkout runs the first
two. How the suite is built, and what belongs in which of the three, is in
`.claude/rules/TESTING.md`.

**A unit test uses no doubles.** A tool test writes a real tool directory and the engine
spawns a real process; a vault test encrypts with real scrypt and AES-GCM; a test about
`isatty()` opens a real pseudo-terminal. The failures worth catching there are the ones a
substitute cannot have: a lost executable bit, a process that outlives its timeout, a secret
in an environment it was not granted.

**An integration test may use a fake, a stub or a mock, and should prefer them in that
order.** A fake is a working implementation, so it can still fail for a real reason. A stub
only answers. A mock asserts on how the code went about something rather than on what it
decided, so it is the last resort.

The one in the tree today is a fake: `tests/support/fake_claude.py` speaks the Claude Code
CLI's protocol, so the adapter spawns a real process without an account or a network.
`PATH=/usr/bin:/bin pytest` passes, which is how you can tell nothing reaches the real one.

**An end-to-end test is about the artefact, not this source.** It belongs there when it
would pass against `src/` and still ship something broken: a frozen process spawning
`openssl`, the binary re-invoking itself to serve an agent's tools, `atf` reached through
the symlink `install.sh` leaves, a password prompt that needs a controlling terminal. Its
agent steps use `adapters.echo`, which ships and answers from the request, because a
registry frozen into a binary cannot be added to from outside.

## Adding a component

Copy the nearest existing one. That is the intended way in.

| Adding | Copy | Needs |
| ------ | ---- | ----- |
| A tool | `src/builtin/tools/read_file` | `spec.json`, `tool.md`, executable `run.sh` |
| A tool that writes | `src/builtin/tools/write_file` | the same, plus `permissions.filesystem: "write"` |
| An adapter | `src/adapters/claude_code.py` | one Python module, plus an entry in `ADAPTERS` |
| An agent | `examples/file-review/agents/summarizer` | `spec.json`, `agent.md` |
| A flow | `examples/sign-release/flows/sign_release.yaml` | one YAML file |

Conventions that are load-bearing rather than stylistic:

- **`run.sh` reads one JSON object on stdin** and writes its result to stdout. Errors go to
  stderr as a single line, with an exit code listed in the component's own `exit_codes`.
  The engine turns that code back into a message using your own text.
- **`spec.json` is checked before anything runs.** `engine/specs.py` requires what the
  runtime actually reads (`name`, `description`, `run.command`, `input_schema` for a tool;
  `name`, `description`, `adapter` for an agent; `permissions` for a tool, because a grant
  is decided from it) and verifies that `run.command` exists and
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
- **An agent's `tools` are the engine's tools.** They reach the turn over MCP, served by
  `atf mcp-serve`, and run through the same `invoke()` a tool step uses. A tool spec is
  already an MCP tool definition, so nothing in it is written twice. Naming a runtime's own
  built-in tools instead would tie one agent spec to one adapter.
- **A model asking for several tools at once gets them at once.** `mcp-serve` runs calls
  on a pool, so a turn takes the longest rather than the sum. Replies come back as calls
  finish, which is why the test helpers key them by request id and never by position.
- **Granting a tool that writes needs `unattended: true`.** Nothing approves a call an agent
  makes for itself, so a grant that can change the workspace is declared where the grant is.
- **A granted tool gets no secrets.** Granting one that declares `secrets` is refused, and
  so is a step that declares `secrets` and runs a tool-granted agent. Scoping a grant to a
  single in-turn call is the follow-up that would lift that; until then, secrets belong to
  tool steps.
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

## Inputs

A flow declares what it takes, and `check_inputs` refuses an input it never declared or a
required one left out. That runs in `prepare`, before the vault is touched and before the
first step.

An input has two sources: a mapping the caller passes, and `$ATF_VAR_<NAME>` from the
environment. Three decisions in that are worth knowing.

**The prefix is not a bare `ATF_`.** `$ATF_PATH` is the highest-precedence search root and
`$ATF_VAULT_PASSWORD` is a password. Bare, an input named `path` would collide with the
first of those, and every variable the engine claimed afterwards would spend a name for
every flow ever written. Inputs get their own prefix so the two sets cannot meet.

**Reading is driven by the declaration**, not by scanning the environment for the prefix. A
variable is ambient: it outlives the command that wanted it. Refusing an `ATF_VAR_` the
flow has no input for would mean one exported for one flow breaks every other flow in that
shell, so it is ignored instead. This is the one place the engine prefers ignoring something
to failing loudly, and the missing-input error names the variable so a typo is still
findable.

**Precedence lives in `commands.prepare`**, the only place both sources exist. A passed
input wins, because it was passed for this run and the variable was exported for the shell.
`inputs_from_environment` reads `paths.env` rather than `os.environ`, so one environment
decides both the search roots and the inputs, and a caller isolating one isolates both.

Values are strings from either source. A declaration's `type:` is documentation; nothing
coerces or checks it.

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

A third rule follows from agents being able to call tools. **A tool an agent calls gets no
secrets**, and `lint` enforces that from both ends: granting a tool that declares `secrets`
is refused, and so is a step that declares `secrets` and runs a tool-granted agent. The
reason is that the adapter is handed the step's grant, and a tool the agent calls would
inherit the lot, which is wider than what `spawn` promises a component. The engine keeps
the secret out of the process tree entirely rather than trying to strip it two processes
down. Scoping a grant to one in-turn call is what would lift the restriction, and nothing
does that yet.

## Deferred, and what would earn it

Written down so the decision is not re-litigated, and so the trigger is recognisable.

**A portable model vocabulary.** An agent names `model: "sonnet"`, which is one provider's
word. The obvious fix is a `tier` enum on the agent (`fast`/`balanced`/`deep`) plus a
`MODELS: dict[str, str]` on each adapter, resolved inside `specs.adapter_parameters` so the
existing lint probe is the check, with `tier` and `model` together refused.

It is not built, for two reasons. The vocabulary is unknowable with one adapter: `model`
and `effort` both move depth, so whether `deep` means the bigger model or more thinking has
no evidence behind it, and `AGENT_SPEC_SCHEMA` is a contract with a release already tagged.
And it would not deliver what it looks like it delivers, because `adapter` is itself
required in every agent spec with no override anywhere. A tier changes what you edit per
spec from two fields to one. The larger coupling is `adapter`, and an ambient `$ATF_ADAPTER`
would recreate exactly the per-machine dependency `isolate` and the required `model` exist
to remove.

**The trigger is a second adapter.** It settles the vocabulary, and it is what the rule in
`.claude/rules/CONTRIBUTING.md` already names: the second adapter is what earns a change to
the adapter interface, not the first.

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

Once `dist/atf/atf` exists, the end-to-end suite finds it:

```sh
pytest tests/e2e
```

The pipeline runs it on a release tag, between the build and the publish, so a binary that
fails it is never released.

If you change `pyproject.toml`'s dependencies, regenerate the lock:

```sh
docker run --rm atf-build pip freeze | grep -viE '^(pip|setuptools|wheel)==' > packaging/requirements-lock.txt
```

`packaging/verify_deps.py` fails the build when the lock and `pyproject.toml` disagree, so
forgetting this is loud rather than silent.

PyInstaller cannot cross-compile: this produces a Linux x86-64 binary. macOS and Windows
need runners on those platforms.

## Releasing

Tag it. `.github/workflows/ci.yml` runs lint, test and build on every pull request; a
version tag runs them again and adds the release job, which publishes a GitHub Release.

```sh
git tag v0.2.0 && git push origin v0.2.0            # what install.sh installs
git tag v0.2.0-rc.1 && git push origin v0.2.0-rc.1  # published, but not by default
```

The tag is the version, and nothing to edit beforehand. `packaging/stamp_version.py` writes
it into `src/cli/branding.py` inside the build, before `pip install` reads it through
pyproject.toml's dynamic version. A checkout carries `0.0.0.dev0`, which is what an untagged
build honestly reports.

The hyphen is what makes it a prerelease. GitHub leaves those out of `/releases/latest`,
the redirect `install.sh` follows, so a candidate is installed only when named:
`install.sh --version v0.2.0-rc.1`.

Those two shapes are the only tags the workflow answers to, and the only two the stamper
accepts. Anything else starts no run at all.

The wheel spells it differently: setuptools normalises to PEP 440, so `v0.2.0-rc.1` ships
`arctic_flow-0.2.0rc1-*.whl` beside the tarball's `atf-0.2.0-rc.1-linux-x86_64.tar.gz`.

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
that writes without saying it is unattended, and a release whose tag disagrees with its
version. Each of those could have been a default, a guess, or a silent no-op instead.

## Licence

Arctic Flow is GPLv3 or later. By opening a pull request you agree that your contribution
is licensed under those terms, and that you have the right to submit it.

There is no CLA and nothing to sign. Copyright stays with whoever wrote the code.
