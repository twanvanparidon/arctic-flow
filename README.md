# Arctic Flow

```
   *  .  *
    \ | /       A R C T I C   F L O W
  .-- * --.     atf: push-based agentic workflows
    / | \
   *  .  *
```

A code-first engine for agentic workflows. A **flow** is a graph of steps: some run a
tool, some run a model. Each step declares **where it hands its result next**, so a flow
reads forwards, in the order it happens.

```yaml
- id: read
  tool: read_file
  input:
    path: "{{ inputs.path }}"
  push: [explain]        # <- where this result goes

- id: explain
  agent: explainer
  prompt: |
    Explain this file.
    ---
    {{ steps.read.text }}
```

That is the whole idea, and everything else follows from it. Nothing declares what it
waits for. The engine derives the reverse edges, runs whatever is ready, and delivers
results onward. Two steps pushed from one place run concurrently. A step named by two
places runs once both have arrived.

**Why it is built this way.** Workflows are code. They live in files you can diff, review
and override, not in a UI. A flow names a graph and nothing else. Which model, which
effort and which prompt belong to the agent, in its own directory, so changing a prompt is
not a change to the workflow. A flow can also be read without running it. `atf diagram`
draws the graph, `atf lint` checks every reference and every component spec, and neither
calls a model.

---

## Install

Pick one. Nothing here needs a running service or a config file.

**A binary, no Python required.** From the repository's **Releases** page, take
`atf-<version>-linux-x86_64.tar.gz` and its `.sha256`:

```sh
sha256sum -c atf-0.1.0-linux-x86_64.tar.gz.sha256
tar xzf atf-0.1.0-linux-x86_64.tar.gz
./atf/atf --version
```

Put it somewhere on your `PATH`. Move the whole `atf/` directory and link the binary
inside it, since it carries its own interpreter next door:

```sh
mv atf ~/.local/lib/atf && ln -s ~/.local/lib/atf/atf ~/.local/bin/atf
```

PyInstaller cannot cross-compile, so this is a Linux x86-64 build. On macOS or Windows,
install from Python.

**From Python** (3.11 or newer). The same release carries a wheel, and a checkout
installs directly. It is not on PyPI:

```sh
pip install ./arctic_flow-0.1.0-py3-none-any.whl
pip install .                # from a checkout
atf --version
```

**From a checkout**, with no install at all:

```sh
python3 src/main.py --version
```

`src/main.py` puts `src/` on the import path and hands over to the same CLI. Every `atf`
below works as `python3 src/main.py`.

### What else you need

| For | You need |
| --- | --- |
| The built-in `read_file` tool | `bash`, `jq`, `awk`, `realpath` |
| Any **agent** step | the `claude` CLI (Claude Code), installed and authenticated |
| Tool-only flows | nothing else: no key, no network |

Check the last two:

```sh
claude --version     # the adapter is verified against 2.1.222
atf list             # what the engine can see, by name
```

---

## Write your first flow

A project is a directory. There is nothing to initialise. The engine finds components by
name, and a `flows/` directory is enough to be found in.

```sh
mkdir -p first-flow/flows && cd first-flow
printf '# 0.4.0\n\n- The engine derives reverse edges, so a step only declares where it pushes.\n' > notes.md
```

### 1. One step

Save this as `flows/explain.yaml`:

```yaml
flow: explain
version: 1
description: Read a file and explain what it does.

inputs:
  path:
    type: string
    required: true
    description: The file to explain, relative to the project.

start: read

steps:
  - id: read
    tool: read_file
    input:
      path: "{{ inputs.path }}"
      max_lines: 200

output:
  template: |
    {{ steps.read.text }}
```

Every key here is load-bearing:

- **`flow`** is the name you run it by. **`start`** is the one step the first push goes to.
- **`inputs`** declares what the flow takes. Undeclared or missing inputs are rejected
  before anything runs, so a typo costs nothing.
- **`steps`** is a list, but the order in the file means nothing. `push` and `start` decide
  what runs when.
- **`tool: read_file`** is a name, not a path. It comes from the engine's built-ins; you
  did not have to install it.
- **`output.template`** is what lands on stdout. `{{ steps.read.text }}` is that step's
  result.

Check it, then run it:

```console
$ atf lint explain
./flows/explain.yaml: ok, 1 step, no issues found

$ atf run explain --input path=notes.md
→ read           tool read_file
✓ read           22ms

  1 step · 22ms

────────────────────── output · explain ───────────────────────
# 0.4.0

- The engine derives reverse edges, so a step only declares where it pushes.
───────────────────────────────────────────────────────────────
```

