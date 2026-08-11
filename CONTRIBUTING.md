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
any `sources` that directory's `config.yaml` names, then what ships with the engine. A
project overrides anything it inherits by defining the same name. Adapters are not part of
this: see above.

**There is one config file, and it is small.** `~/.arctic/config.yaml`, written by
`atf init` and read by `paths/config.py`. It holds what neither a flow nor a spec can:
extra roots to search, which shipped packs are switched on, and a ceiling on how long a
run may take. Anything a flow should decide belongs in the flow, so the bar for adding a
key here is that no component and no flow could own it. An unknown key is refused rather
than ignored.

**A pack is components that ship switched off.** `src/builtin/packs/<name>/` is a search
root laid out like any other, spliced in above the built-ins when `packs:` names it. It
sits *inside* `builtin/`, which is the whole design: `arctic/` resolves inside the built-in
root or nowhere, so a pack may define `arctic/git/commit` and a source never can. See
`PACKS_DIR`, and "Adding a pack" below.

**A name may carry a namespace.** `arctic/read_file` is `tools/arctic/read_file` under
whichever root wins, at any depth, with nothing to declare: a directory holding a
`spec.json` is a component and any other directory is a namespace. The name is the whole
path, so grouping a tool moves it rather than aliasing it and a bare `read_file` neither
overrides nor falls back to it. The first segment says who a component came from, the way
`vendor/package` does in Composer.

**And one namespace is not overridable.** Everything the engine ships sits under `arctic/`,
and nothing outside `builtin/` may define a name inside it: the resolver refuses rather than
choosing, wherever the other definition came from. This is a security property, not tidiness.
`tool: arctic/read_file` in a flow has to mean the tool that ships, or reading a flow says
nothing about what it runs, and a cloned repository is a search root. See `ENGINE_NAMESPACE`
and `Paths.intruders`.

**A branch that is not taken skips its subtree.** `switch` picks one case; the edges to the
others are marked skipped, and skipping propagates. That is what lets a join downstream of
a branch run on both paths instead of waiting forever. A skipped step still resolves in
templates, as the literal `(not run)`, and is *false* in a conditional, so a prompt can leave
out the section that would have read it. See "Prompts and templates".

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

`commands/` is one function per command (`run`, `lint`, `graph`, `vault set`), and it
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

Validation deliberately does **not** live there, even though `atf lint` looks like a
sibling of `atf inspect flow`. Its checks are the ones `run` performs before executing
anything, so they sit in `engine/specs.py` next to the code that depends on them. The test
for whether something belongs in `util/`: could the core import it? If yes, it is not a
util.

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
shellcheck -x $(find . -name '*.sh' -not -path './dist/*' -not -path './build/*' -not -path './var/*')
pytest

