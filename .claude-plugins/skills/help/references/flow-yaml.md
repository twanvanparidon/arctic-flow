# Flow YAML

One file, one flow, under `flows/<name>.yaml`. A name may carry a namespace, so
`flows/release/sign.yaml` is run as `release/sign`.

## Flow keys

| Key | Required | Is |
| --- | --- | --- |
| `flow` | yes | the flow's name |
| `start` | yes | the id of the one entry step |
| `steps` | yes | a non-empty list |
| `version` | no | an integer |
| `description` | no | one sentence |
| `inputs` | no | what the caller supplies |
| `vault` | no | path to an encrypted secrets file, relative to the working directory |
| `output` | no | a mapping with a `template` key |

`output` must be a mapping. `output: "{{ steps.x.text }}"` is the natural typo and is
refused by name, because it used to surface as a traceback.

```yaml
output:
  template: |
    {{ steps.report.text }}
```

## Inputs

```yaml
inputs:
  path:
    type: string
    required: true
    description: File to read, relative to the workspace root.
```

An input arrives from `--input path=...` or from `$ATF_VAR_PATH`. `--input` wins where both
are set. The prefix is `ATF_VAR_` and not a bare `ATF_`, because `$ATF_PATH` and
`$ATF_VAULT_PASSWORD` are the engine's own.

Only names the flow declares are read, so an `ATF_VAR_` exported for another flow is
ignored rather than refused. An input the flow never declared, or a required one left out,
is refused before anything runs.

`type:` is documentation. Nothing coerces or checks it, and a value is a string from either
source.

## Step keys

Every step:

| Key | Required | Is |
| --- | --- | --- |
| `id` | yes | unique in the flow |
| `tool` or `agent` | exactly one | the component this step runs |
| `push` or `switch` | at most one | where the result goes |
| `secrets` | no | a list of names this step may read |

A tool step adds `input:`. An agent step adds `prompt:` (required) and may add `gate:`. A
switching step adds `cases:`, and may add `default:` and `max_loops:`.

```yaml
- id: read_target
  tool: common/read_file
  input:
    path: "{{ inputs.path }}"
    max_lines: 400
  push: [summarize, triage]
```

```yaml
- id: summarize
  agent: summarizer
  prompt: |
    Summarise this file.
    ---
    {{ steps.read_target.text }}
  push: [report]
```

### push

`push: [a, b]` hands the result on unconditionally. Both run concurrently. A step named by
two places runs once every inbound edge is delivered or skipped.

`push: []` ends that path. A step with no outbound edge ends it too.

Refused: pushing to an unknown step, pushing to itself, setting both `push` and `switch`.
Every step except `start` needs something pushing to it, or it is unreachable.

### switch and cases

```yaml
- id: triage
  agent: triager
  prompt: "..."
  switch: "{{ this.json.verdict }}"
  cases:
    risky: [risk_scan]
    clean: []
  default: [report]
```

`switch` is a template over `this`, the step's own result. The rendered value is matched
against the case keys as a string.

A value matching no case and no `default` fails the run. Give the agent an `output_schema`
so the field is a fixed set rather than prose.

Quote a key YAML would read as a boolean. YAML 1.1 reads bare `yes`, `no`, `on`, `off`,
`true` and `false` as booleans, which never match a rendered string. The engine refuses a
non-string key rather than falling through to `default`.

Refused: a `switch` with no `cases`, `cases` or `default` with no `switch`, a case whose
value is not a list of step ids.

### gate

An agent step may name a tool that has to accept its result before the result goes
anywhere.

```yaml
- id: draft
  agent: brief_writer
  prompt: |
    Summarise this file in at most 60 words.
    ...
  gate:
    tool: word_limit
    input:
      text: "{{ this.text }}"
      max_words: 60
    max_attempts: 3
    feedback: |
      Your last answer was rejected by word_limit:

      {{ gate.text }}

      Write it again, inside the limit.
```

Exit 0 accepts. Any other exit rejects, and the tool's output becomes `{{ gate.text }}` in
the next prompt, appended to the original one. Every turn is a fresh session, so the retry
carries its own history or it has none.