Progress is on stderr; the framed part is stdout, byte for byte. So
`atf run explain --input path=notes.md > out.md` writes the output alone, and the frame
does not appear at all.

### 2. Add an agent

An agent is a directory: what to run in `spec.json`, and the system prompt as prose in
`agent.md`. Create `agents/explainer/spec.json`:

```json
{
  "name": "explainer",
  "kind": "agent",
  "version": 1,
  "description": "Explains what a file does, for someone seeing it for the first time.",

  "adapter": "claude_code",
  "model": "sonnet",
  "effort": "low"
}
```

And `agents/explainer/agent.md`, which **is** the system prompt, read verbatim:

```markdown
You explain code and configuration to someone reading it for the first time.

Lead with what the thing is for. Then the three or four points that would actually
surprise a reader. Skip anything they can see at a glance.

Plain prose, no headings, under 150 words.
```

Now push the file into it. Add `push: [explain]` to the `read` step, add the new step, and
point the output at it:

```yaml
  - id: read
    tool: read_file
    input:
      path: "{{ inputs.path }}"
      max_lines: 200
    push: [explain]

  - id: explain
    agent: explainer
    prompt: |
      Explain this file.

      Path: {{ inputs.path }}
      ---
      {{ steps.read.text }}

output:
  template: |
    {{ steps.explain.text }}
```

The flow says *what connects to what*. It does not say `sonnet`, or `low`, or carry the
prompt. Those belong to the agent. So you can rewrite the prompt without touching the
workflow, and swap the model for every flow that uses it in one place.

`graph` shows what the engine made of it:

```console
$ atf graph explain
explain: start -> read

  read  (tool:read_file)
    -> explain

  explain  (agent:explainer)
    (terminal)
```

And running it now costs a fraction of a cent:

```console
$ atf run explain --input path=notes.md
→ read           tool read_file
✓ read           23ms
→ explain        agent explainer
✓ explain        7.4s

  2 steps · 7.4s · $0.0069

────────────────────── output · explain ───────────────────────
This is a changelog entry documenting version 0.4.0 of a workflow tool…
───────────────────────────────────────────────────────────────
```

While an agent step is working, a live clock ticks on the last line. The difference
between *working* and *hung* is the only thing worth knowing while you wait.

### 3. Branch, and let the unused path skip

A step can pick one path instead of pushing to all of them. `switch` is evaluated against
the step's own result. `this` is the step that just ran:

```yaml
  - id: triage
    agent: triager
    prompt: |
      Does this file need a full risk review?
      ---
      {{ steps.read_target.text }}
    switch: "{{ this.json.verdict }}"
    cases:
      risky: [risk_scan]
      clean: [report]
```

For `this.json` to hold anything, the agent has to return JSON. Declare an
`output_schema` in its `spec.json` and the adapter enforces it:

```json
  "output_schema": {
    "type": "object",
    "properties": {
      "verdict": { "enum": ["risky", "clean"] },
      "reason":  { "type": "string" }
    },
    "required": ["verdict", "reason"],
    "additionalProperties": false
  }
```

That is `examples/file-review`, whole and working. Read it forwards, the way the engine
does:

```console
$ atf --workspace examples/file-review graph review_file
review_file: start -> read_target

  read_target  (tool:read_file)
    -> summarize
    -> triage

  summarize  (agent:summarizer)
    -> report

  triage  (agent:triager)
    switch {{ this.json.verdict }}
      risky      -> risk_scan
      clean      -> report

  risk_scan  (agent:risk_scanner)
    -> report

  report  (agent:reporter)
    (terminal)
```

`summarize` and `triage` are pushed from one place, so they run at the same time. `report`
is named by three, so it waits.

The interesting part is what happens to the path not taken. When triage says `clean`,
`risk_scan` never runs, and `report`, downstream of it, does **not** wait for it. Skipping
propagates along the untaken edges, so a join is reached as soon as every inbound edge has
either delivered or been skipped. A skipped step still resolves in templates, as the literal
`(not run)`, so a prompt can mention the gap instead of silently omitting it.

```sh
atf --workspace examples/file-review diagram review_file   # the graph, as Mermaid
atf --workspace examples/file-review run review_file --input path=flows/review_file.yaml
```

`diagram` also reports which steps run concurrently and which can be skipped. It works
statically, without calling a model.