# the engine validates flows better than any generic linter. A bare `lint` checks every
# flow in the workspace and exits non-zero if any of them failed.
for project in examples/*/; do
  python3 src/main.py --workspace "$project" lint
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

`atf create` writes one from the scaffold that ships with the engine, into `./.arctic` when
the project keeps one and the project root otherwise:

```sh
atf create flow review          # flows/review.yaml
atf create agent reviewer       # agents/reviewer/: spec.json, agent.md
atf create tool deploy/notify   # tools/deploy/notify/: spec.json, tool.md, run.sh
```

The scaffolds are data, under `src/builtin/scaffolds/`, for the reason the built-in tools
are: a scaffolded `run.sh` is shell, so `shellcheck` reads it with the rest of the
repository and it is edited as shell rather than as a string in a Python module. Changing
what a new component looks like is editing those files.

The scaffold is a plain component of each kind. Anything past that, copy the nearest
existing one: a tool that writes, one that reaches the network, an adapter.

| Adding | Copy | Needs |
| ------ | ---- | ----- |
| A tool | `src/builtin/tools/arctic/read_file` | `spec.json`, `tool.md`, executable `run.sh` |
| A tool that writes | `src/builtin/tools/arctic/write_file` | the same, plus `permissions.filesystem: "write"` |
| A tool that searches | `src/builtin/tools/arctic/grep` | the same, and only POSIX options, for the reason in its doc |
| A tool that lists paths | `src/builtin/tools/arctic/glob` | the same, and a sorted result so truncation is stable |
| A tool that reaches the network | `src/builtin/tools/arctic/fetch_url` | the same, plus `permissions.network: true` |
| An adapter | `src/adapters/claude_code.py` | one Python module, plus an entry in `ADAPTERS` |
| An agent | `examples/file-review/agents/summarizer` | `spec.json`, `agent.md` |
| A flow | `examples/sign-release/flows/sign_release.yaml` | one YAML file |
| A flow with its prompts | `examples/file-review/flows/review_file/` | a directory of its own name, holding the flow and `prompts/` |

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
- **A namespace is a directory, not a field.** Put the component in `tools/deploy/notify/`
  and a flow names it `deploy/notify`. `spec.json` still says `"name": "notify"`: the namespace is
  where the directory sits, which the spec has no way of knowing.
- **A flow names the graph, nothing else.** Model, effort, tools and output shape belong to
  the agent, in `agents/<name>/spec.json`.
- **An agent's `tools` are the engine's tools.** They reach the turn over MCP, served by
  `atf mcp-serve`, and run through the same `invoke()` a tool step uses. A tool spec is
  already an MCP tool definition, so nothing in it is written twice. Naming a runtime's own
  built-in tools instead would tie one agent spec to one adapter.
- **A granted namespaced tool loses its slash on the way to the model.** A client builds
  `mcp__atf__<tool>` out of the name and a slash is not legal in one, so `arctic/read_file`
  is offered as `arctic__read_file`. Granting two names that flatten onto one is refused by
  `lint`, since the model would see a single tool where there are two.
- **A model asking for several tools at once gets them at once.** `mcp-serve` runs calls
  on a pool, so a turn takes the longest rather than the sum. Replies come back as calls
  finish, which is why the test helpers key them by request id and never by position.
- **Withdrawing a call really stops it.** A client that sends `notifications/cancelled`
  gets no reply, and the tool's process tree is signalled rather than left running. A
  tool that writes should expect to be interrupted: `write_file` truncates in place, so
  a stopped write can leave a partial file, which is why TERM comes before KILL.
- **Granting a tool that writes needs `unattended: true`.** Nothing approves a call an agent
  makes for itself, so a grant that can change the workspace is declared where the grant is.
- **A granted tool gets no secrets.** Granting one that declares `secrets` is refused, and
  so is a step that declares `secrets` and runs a tool-granted agent. Scoping a grant to a
  single in-turn call is the follow-up that would lift that; until then, secrets belong to
  tool steps.
- **Do not append a trailing newline to a single-value tool output.** A digest or an id gets
  templated mid-line, and a stray newline breaks the line it lands in.

## Adding a pack

A pack is a set of first-party components that ships with the engine and stays switched
off until `~/.arctic/config.yaml` names it:

```yaml
packs:
  - git
```

Adding one is a directory under `src/builtin/packs/`, with a `pack.json` beside the same
`tools/`, `agents/`, `flows/` every root has. Copy the nearest of the three that ship:
`packs/git` for a pack that runs a local command, `packs/github` for one that calls an API
with a credential.

```txt
src/builtin/packs/git/
  pack.json                  description, and what it needs on PATH
  README.md                  what is in it, and what is deliberately not
  lib/git.sh                 shared by the tools, outside tools/ on purpose
  tools/arctic/git/log/      spec.json, tool.md, run.sh
```

Nothing has to be registered. `available_packs()` reads the directory, `pack.json` is the
marker the way `spec.json` marks a component, and `Paths.roots` splices in whichever the
config named. The packaging carries it for free: `builtin = ["**/*"]` puts it in the
wheel and `collect_data_files("builtin")` puts it in the bundle.

Four things to get right, and each has a test that says so:

