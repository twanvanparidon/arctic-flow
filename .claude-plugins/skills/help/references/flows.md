<!-- Generated from docs/flows.md by packaging/sync_docs.py. Edit that file. -->

# Writing flows

The YAML, front to back: the keys, then templates, branching, checks and loops, prompts,
inputs and output, and secrets. Read the first two sections and you can write a flow.

```sh
atf create flow hello       # scaffold first: what it writes already lints and runs
atf lint hello
atf run hello --input path=flows/hello.yaml
```

A flow is one YAML file under `flows/<name>.yaml`. Every step names where its result goes.
Nothing declares what it waits for.

```yaml
flow: review_file
start: read_target

inputs:
  path: { type: string, required: true }

steps:
  - id: read_target
    tool: arctic/read_file
    input:
      path: "{{ inputs.path }}"
    push: [summarize, triage]     # both run concurrently

  - id: summarize
    agent: summarizer
    prompt_file: summarize
    push: [report]

  - id: triage
    agent: triager
    prompt_file: triage
    switch: "{{ this.json.verdict }}"
    cases:
      risky: [risk_scan]
      clean: [report]

  - id: report
    agent: reporter
    prompt_file: report           # runs once every inbound edge arrived or was skipped

output:
  template: |
    {{ steps.report.text }}
```

That is `examples/file-review`. Read the graph back with `atf inspect flow review_file -o md`.

## Keys

| Flow key | Required | Is |
| --- | --- | --- |
| `flow` | yes | the flow's name |
| `start` | yes | the id of the one entry step |
| `steps` | yes | a non-empty list |
| `inputs` | no | what the caller supplies |
| `output` | no | a mapping with a `template` key. Left out, the run prints nothing |
| `vault` | no | path to an encrypted secrets file, relative to the working directory |
| `description` | no | one sentence. `atf inspect flow -o md` prints it as the heading |
| `version` | no | nothing reads it. The scaffold writes `1` as a convention for your own use |

| Step key | Required | Is |
| --- | --- | --- |
| `id` | yes | unique in the flow |
| `tool` or `agent` | exactly one | the component this step runs |
| `input` | tool steps | a mapping, checked against the tool's `input_schema` |
| `prompt` or `prompt_file` | agent steps | one of them, never both |
| `push` or `switch` | at most one | where the result goes |
| `cases` | with `switch` | a mapping of string key to a list of step ids |
| `default` | no | where an unmatched value goes |
| `max_loops` | on a step that loops | an integer of 1 or more |
| `secrets` | no | names this step may read |

There is no `timeout_seconds` on a step: a tool's is `run.timeout_seconds` in its
`spec.json`, an agent's is `timeout_seconds` in its own. An unknown key is ignored rather
than refused, so `atf inspect flow` is how you check what was read.

## Templates

`{{ dotted.path }}` over four namespaces. An unresolvable path is an **error, never an empty
string**.

| Namespace | Is | Legal in |
| --- | --- | --- |
| `inputs.<name>` | what the caller supplied | anywhere |
| `steps.<id>.…` | another step's result | anywhere, if that step is upstream |
| `secrets.<name>` | a value from the vault | a tool step's `input` only, never an agent's |
| `this.…` | the step's own result | that step's `switch` only |

```
{{ steps.read_target.text }}      the result as text
{{ steps.triage.json.verdict }}   a field out of it, when the result was JSON
```

A tool's stdout is parsed into `.json` when it is JSON; an agent's answer when the agent
declares an `output_schema`. A step may only read a step it transitively depends on: reading
sideways is refused, because a concurrent step may not have run.

A step that was skipped, or that has not run yet on a loop's first pass, resolves as the
literal `(not run)`. That is `.text` only.

### Conditionals

Four tags. They nest, `{% else %}` is optional, and a tag alone on its line takes the line
with it.

```
{% if steps.risk_scan %}
Risk findings:
{{ steps.risk_scan.text }}
{% else %}
No risk review was run: triage judged the file clean.
{% endif %}
```

**A step that did not run is false.** Everything else is JSON's emptiness: `null`, `false`,
`0`, `""`, `[]`, `{}`. The string `"false"` is true.

The branch not taken is never rendered, so `{{ steps.risk_scan.json.severity }}` is safe
inside a guard where it would fail outside one. Both branches are still validated, so a guard
is not a way past a rule.

Any `{% ... %}` must be one of the four tags. `{%` and `%}` are reserved.

## Branching

`push: [a, b]` hands the result on unconditionally, and both run concurrently. A step named
by two places runs once, when every inbound edge has delivered or been skipped. That is a
join, and nothing declares it. `push: []` ends the path.

