---
name: help
description: Debug and improve an existing Arctic Flow workflow run by the `atf` engine. Use this whenever an `atf lint` or `atf run` fails or is refused, a flow hangs, a step is skipped or never runs, a branch takes the wrong case, a gate or a loop runs out of attempts, a template reference is rejected, a tool or agent name will not resolve, a secret is refused, an agent's granted tool does not work, or the user asks why a flow behaves as it does or how to make it cheaper, faster or more reliable. Use it for any question about flow YAML, a `spec.json`, `agent.md`, `run.sh` or the `atf` CLI itself.
---

# Debugging and improving an Arctic Flow

The engine answers most of these questions itself. Ask it before reading anything, and
before guessing.

## 1. Find the engine and the project

```sh
atf --version || python3 src/main.py --version
```

Use whichever answers for every command below. A project is a directory with `flows/` in
it. `--workspace DIR` goes **before** the subcommand.

## 2. Run lint first, every time

`lint` performs exactly the checks `run` performs before its first step: the graph, every
template reference, and every component spec. Its message names the step and usually says
what to do.

```sh
atf lint <flow>                 # one flow, stops at its first problem
atf lint                        # every flow in scope, all answers, non-zero if any failed
```

Do not read the YAML looking for the fault until lint has spoken. It catches unreachable
steps, sideways reads, undeclared loops, a `run.sh` that lost its executable bit, an
`effort` the adapter rejects, and a step passing a key the tool does not accept.

Take the message to `references/errors.md`. It maps every refusal to its cause and its fix.

## 3. Read the graph back

```sh
atf inspect flow <flow>         # push edges as text, loops marked
atf inspect flow <flow> -o md   # Mermaid, plus which steps run concurrently, which may be
                                # skipped by a branch, and where the joins are
```

The `-o md` report is the fastest way to answer "why did that step not run" and "what does
this actually wait for". Nothing is executed.

```sh
atf inspect agent <name>        # its settings, and its system prompt verbatim
atf inspect tool <name>         # what it takes, what it may touch, how it fails
atf inspect adapter <name>      # what an agent spec naming it may ask for
```

`inspect agent` matters more than it looks. A flow naming an agent inherited from a higher
search root shows nothing of its prompt, and the prompt is the whole of what the agent is.

## 4. When a name will not resolve

```sh
atf list
```

Every flow, tool, agent and adapter that resolves, beside the definition that won.
Anything a higher-precedence root shadows is marked, and a second definition is why an edit
can appear to do nothing.

Roots are searched in this order, first match wins: `$ATF_PATH`, then `./.arctic`, then the
workspace root, then `~/.arctic`, then what ships with the engine. Overriding is per name
and total: a project's `arctic/read_file` replaces the built-in and inherits nothing from
it. `arctic/read_file` and a bare `read_file` are two different tools.

A name that does not resolve reports every path it was looked for, so read that list rather
than guessing which directory was meant.

## 5. When it lints but does not run

Lint cannot see a value that only exists at run time. These are the failures left:

```sh
atf run <flow> --input path=... --trace     # per-step JSON summary on stderr
atf run <flow> --input path=... -q          # no progress, only the output
```

- **A switch matched no case.** The rendered value is not one of the case keys. Print what
  the step actually returns, add a `default:`, or give the agent an `output_schema` so the
  field is a fixed set rather than prose.
- **A template resolved to nothing.** An unresolvable path is an error, never an empty
  string. `{{ steps.x.json.field }}` fails when the step's result is not JSON, and before
  the step has run at all.
- **A step failed and took the flow with it.** The error carries the step id. A tool's
  non-zero exit is reported using the sentence from its own `exit_codes`.
- **A gate ran out of attempts.** The step fails carrying what the gate last said. Where
  the prompt and the gate disagree, the prompt is what the model is writing to, so make the
  two agree before raising `max_attempts`.
- **A loop ran out of passes.** Running out is a failure by design: a loop that never
  converged has not done its job. Check that the writing step reads both its own previous
  answer and the review, or every pass starts over and fixes what the last pass broke.
- **Nothing wrote the file.** A turn that succeeds while the output directory stays empty
  is what isolation causes: `--safe-mode` disables MCP servers, so an agent with granted
  tools quietly has none. Check the file, not the exit status.

Reproduce it for free before spending money on it. Point the agents at `"adapter": "echo"`
and the whole graph runs offline: `!fail` in a prompt exercises a refusal, and
`!json {"verdict":"rejected"}` drives a switch down a chosen branch.

## 6. When a step is skipped or a run hangs

A branch that is not taken has its edges marked `skipped`, and skipping propagates: a step
whose every inbound edge is skipped is itself skipped. That is what lets a join downstream
of a branch run on both paths instead of waiting forever.

A skipped step still resolves in templates, as the literal `(not run)`. So a prompt reading
`{{ steps.risk_scan.text }}` gets that text rather than failing, and can mention the gap.

If a step never ran and you did not expect that, `inspect flow -o md` says which branch
could skip it. If a run hangs, it is a tool without a timeout: give it
`run.timeout_seconds` well under the engine's 60 second default.

## 7. Improving a flow

- **Move work out of the model.** Anything deterministic is a tool step: free, repeatable,
  and it cannot misread its instructions. `glob` finds files, `grep` finds text in them,
  `read_file` returns them. In that order, this is much cheaper than asking an agent to
  read a tree.
- **Push to several steps at once.** Two steps pushed from one place run concurrently, and
  a step named by two places runs once both arrive. Nothing has to be declared for either.
- **Branch instead of always paying.** A triage step with a `switch` spends the expensive
  path only when it is warranted.
- **Prefer a gate to a longer prompt.** A rule a tool can check is a rule the model cannot
  talk itself out of. Ask for the limit in the prompt and enforce it in the gate.
- **Prefer a loop when a pass should be an edit.** Hand the next pass both its own previous
  answer and the review of it, or it rewrites from scratch every time and never converges.
- **Give a branching agent an `output_schema`.** Switching on `{{ this.json.verdict }}` is
  reliable; switching on prose is not.
- **Check what a step may read.** `inspect flow -o md` prints a secrets column, so which
  step holds what is answerable without running anything.
- **Watch the cost line.** A gated step reports the cost of all its attempts, and every
  in-turn tool call prints its own line under the step.

## Reference

Read the file that covers the question rather than all of them:

| File | Covers |
| --- | --- |
| `references/flow-yaml.md` | every flow and step key, and what each one refuses |
| `references/templates.md` | the five namespaces, `.text` and `.json`, `(not run)`, what is legal where |
| `references/components.md` | tool and agent `spec.json`, adapters, name resolution, secrets |
| `references/errors.md` | each lint and run refusal, its cause and its fix |

The user's own installed version is the authority. Where a reference and the engine
disagree, run `atf inspect`, `atf lint` or `--help` and believe those.