- **Name everything under `arctic/`.** That is the point of a pack rather than a source.
  A pack sits inside `builtin_root()`, so the engine's namespace is one it may define, and
  `tool: arctic/git/commit` in a flow means the tool that shipped. Naming a pack's tools
  anything else gives away the only thing a pack has that a cloned repository does not.
- **Say what is deliberately absent.** The git pack has no `push`, no `reset`, no
  `--force` and no `add -A`, and its README says so. What a first-party tool refuses to do
  is half of what makes it worth shipping, and it is invisible unless it is written down.
- **Split read from write.** `permissions.filesystem` is one value per tool, so a tool that
  both listed branches and switched them could only ever be granted as one that writes.
  That is why `git/branch` and `git/checkout` are two tools rather than one with a flag.
- **Share a helper if the tools share a check.** `lib/git.sh` holds the containment rule
  for all eight, because eight copies of a security check is seven places to forget it. It
  goes *outside* `tools/`, since the resolver walks that directory for `spec.json` and
  anything else in there reads as an empty namespace. The sourcing line needs
  `# shellcheck source-path=SCRIPTDIR`, and the gate runs `shellcheck -x`.

A pack that reaches the network owes three more, and `packs/github` is where each is
worked out:

- **Declare `secrets` and read the credential from the environment.** That is what makes
  the token come out of the vault and reach exactly the step that asked for it. It also
  means the engine refuses to grant the tool to an agent, which is the right answer:
  nothing scopes a credential to one in-turn call.
- **Keep the credential out of `argv`.** `curl -H "Authorization: ..."` shows it to `ps`
  for the length of the request. `lib/api.sh` writes a config file with mode 600 instead.
- **Answer in JSON, with the same field names as its sibling.** The engine parses a tool's
  stdout and offers it as `.json`, so a JSON answer is switchable where prose is not. And
  a normalised vocabulary is what lets a flow swap `arctic/github/pr/status` for
  `arctic/bitbucket/pr/status` and change nothing else. Where a forge cannot answer a
  field, return `null`; do not invent one and do not drop the key.

Adding a pack means adding to `tests/integration/`. The mechanism is covered once, in
`tests/unit/paths/test_packs.py` and `tests/integration/test_packs.py`; what a new pack
owes is its own tools, run through the CLI against the real thing. For a network pack that
means a real loopback server (`tests/support/forge.py`), routed by method and path exactly
as the tools request them, so a wrong verb or a dropped filter fails rather than passing
against a double that was happy with anything.

## Checks

A check is a tool step with a `switch`, and there is nothing else to it. The engine has no
`gate` key and never had a notion of a check: a `switch` is a step's own result choosing a
branch, and a tool step has one as readily as an agent step does.

```yaml
- id: draft
  agent: brief_writer
  prompt_file: draft
  push: [check]

- id: check
  tool: word_limit
  input:
    text: "{{ steps.draft.text }}"
    max_words: 60
  switch: "{{ this.json.verdict }}"    # `this` is the result being switched on
  max_loops: 3
  cases:
    approved: []                       # ends the flow
    rejected: [draft]                  # already upstream, so this is a loop
```

**A check exits 0 whether it approves or rejects.** Answering "no" is the tool doing its
job, so the verdict goes on stdout where a flow can read it, and a non-zero exit stays what
it is everywhere else in the engine: this tool could not answer at all, and the step fails.
That is the convention every shipped tool already follows, `arctic/grep` included: "the
search ran; it matched something or it did not".

Answer in JSON. `run_step` parses a tool's stdout into `.json`, so `.verdict` is what the
switch matches and `.reason` is what the next pass is told, and neither has to be picked out
of prose. Both halves are needed in different places, which is what a single line cannot do.

What the check said is in `steps` like anything else, so the writer's prompt reads it under
a guard, because on the first pass the check has not run:

```
{% if steps.check %}
Your last summary was rejected: {{ steps.check.json.reason }}
{% endif %}
```

Nothing is appended to a prompt behind the flow's back. The check is a step, with a row in
`inspect flow`, a line per pass in the progress output and its own entry in `--trace`.

