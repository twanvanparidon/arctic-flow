# Examples

Five projects that run as they are. A project is a directory with `flows/` in it: there is
nothing to initialise.

Each flow YAML carries a long comment header explaining the shape it demonstrates. Read those
forwards, the way the engine does.

| Example | Is | Costs |
| --- | --- | --- |
| [`sign-release`](sign-release) | tools and secrets | nothing |
| [`file-review`](file-review) | a fan-out, a branch, a skip and a join | a few cents |
| [`checked-summary`](checked-summary) | a check: a tool sends work back | a few cents |
| [`draft-review`](draft-review) | the same shape, judged by an agent | several turns |
| [`agent-tools`](agent-tools) | an agent calling tools inside one turn | a few cents |

The four that call a model need the `claude` CLI installed and authenticated.
`sign-release` needs neither a key nor a network.

## sign-release

Two steps and one key from an encrypted vault. Deterministic, free, and the fastest way to
see that the engine works.

`signing_key` never appears in the flow, never reaches a prompt and never lands in the
output. `read_artifact`, one step earlier, cannot read it at all.

```sh
ATF_VAULT_PASSWORD=demo atf --workspace examples/sign-release \
    run sign_release --input path=release-notes.md
```

The vault is committed on purpose so this runs with nothing to prepare. Its password is
`demo`. It has [a README of its own](sign-release/README.md).

## file-review

Triage picks one path, the other is skipped, and the report waits for neither. A *bundle*, so
its prompts live in `flows/review_file/prompts/`.

The report's prompt guards the risk section with `{% if steps.risk_scan %}`, so the branch
that did not run is never rendered.

```sh
atf --workspace examples/file-review run review_file \
    --input path=flows/review_file/review_file.yaml

atf --workspace examples/file-review inspect flow review_file -o md
```

## checked-summary

A tool answers whether the summary is inside its word budget, and the flow switches on that
answer: `approved` ends the run, `rejected` goes back to the writer with the count.

The check exits 0 either way. Saying "no" is the tool doing its job.

```sh
atf --workspace examples/checked-summary run summarize --input path=incident.md
```

## draft-review

The same shape built out of an agent. A reviewer can judge anything and costs a turn to ask;
a tool costs a subprocess and can only count words.

The writer reads **both** its own previous draft and the review of it, so each pass is an
edit rather than a rewrite. Read this one against `checked-summary` for when to prefer which.

It loops, so it pays for several turns.

```sh
atf --workspace examples/draft-review run draft_review --input path=brief.md
```

## agent-tools

Grants an agent `arctic/read_file` and `arctic/write_file`, so it decides for itself when to
read and when to write. One step where three would also work, and the flow header says when
to prefer which.

`"unattended": true` on the agent spec is what lets a granted tool write.

```sh
atf --workspace examples/agent-tools run annotate \
    --input path=notes/incident.md --input out=out/incident.md
```

## Reading one without running it

Only `run` calls a model.

```sh
atf --workspace examples/file-review lint                       # every flow in the project
atf --workspace examples/file-review inspect flow review_file   # the graph
atf --workspace examples/file-review inspect agent summarizer   # its system prompt
atf inspect tool arctic/read_file                               # what it may touch
```

`lint` with no flow checks every flow in scope and reports all of them before exiting
non-zero, which is the shape a pipeline wants: one run, every answer.

## Running them for free

Point the agents at `"adapter": "echo"` in their `spec.json` and the whole graph runs
offline, with no model and no cost. See
[Agents](../docs/components.md).

## Starting your own

[The quickstart](../docs/flows.md) writes a flow from nothing. The
[documentation](../docs/README.md) has the rest.
