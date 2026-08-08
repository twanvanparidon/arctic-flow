---
name: atf-create
description: Create Arctic Flow components for the `atf` engine, meaning flows, agents and tools. Use this whenever the user wants to build, add or scaffold an Arctic Flow flow, agent or tool, turn a task or a script into an `atf` workflow, wire steps together with push, switch, cases, a gate or a loop, grant an agent a tool, or mentions `atf create`, `flows/*.yaml`, `agents/*/agent.md`, `tools/*/run.sh` or a `spec.json`. Use it for small asks too, like "add a step to this flow", "make this agent loop until it passes", or "let the agent read files for itself".
---

# Creating Arctic Flow components

A flow is a graph of steps in one YAML file. Each step declares **where it pushes its
result next**, so a flow reads forwards and nothing declares what it waits for. The engine
derives the reverse edges and runs whatever is ready.

A flow names the graph and nothing else. Which model, which effort, which prompt and which
output shape belong to the agent, in its own directory.

## 1. Find the engine

Run this before anything else, and use whichever answers for every command below:

```sh
atf --version || python3 src/main.py --version
```

`atf` is the installed binary. `python3 src/main.py` is an arctic-flow checkout, where the
two are the same program. If neither answers, the user has not installed it: point them at
`https://github.com/twanvanparidon/arctic-flow` and stop.

A project is any directory with `flows/` in it. There is nothing to initialise. `atf` acts
on the current directory unless `--workspace DIR` is given, and that flag goes **before**
the subcommand:

```sh
atf --workspace path/to/project lint
```

## 2. Scaffold first, always

Never hand-write a `spec.json`, an `agent.md` or a `run.sh`. `atf create` writes the
scaffold that ships with the engine, which is runnable as it is and carries the fields
`engine/specs.py` requires today. A hand-written spec goes stale against a schema you
cannot see, and fails at lint time with a message about a key nobody typed.

```sh
atf create flow review          # flows/review.yaml
atf create agent reviewer       # agents/reviewer/: spec.json, agent.md
atf create tool git/commit      # tools/git/commit/: spec.json, tool.md, run.sh
```

It writes into `./.arctic` when the project keeps that directory, and the project root
otherwise: the top of the lookup, so what is created is what then resolves. Nothing is
overwritten; a name that already exists is an error rather than a clobber.

A name may carry a namespace at any depth, for all three kinds. `git/commit` is
`tools/git/commit/`. A flow takes no `.yaml` on the end.

Then **edit what it wrote**. The scaffold's comments explain each key; read them before
deleting them, and delete them once the file says what it does on its own.

## 3. Pick the kind

| The user wants | Create | Because |
| --- | --- | --- |
| A workflow, a pipeline, "run these in order" | a **flow** | it is the graph |
| Judgement, prose, a decision from context | an **agent** | it calls a model |
| Anything deterministic: read, write, search, sign, call an API | a **tool** | same input, same result, no cost |

Reach for a tool before an agent every time. A tool step is free, repeatable and cannot
misread its instructions. Ask a model only for the part that genuinely needs one.

Check what already exists before creating anything:

```sh
atf list                        # every name that resolves, and what shadows what
atf inspect tool common/read_file
atf inspect agent <name>
```

Five tools ship under `common/`: `read_file`, `write_file`, `glob`, `grep`, `fetch_url`.
The first four cannot reach outside the workspace root. Use them rather than writing a
fifth way to read a file.

## 4. Write the flow

Fill in the scaffold in this order: `inputs`, `start`, `steps`, `output`.

```yaml
flow: review                    # matches the file name
version: 1
description: One sentence.

inputs:
  path:
    type: string
    required: true
    description: File to read, relative to the workspace root.

start: read_target              # exactly one entry

steps:
  - id: read_target
    tool: common/read_file
    input:
      path: "{{ inputs.path }}"
      max_lines: 400
    push: [summarize, triage]   # both run concurrently

  - id: summarize
    agent: summarizer
    prompt: |
      Summarise this file.
      ---
      {{ steps.read_target.text }}
    push: [report]

output:
  template: |
    {{ steps.report.text }}
```