Reach for a tool wherever the rule is one a tool can hold. It costs a subprocess rather
than a turn, it cannot be talked round by the prompt it is checking, and it is deterministic,
so a flow that fails a check fails it the same way twice. `examples/checked-summary` is this
shape and `examples/draft-review` is the same shape with an agent doing the judging, which
can judge anything and costs a turn to ask.

## Loops

A loop sends the work back through the steps that produced it, which is what a reviewer
declining a draft needs: the reviewer is a step of its own, with its own agent, its own cost
line and its own row in `inspect flow`.

Nothing declares a loop. A `switch` case naming a step that is already upstream **is** one,
and `lint` finds it from the graph:

```yaml
- id: write
  agent: writer
  prompt: |
    Write the section.
    Last review: {{ steps.review.text }}
  push: [review]

- id: review
  agent: reviewer
  prompt: "Review this draft: {{ steps.write.text }}"
  switch: "{{ this.json.verdict }}"
  max_loops: 5
  cases:
    rejected: [write]        # already upstream, so this is the loop
    approved: [publish]
```

Every step from `write` to `review` goes back to waiting and runs again. `max_loops` is how
many times `review` may send the work back, so `write` runs at most six times. Running out
is a failure rather than a quiet exit: a loop that never converged has not done its job.

**A count is per step and over the whole run**, never reset by a loop around it. So a cheap
check nested inside an expensive review, each bounded at three, is six extra passes and not
sixteen: a flow's worst case is something a reader can add up. The price is that an inner
loop which spent its bound early fails on a later outer pass rather than starting fresh.

What the last pass produced stays in `steps`, and that is how `write` reads the review that
sent its work back. On the first pass there is no review yet, so it reads `(not run)`, the
same literal a skipped step resolves to. That holds for `.text` only. There is nothing to
reach into for `{{ steps.review.json.verdict }}` before the step has run, so what a first
pass reads is the prose.

**A loop makes a step its own ancestor, so a step may read itself.** `{{ steps.write.text }}`
inside `write` is the draft it produced last pass, and `(not run)` on the first. Outside a
loop the same reference is refused, because a step is not upstream of itself there, so the
permission arrives with the loop and goes away with it.

Reach for it whenever a pass is meant to improve on the last one rather than replace it.
Without it an agent starts from its inputs every pass, and a draft that fixes what was
flagged breaks something that had already passed, so a strict reviewer and a forgetful
writer produce a loop that runs to its bound and fails. With it each pass is an edit. The
`draft-review` example reads both its own draft and the review of it, and says why.

Six rules, all enforced by `lint`:

- **A loop needs a `switch`.** A `push` always fires, so a step that always sends its result
  back has no way to stop, and could only run to its bound and fail.
- **`max_loops` is required**, and refused where nothing loops. An unbounded cycle is the
  one mistake here that spends money rather than failing.
- **`max_loops` is an integer of 1 or more.** YAML reads `yes` as `True` and a bool is an
  int, so without its own check `max_loops: yes` would pass as a bound of one.
- **Everything the loop reaches is on it or after it.** A step the loop head reaches that
  does not lead back would run on the first pass and then sit finished while the rest went
  round again.
- **Two loops may share steps only where one contains the other.** Nesting is defined: the
  inner body goes round again in full whenever the outer one fires, so no pass leaves a step
  holding a result from a pass that is over. Two bodies that *cross* are refused, because a
  step one loop re-runs and the other does not belongs to neither pass.
- **A cycle nothing enters is still refused.** No walk from `start` reaches it, so nothing
  opens it: it is a ring of steps that can never run.

The last rule but one is also what keeps a pass safe to start. A loop's body is bounded by
what leads back to the step that closes it, so every member has finished by the time the
work goes back, and none is still running when its state returns to waiting.

## Prompts and templates

A template is `{{ dotted.path }}` over five namespaces, plus `{% if %}` to leave a section
out. `render` and `parse_template` in `engine/executor.py` are the whole of it, and both are
deliberately small: the language is not meant to grow into a second way of expressing the
graph.

### Conditionals

