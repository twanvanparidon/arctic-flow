# Design

Why the engine is arranged this way, and what breaks if you rearrange it. Read this to change
arctic-flow. To use it, read [the documentation](../README.md).

These pages are more detailed than the user-facing ones on purpose. A user needs the rule; a
contributor needs the rule, the reason, and what happens to someone who does the obvious thing
instead.

| Page | Covers |
| --- | --- |
| [Execution](execution.md) | `engine/executor.py`: the state machine, skips, loops, the pool, the ceiling |
| [Resolution](resolution.md) | `paths/`: roots, namespaces, packs, and why `arctic/` is refused rather than preferred |
| [Components](components.md) | the directory contract, spec schemas, adapters, tools inside a turn |
| [Secrets](secrets.md) | the vault, what a step is given, and the four rules that keep it there |
| [Layering](layering.md) | the `src/` layout and the four invariants that hold it |
| [Contracts](contracts.md) | what other people's files are written against, and what breaks |
| [Deferred](deferred.md) | what is deliberately missing, and what would earn it |

## The idea

A flow is a graph of steps. Each step declares **where it hands its result next**, not what it
waits for, so a flow reads forwards. The engine derives the reverse edges, runs whatever is
ready, and delivers results onward.

Workflows are code. They live in files you can diff, review and override, not in a UI. A flow
can also be read without running it: `atf inspect flow` draws the graph and `atf lint` checks
every reference and every component spec, and neither calls a model.

Six ideas carry most of the rest.

**A component is a directory with a contract.** A tool holds `spec.json`, a doc and an
executable; an agent holds `spec.json` and `agent.md`. Nothing about one lives outside its own
directory, so it can be added, replaced or deleted without touching the engine.

**Components are found by name, not by path.** Roots are searched in precedence order and the
first match wins. A project overrides what it inherits, totally and per name.

**One namespace is not overridable.** Everything the engine ships sits under `arctic/`, and
the resolver **refuses** rather than choosing when anything else defines a name inside it.
That is a security property: `tool: arctic/read_file` has to mean the tool that ships, or
reading a flow says nothing about what it runs, and a cloned repository is a search root.

**There is one config file, and it is small.** The bar for adding a key to
`~/.arctic/config.yaml` is that no component and no flow could own it. An unknown key is
refused rather than ignored.

**A flow names the graph and nothing else.** Which model, which effort and which prompt belong
to the agent, in its own directory, so changing a prompt is not a change to the workflow.

**Fail loudly rather than plausibly.** The engine refuses a flow that reads from a step it
does not depend on, a switch value matching no case, an agent granted a writing tool without
saying it is unattended, a release whose tag disagrees with its version. Each could have been
a default, a guess or a silent no-op, and each would have been wrong in a way nobody noticed
until it mattered.

## House style for these pages

Say what the code cannot: ordering that matters, what happens on the path not taken, why a
check sits where it does rather than one line later. Where the obvious approach is wrong, say
what happens to someone who tries it. Several comments in this repository exist because it was
tried and it failed.

Do not restate a contract the schema already carries. That belongs in
[the user documentation](../README.md), once.