### 4. Secrets, when a step needs one

Secrets live in an encrypted file, and a step declares which of them it may read. This is
`examples/sign-release`, which brings its own `hmac_sign` tool:

```yaml
vault: secrets.vault

steps:
  - id: sign
    tool: hmac_sign
    secrets: [signing_key]
```

Making your own takes one command, which reads a YAML mapping from stdin:

```sh
printf 'signing_key: hunter2\n' | atf vault create secrets.vault
```

It prompts for the vault's own password. There is deliberately no `--vault-password` flag,
because it would land in shell history and in the process list. Scripts use
`--vault-password-file`, `$ATF_VAULT_PASSWORD` or `$ATF_VAULT_PASSWORD_FILE` instead.

The engine hands that step **only** the secrets it names, as environment variables. The
step before it cannot read them. Two rules follow, and `lint` enforces both:
`{{ secrets.NAME }}` works in a tool's `input` only if that step declared it, and a secret
in an **agent prompt** is refused outright. It would be sent to the model and stay in the
session. Credentials reach an adapter through the environment instead.

```sh
atf --workspace examples/sign-release run sign_release --input path=release-notes.md
```

Its vault password is `demo`. `atf --workspace examples/sign-release diagram sign_release`
prints a secrets column, so you can check which step holds what without running it.

---

## Where names come from

Nothing is referenced by path. Roots are searched in order and the first match wins:

| | Root | What it is for |
| - | ---- | -------------- |
| 1 | `$ATF_PATH` | colon-separated, for tests and one-off overrides |
| 2 | `./.arctic/` | this project, kept out of the way |
| 3 | `./` | this project, at the top level |
| 4 | `~/.arctic/` | you, across every project |
| 5 | the engine's built-ins | what ships with `atf` |

Under any root, components live in `tools/`, `agents/` and `flows/`. Overriding is per
*name*: a project-level `read_file` replaces the built-in entirely.

```console
$ atf paths
search roots, highest precedence first:

  1. .
     agents, flows
  2. …/site-packages/builtin
     tools

working directory: /home/you/first-flow
components run with the working directory set here, wherever they were found
```

Where a component is *found* never changes where it *runs*: tools execute with the working
directory set to the project root, so a tool installed in your home directory still acts on
the project in front of you. `atf list` shows every name available and flags anything a
higher root is shadowing.

Adapters are the exception. They are Python modules registered in code, not directories on
disk, so there is no `~/.arctic/adapters/`. `src/adapters/__init__.py` says why.

---

## Commands

| | |
| - | - |
| `atf run <flow> --input K=V` | execute it. `--trace` for a per-step JSON summary, `--quiet` for output only |
| `atf lint <flow>` | the graph, every template reference, and every component spec: the same checks `run` makes first |
| `atf graph <flow>` | the push edges as text |
| `atf diagram <flow>` | Mermaid markdown plus how the flow resolves, with no model |
| `atf list` | what is installed, and what is shadowing what |
| `atf paths` | the search roots, in order |
| `atf vault create\|set\|list\|view` | the encrypted secrets file |

Any flow argument may also be a path to a `.yaml` file, so an ad-hoc flow outside the
search roots still runs.

## Limits worth knowing before you build on it

- **Linux x86-64 binaries only.** macOS and Windows need runners on those platforms; from
  Python it is portable.
- **One adapter**, `claude_code`, which shells out to the `claude` CLI. Adding a second is a
  module in `src/adapters/` and no change anywhere else.
- **Agents cannot use tools inside a turn.** An agent declaring `tools` is refused with an
  explanation rather than quietly ignored. Give it the output of a tool step instead. The
  engine's loop stays the only loop.
- **No tests yet.** `tests/` is scaffolded and the pipeline has a step waiting for them.

## More

- `examples/sign-release`: tools and the vault. Deterministic, no key, no network.
- `examples/file-review`: agents, a branch, and a join. Costs a few cents to run.
- [CONTRIBUTING.md](CONTRIBUTING.md): the design, the layout, and how to add a component.

## Licence

Arctic Flow is free software under the GNU General Public License, version 3 or later.
The full text is in [LICENSE](LICENSE).

    Copyright (C) 2026 Twan van Paridon

    This program is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License as published by the Free Software Foundation,
    either version 3 of the License, or (at your option) any later version.

    This program is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with this
    program. If not, see <https://www.gnu.org/licenses/>.

Use it, change it and run it however you like. Distribute a modified version and the
source goes with it, under the same licence.