When the attempts run out the step fails carrying what the gate last said. Nothing
downstream ever sees a result the gate refused. The loop is inside the step, so there is no
edge and `inspect flow` reports the gate rather than drawing one.

| Rule | Why |
| --- | --- |
| Gates are for agent steps | a tool given the same input returns the same result |
| `feedback` is required | a retry that says nothing about what was wrong is the first attempt again |
| `max_attempts` is 2 or more, 3 by default | one attempt leaves no turn to act on the feedback |
| `{{ secrets.NAME }}` is allowed in `input`, refused in `feedback` | `input` is a tool's, `feedback` becomes a prompt |

Any tool is a gate, with no second contract to write. The cost is that a broken gate
reports its own error the way a rejection arrives, and spends the attempts first.

### Loops

Nothing declares a loop. A `switch` case naming a step that is already upstream **is** one,
found from the graph.

```yaml
- id: write
  agent: writer
  prompt: |
    Write the section.
    Your previous draft: {{ steps.write.text }}
    The review of it: {{ steps.review.text }}
  push: [review]

- id: review
  agent: reviewer
  prompt: "Review this draft: {{ steps.write.text }}"
  switch: "{{ this.json.verdict }}"
  max_loops: 3
  cases:
    approved: []
    rejected: [write]          # already upstream, so this is the loop
```

Every step from the head to the step that closed the cycle goes back to waiting and runs
again. `max_loops` counts how many times that step may send the work back, so `write` runs
at most four times here. Running out fails the step.

What the last pass produced stays in `steps`. That is how `write` reads the review. On the
first pass there is no review, so it reads `(not run)`, the same literal a skipped step
gives. That holds for `.text` only: there is nothing to reach into for
`{{ steps.review.json.verdict }}` before the step has run.

A loop makes a step its own ancestor, so a step may read itself. `{{ steps.write.text }}`
inside `write` is last pass's draft. Outside a loop the same reference is refused.

Six rules, all enforced by `lint`:

- A loop needs a `switch`. A `push` always fires, so it could only run to its bound.
- `max_loops` is required on the step that closes the cycle, and refused where nothing
  loops.
- `max_loops` is an integer of 1 or more. A bool is refused in its own right, because YAML
  reads `yes` as `True` and a bool is an int.
- Everything the loop head reaches must lead back to the closing step, or come after the
  loop. A stranded step would run on the first pass and then sit finished.
- No nested or overlapping loops. Which one's count a pass resets would be undefined.
- A cycle nothing enters is refused: nothing opens it, so it can never run.

Declaration order decides which edge closes a cycle, because the back-edge walk starts at
`start` and follows the file.

### secrets

```yaml
- id: sign
  tool: hmac_sign
  secrets: [signing_key]
  input:
    payload: "{{ steps.read_artifact.text }}"
```

The engine passes that step only those names, as environment variables. A step earlier in
the flow cannot read them at all.

`{{ secrets.NAME }}` works in a tool's `input` for a name that step declared. In an agent
prompt it is refused: it would be sent to the model and stay in the session. Credentials
reach an adapter through the environment instead.

Values are scrubbed from errors and traces, but **not** from step results. A result is data
the flow asked for, and scrubbing it would corrupt the workflow rather than protect it.

A step that declares `secrets` may not run an agent that is granted tools, and a tool that
declares `secrets` may not be granted to an agent. Nothing scopes a secret to one in-turn
call yet.

Refused: a `secrets` that is not a list of names, and a name listed twice.

## Gate or loop

| | Gate | Loop |
| --- | --- | --- |
| Checks | one answer against a fixed rule | the work, with judgement |
| Is | a tool, retried inside the step | a step of its own, with its own agent |
| Costs | the step's attempts | a cost line per pass, per step in the body |
| Drawn by `inspect flow` | reported, not drawn | a real edge |
| Bound by | `max_attempts` | `max_loops` |
| Next attempt sees | the original prompt plus `feedback` | everything in `steps` from the last pass |

Read `examples/gated-summary` against `examples/draft-review` in the arctic-flow
repository. Both are commented at length and say when to prefer which.
