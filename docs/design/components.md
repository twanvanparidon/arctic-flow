# Components

A component is a directory with a contract. Nothing about one lives outside its own directory,
so it can be added, replaced or deleted without touching the engine.

| Kind | Is | Contract |
| --- | --- | --- |
| tool | directory | `spec.json`, a markdown doc, an executable; JSON on stdin, result on stdout, errors one line on stderr with a code from its own `exit_codes` |
| agent | directory | `spec.json` plus `agent.md`, which **is** the system prompt, read verbatim |
| flow | one YAML file, or a directory holding one of its own name | names the graph and nothing else |
| adapter | Python module | in `src/adapters`, plus an entry in `ADAPTERS` |

## Why a subprocess for a tool and not for an adapter

A tool is user-extensible in any language, so it earns a subprocess and a JSON-on-stdin
protocol. An adapter exists to be called by the engine, in-process, and would pay that cost
for nothing.

So there is no `~/.arctic/adapters/`. Adding one is a module plus a line in `ADAPTERS`.
Loading them from a path would be a plugin mechanism and should be built as one.

`ADAPTERS` is static imports because **a frozen build misses anything resolved by name.** That
is the same reason the scaffolds are files rather than strings and `packages.find` is explicit.

An adapter still declares an `INPUT_SCHEMA` the engine validates against, so the guarantee is
the same one a tool gets. What went away is a file on disk restating what the module already
says, and a layer of shell quoting in between.

## specs.py checks what the runtime reads

`engine/specs.py` holds `TOOL_SPEC_SCHEMA` and `AGENT_SPEC_SCHEMA`, and they are deliberately
the set of fields the runtime actually reads. **Adding a field the engine reads means adding
it to the schema there**, or it passes lint and is silently ignored.

Beyond the schema it verifies `run.command` exists and is executable, that declared schemas
are valid schemas, and that an agent's settings are ones its adapter accepts. That last one
works by building the payload the agent would send and validating it against the adapter's own
`INPUT_SCHEMA`, so an `effort` an adapter does not take fails before a turn is paid for.

`input_schema` is enforced **twice**: against the real payload at run time, and against a
flow's static `input` at lint time. The second only works when the tool sets
`additionalProperties: false`, which is why every shipped tool does.

Templated values are not examined at lint time. A lint that guessed at what a template would
render is a lint people switch off.

`permissions` is required and `filesystem` is an enum rather than free text, because a grant
is decided from it. `"rw"` or a typo would read as "not write" and open the write gate
silently.

## The scaffolds are data

`src/builtin/scaffolds/<kind>/` holds real files, not strings in `commands/scaffold.py`. So a
scaffolded `run.sh` is covered by `shellcheck` with the rest of the repository, and is edited
as shell rather than as an escaped Python literal.

`__NAME__` is the placeholder, and `_declared_name` decides whether it becomes the whole name
(a flow, which is what `run` is handed) or the leaf (a spec, whose namespace is the directory
it sits in).

Everything written has to be runnable as it is. Only `tests/unit/commands/test_scaffold.py`
checks that, so **a new requirement in `engine/specs.py` means updating the scaffold too**.

## Tools inside a turn

An agent's `tools` are the **engine's** tools. They reach the turn over MCP, served by
`atf mcp-serve`, and run through the same `invoke()` a tool step uses, so containment, the
schema check and the timeout are unchanged.

A tool's `spec.json` is already an MCP tool definition: `description` plus its doc, and
`input_schema`. There is no second spec to write and nothing to keep in step.

The tool's **name** there is the one it was looked up by, not `spec["name"]`, which for a
namespaced tool is only the leaf.

Naming a runtime's own built-in tools instead would tie one agent spec to one adapter, which
is the coupling the adapter layer exists to remove.

### The gates, and why each is where it is

**A granted tool whose `permissions.filesystem` is `write` needs `unattended: true`.** Nothing
approves a call an agent makes for itself, so a grant that can change the workspace is
declared where the grant is, not where the tool is.

**In-turn calls are reported.** `mcp-serve --events` appends one line per call and the engine
forwards it to `on_event`, so a turn that read nine files does not look like one silent step.
Without that, the cost of a grant is invisible.

**No secret reaches an in-turn tool.** See [secrets](secrets.md).

### An isolation gap worth knowing

`claude_code`'s `isolate` defaults true, but how it is spelled depends on the turn:
`--safe-mode` without tools, and `--setting-sources "" --disable-slash-commands` with them,
because `--safe-mode` disables MCP servers and would silently leave the agent with no tools at
all.

That substitute is narrower than the flag it replaces, and the gap is listed in `build_args`.
The user-visible symptom is a turn that succeeds while the output directory stays empty.

## Adapter flags move

`claude_code`'s flags are verified against `VERIFIED_CLI_VERSION` and move between CLI
releases. Check `claude --help` before adding a parameter and move the constant.

`model` is required rather than defaulted, because the CLI's configured default is a
per-machine dependency and a flow that ran differently on two laptops is worse than one that
refuses.

`echo` is not a second runtime. It answers from the request, so a flow's graph, branches, loops
and templates run with no runtime, no network and no cost. It is also the only way
`tests/e2e` reaches an agent step at all, since `ADAPTERS` is frozen into the binary and
nothing outside it can register one.

One real implementation is deliberate: the adapter interface is duck-typed modules, and the
**second** runtime is what earns a change to it. See [deferred](deferred.md).

## Packs

A pack is components that ship switched off. The design decisions are in
[resolution](resolution.md#packs-sit-inside-the-built-in-root); what a pack owes when you add
one is in [the user documentation](../components.md#writing-a-pack).

Two that are design rather than instruction:

- **`permissions.filesystem` is one value per tool**, so a tool that both listed branches and
  switched them could only ever be granted as one that writes. That is why `git/branch` and
  `git/checkout` are two tools rather than one with a flag.
- **A shared helper goes outside `tools/`**, since the resolver walks that directory for
  `spec.json` and anything else in there reads as an empty namespace.