**Templates** are `{{ dotted.path }}` over five namespaces: `inputs`, `steps`, `secrets`,
`this` and `gate`. A step reads `{{ steps.x.text }}` only when `x` is transitively
upstream of it. An unresolvable path is an error, never an empty string. Details are in
`../atf-help/references/templates.md`.

### Choosing the shape

Work down this list. The first one that fits is the answer.

- **Two things from one result** → `push: [a, b]`. They run concurrently. A step named by
  two places runs once both have arrived, and needs nothing declared to say so.
- **One of several paths** → `switch` on the step's own result, with `cases`. Give the
  agent an `output_schema` in its spec and switch on `{{ this.json.verdict }}`, so the
  branch is decided by a field rather than by prose. Add `default:` for anything else, or
  the run fails on a value no case matches.
- **One answer that must satisfy a fixed rule** → a **gate**. A tool has to exit 0 on the
  agent's result before it goes anywhere. A rejection is not a failure: the tool's output
  is appended to the prompt through the step's own `feedback` and the agent answers again,
  up to `max_attempts` (3 by default, 2 minimum). The retry is inside the step, so there
  is no edge to draw.
- **A pass that should improve on the last one** → a **loop**. A `switch` case naming a
  step that is already upstream *is* one, and `max_loops` on that step is then required.
  The body goes back to waiting and runs again. The last pass stays in `steps`, so the
  writer reads the review that sent its work back, and reads `(not run)` on the first pass.

Gate or loop: a gate checks one answer against a fixed rule with no new step. A loop puts
the reviewer in the graph, with its own agent, its own cost line and its own row in
`inspect flow`. Read `examples/gated-summary` against `examples/draft-review`.

### Secrets

A step declares what it may read, and the engine passes that step only those, as
environment variables:

```yaml
- id: sign
  tool: hmac_sign
  secrets: [signing_key]
  input:
    payload: "{{ steps.read_artifact.text }}"
```

`{{ secrets.NAME }}` works in a tool's `input` for a name that step declared. In an agent
prompt it is refused outright: it would be sent to the model and stay in the session.
Secrets are scrubbed from errors and traces but **not** from step results, so never
template one into something you would not print.

```sh
atf vault create secrets.vault < secrets.yaml
atf vault set secrets.vault signing_key       # value from stdin or a prompt, never a flag
```

## 5. Write the agent

`agent.md` **is** the system prompt, read verbatim. Write the job and the shape of the
answer: what to lead with, what to leave out, how long. "Report the risks you can see, one
line each" beats "you are a security expert".

Nothing in it is about one flow. A prompt naming a step or an input cannot be reused, and
the flow already carries both.

`spec.json` carries everything else:

```json
{
  "name": "reviewer",
  "kind": "agent",
  "version": 1,
  "description": "One sentence a flow author reads to decide whether to name it.",
  "system_prompt": "agent.md",
  "adapter": "claude_code",
  "model": "sonnet",
  "effort": "medium",
  "output_schema": { "type": "object", "properties": { "verdict": { "enum": ["approved", "rejected"] } }, "required": ["verdict"] },
  "tools": [],
  "unattended": false
}
```

`name` is the leaf only: the namespace is which directory it sits in. `model` is required
by the `claude_code` adapter, because the CLI's configured default is a per-machine
dependency. `effort` is one of `low`, `medium`, `high`, `xhigh`, `max`.

**While drafting, set `"adapter": "echo"`.** It answers from the request, so the whole
graph, every branch, every gate and every template run offline and for free. The prompt can
carry `!fail` to see what a refusal does downstream, or `!json {"verdict":"rejected"}` to
send a switch down the branch you want to look at. Switch back to `claude_code` when the
graph is right.