A skipped branch and a loop's first pass are the same problem. A template reads a step that
has no result, and the only answer used to be the literal `(not run)` plus a sentence in the
system prompt explaining it. A guard leaves the section out instead:

```yaml
prompt: |
  Summary:
  {{ steps.summarize.text }}

  {% if steps.risk_scan %}
  Risk findings:
  {{ steps.risk_scan.text }}
  {% else %}
  No risk review was run.
  {% endif %}
```

Four tags: `{% if path %}`, `{% if not path %}`, `{% else %}` and `{% endif %}`. They nest,
and `{% else %}` is optional.

**A step that did not run is false.** Its result is a mapping, so emptiness alone would call
it true, and `truthy` checks the `skipped` marker before anything else. That one rule is what
makes `{% if steps.risk_scan %}` the whole test. Everything else is JSON's emptiness: null,
false, `0`, `""`, `[]` and `{}`. A string is never parsed, so the *text* `"false"` is true.

**The branch that is not taken is never rendered.** This is the part worth keeping. A
reference like `{{ steps.risk_scan.json.severity }}` is legal before the step has run but
has nothing to reach into, so it fails on the path. Inside a guard it is never reached, which
is what lets a prompt read a field of a step that may not have run.

**Both branches are still validated.** `template_refs` walks the parsed template and returns
the references from the condition and from both sides, so `check_refs` sees all of them. A
guard is not a way to read a step that is not upstream, and not a way to put a secret in a
prompt.

**A tag alone on its line takes the line with it**, indentation and newline included, or
every conditional would leave a blank line in the prompt it was added to tidy up. A tag with
anything else on its line stays where it is.

**A malformed tag is refused, and `lint` is where.** `template_refs` parses too, so an
unclosed `{% if %}`, a second `{% else %}` and an unknown tag all fail validation rather
than waiting for the step to run. That is the opposite of the rule for `{{ }}`, whose
pattern is narrow enough that `{{ a-b }}` passes through as prose: `{{ a-b }}` is plausible
English and `{% ... %}` is not, so the same leniency would only hide a typo. Half a tag is
refused for the same reason, since `{ % if x %}` would otherwise render its body
unconditionally.

`{%` and `%}` are therefore reserved in any template. A flow written before this that has one
in a prompt is refused by `lint`, which is the only backward-incompatible part of it.

### Prompt files

A prompt is the long part of a flow and inlining it buries the graph. `prompt_file` names a
file instead:

```yaml
- id: report
  agent: reporter
  prompt_file: report      # reads prompts/report.md beside the flow
```

**The file is read from `prompts/` beside the flow file**, so where the flow lives decides
where its prompts live. That is one rule with two useful results: a *bundle*
(`flows/review/review.yaml`) has prompts of its own, and a flat `flows/review.yaml` shares
`flows/prompts/` with its siblings.

**A bundle is a directory holding a flow of its own name.** `flows/review/review.yaml` is the
flow `review`; the directory is not part of the name. Inside a namespace the file carries the
leaf, the way a `spec.json` does, so `release/sign` is `flows/release/sign/sign.yaml`. A
bundle is *also* still a namespace, so `flows/review/helper.yaml` remains `review/helper` and
nothing that resolved before stops resolving. Written both ways at once, the flat spelling
wins and `find_all` reports the other as shadowing, which is what two suffixes in one
directory already do.

**`inline_prompts` resolves it in `load_flow`**, not in `run_agent`. By the time anything
looks at a step there is one kind of prompt, so `validate`, `template_refs`, `inspect` and
the engine are unchanged, and a missing file fails `lint` rather than a paid-for step. What
it costs is that a template error names the step rather than the file.

**The name cannot leave the directory.** Same rule as a component name and for the same
reason: joining `../../etc/passwd` on resolves, and a flow can arrive by clone. Naming both
`prompt` and `prompt_file` on one step is refused, because one of them would be the prompt
and the other dead text in the repository.

Only a step's prompt can be a file today. The flow's `output.template` has the same
readability problem and would work the same way; nobody has asked yet.

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