`switch` is a template over `this` and picks exactly one branch. The rendered value is matched
against the case keys as a string; `default:` catches the rest, and **without one an unmatched
value fails the run**.

Quote a case key YAML would read as a boolean. Bare `yes`, `no`, `on`, `off`, `true` and
`false` become booleans, which never match, and a non-string key is refused.

Give a branching agent an `output_schema` so the field is a fixed set. Switching on prose is
how a run dies on a value nobody expected.

**The untaken branch is skipped, and skipping propagates**: a step whose every inbound edge is
skipped is skipped too. That is what lets a join downstream of a branch run on both paths
instead of waiting forever.

## Checks and loops

A `switch` case naming a step that is already upstream sends the work back. That is the only
cycle a flow may have. Nothing declares a loop; `lint` finds it from the graph.

A **check** is a tool step with a switch, and there is nothing else to it. There is no `gate`
key.

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
  switch: "{{ this.json.verdict }}"
  max_loops: 3
  cases:
    approved: []            # ends the flow
    rejected: [draft]       # already upstream, so this is a loop
```

**A check exits 0 whether it approves or rejects**, and answers in JSON on stdout. Saying "no"
is the tool doing its job. A non-zero exit means it could not answer, and fails the step.

Every step from the head to the closing step runs again. `max_loops` is how many times that
step may send work back, so `draft` runs at most four times here. **Running out is a failure.**
The count is per step over the whole run, so a loop around it never resets it.

What the last pass produced stays in `steps`. Hand the next one both its own previous answer
and the review of it, under a guard, or each pass starts over and breaks what already passed:

```
{% if steps.check %}
Rejected: {{ steps.check.json.reason }}
It said: {{ steps.draft.text }}
{% endif %}
```

A loop makes a step its own ancestor, so inside one a step may read itself.

Six rules, all enforced by `lint`:

- A loop needs a `switch`. A `push` always fires, so it could never leave.
- `max_loops` goes on the step that closes the loop, and is refused anywhere else.
- It is an integer of 1 or more.
- Everything the head reaches is on the loop or after it.
- Two loops may share steps only where one contains the other.
- A cycle nothing enters is refused.

Prefer a tool wherever a tool can hold the rule: it costs a subprocess rather than a turn, and
the prompt cannot argue it round. Read `examples/checked-summary` against `examples/draft-review`.

## Prompt files

```yaml
- id: report
  agent: reporter
  prompt_file: report      # reads prompts/report.md beside the flow
```

The name carries no path and no suffix, and cannot leave the directory. A **bundle**
(`flows/review/review.yaml`) has `prompts/` of its own; a flat flow shares `flows/prompts/`
with its siblings. A missing file fails `lint`, not a paid-for step, and the cost of that is
that a template error names the step rather than the file.

## Inputs and output

```yaml
inputs:
  path:
    type: string
    required: true
    description: File to read, relative to the workspace root.
```

An input arrives from `--input path=…` or `$ATF_VAR_PATH`, and `--input` wins. `type:` is
documentation: nothing coerces it, and a value is a string from either source. Only declared
names are read, so an `ATF_VAR_` exported for another flow is ignored, while an undeclared
input or a missing required one is refused before anything runs.

`output.template` is rendered after the last step, over `inputs` and `steps` only. Left out,
the run prints nothing, which is right for a flow that exists for its effect.

**stdout carries the flow's output and nothing else.** Progress, warnings and traces go to
stderr, so `atf run f > out.md` gives the result byte for byte.

## Secrets

```yaml
vault: secrets.vault

steps:
  - id: sign
    tool: hmac_sign
    secrets: [signing_key]
    input:
      payload: "{{ steps.read_artifact.text }}"
```

The engine passes that step those names and no others, as environment variables. A step earlier
in the flow cannot read them at all, and `inspect flow -o md` prints a secrets column so you
can check that rather than take it.

`{{ secrets.NAME }}` works in a tool step's `input`, for a name that step declared. In an
agent prompt it is refused: it would be sent to the model and stay in the session.

Values are scrubbed from errors and traces but **not from step results**, since a result is
data the flow asked for. Never template a secret into something you would not print.

A granted tool gets no secrets, in both directions. See
[components](components.md#granting-an-agent-tools).

The vault is opened lazily, so a flow with no secrets never prompts. See
[the CLI](cli.md#vault).

## When it will not lint or run

[The reference](reference.md) maps every refusal to its cause and its fix. Run `atf lint`
before you read the YAML looking for the fault.

Reproduce it for free before spending money on it. Setting `"adapter": "echo"` runs the whole
graph offline, and a prompt can then drive a chosen branch: see
[running for free](components.md#cost-and-running-for-free).