### Granting an agent tools

An agent's `tools` are the engine's tools, not the runtime's. They reach the turn over MCP
and run through the same code a tool step uses, so the sandbox, the schema check and the
timeout are unchanged.

```json
"tools": ["common/read_file", "common/write_file"],
"unattended": true
```

Three rules, all refused at lint time:

- Granting a tool whose `permissions.filesystem` is `write` needs `"unattended": true`.
  Nothing approves a call an agent makes for itself.
- A granted tool gets no secrets. Granting one that declares `secrets` is refused, and so
  is a step that declares `secrets` and runs a tool-granted agent.
- Two grants that flatten onto one name are refused. `common/read_file` reaches the model
  as `common__read_file`, because a slash is not legal in a tool name.

Grant tools when the number of files is genuinely not knowable in advance. Wire steps when
it is: three steps put three rows in the trace and the paths are decided by whoever wrote
the YAML.

## 6. Write the tool

`run.sh` reads one JSON object on stdin and writes its result to stdout. Errors go to
stderr as a single line, with an exit code listed in the spec's own `exit_codes`. The
engine turns that code back into your sentence.

The scaffold is a working tool already. Keep its two input checks: they are what make the
script runnable by hand, which is how it is worth debugging.

```sh
echo '{"text":"hello"}' | ./tools/greet/run.sh
```

Four things the spec must get right:

- `permissions` is required, and `filesystem` is one of `none`, `read`, `write`. It is the
  gate on granting the tool to an agent, so `"rw"` or a typo is refused rather than read as
  "not write".
- `input_schema` needs `"additionalProperties": false`, or `lint` cannot catch a flow
  passing a key the tool does not accept.
- `run.command[0]` must exist and be executable. A lost `chmod +x` is the most common way a
  tool fails on someone else's machine.
- Do not print a trailing newline after a single-value result. A digest gets templated
  mid-line, and a stray newline breaks the line it lands in.

Copy the nearest shipped tool rather than starting fresh: `common/read_file` to read,
`common/write_file` to write, `common/grep` to search, `common/fetch_url` to reach the
network.

## 7. Verify before reporting it done

Never say a component is written until `lint` passes. `lint` runs the same checks `run`
does before its first step: the graph, every template reference, and every component spec.

```sh
atf lint <flow>                 # one flow, stops at its first problem
atf lint                        # every flow in the project, all answers at once
atf inspect flow <flow>         # read the graph back
atf inspect flow <flow> -o md   # Mermaid, plus which steps run concurrently
```

Then run it. With `echo` agents this is free:

```sh
atf run <flow> --input path=README.md
```

Progress goes to stderr and the flow's output goes to stdout, so `atf run f > out.md`
gives the result byte for byte. Add `--trace` for a per-step JSON summary.

If `lint` refuses something, the message names the step and says what to do. When it does
not land, use the `atf-help` skill: `../atf-help/references/errors.md` maps each refusal to
its cause.

## What the engine refuses, so write it right the first time

- A step sets exactly one of `tool` or `agent`, and never both `push` and `switch`.
- A step may not push to itself, and every step except `start` needs something pushing to it.
- A template may only read a step transitively upstream of it. Reading sideways is refused,
  because that step may not have run.
- A `switch` needs `cases`; `cases` or `default` without a `switch` is refused.
- Quote a case key that YAML would read as a boolean: `"yes"`, `"no"`, `"on"`, `"off"`.
- A loop needs a `switch` and a `max_loops` of 1 or more. `max_loops` where nothing loops
  is refused too. No nested or overlapping loops.
- A gate is for agent steps only, and needs a `feedback`. `max_attempts` is 2 or more.
- `{{ this.* }}` exists only in a switch or a gate. `{{ gate.* }}` only in gate feedback.
- A secret in an agent prompt is refused. A secret not declared by the step using it is
  refused.
- `output:` is a mapping with a `template` key, not a bare string.
