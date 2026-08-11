# Components

A component is a tool, an agent or a flow: the things a flow names. What ships first, then
writing your own, then granting an agent tools, then what a run costs and how to avoid paying
it while you build.

Check what ships before writing anything. A tool you do not have to maintain is the cheapest
kind.

## What ships

### Tools

Six, always available, all under `arctic/`, all contained to the workspace.

| Tool | Does |
| --- | --- |
| `arctic/read_file` | One file verbatim, or several with a header each |
| `arctic/write_file` | Writes a file. Refuses to clobber unless told to |
| `arctic/edit_file` | Replaces an exact string. Refuses an ambiguous match |
| `arctic/glob` | The paths matching a shell pattern |
| `arctic/grep` | A pattern across the tree, as `path:line:text` |
| `arctic/fetch_url` | An `http(s)` URL, body undecorated |

`glob` finds files, `grep` finds text in them, `read_file` returns them. In that order it is
far cheaper than asking a model to read a tree.

`write_file` takes whole contents and `edit_file` takes the old text and the new, so a one
line change to a large file costs one line rather than all of it.

The first five cannot reach outside the workspace root: a path is canonicalised before use, so
`..` and a symlink pointing out are both refused. `fetch_url` touches the network and nothing
else, and its `permissions.filesystem` is `none`.

```sh
atf inspect tool arctic/read_file    # what it takes, what it may touch, how it fails
```

### Packs

More ship switched off. Add one to `packs:` in `config.yaml` to use it, which
[setting up](setup.md#packs) covers.

| Pack | Holds | Needs |
| --- | --- | --- |
| `git` | the repository the flow runs in: status, log, diff, show, branch (read); add, commit, checkout (write) | `git`, `jq` |
| `github` | pull requests: open, status, comment. Also Enterprise, via `$GITHUB_API_URL` | `curl`, `jq`, `git` |
| `bitbucket` | the same three, Bitbucket **Cloud** only | `curl`, `jq`, `git` |

Every tool in all three acts on the repository whose root is the workspace itself, and is
refused otherwise. Nothing in `git` reaches the network, and there is deliberately no `push`,
`reset`, `rebase`, `clean`, `--force`, `add -A` or `--no-verify`.

The two forge packs answer in **JSON with the same field names**, so swapping
`arctic/github/pr/status` for the bitbucket one changes the tool name and nothing else. A field
a forge cannot answer is `null`, never invented: Bitbucket reports no `mergeable` without a
dry-run merge, so gate on `checks` and `reviews`.

**No agent can be granted a forge tool.** Every one declares `secrets`, and a tool that expects
a credential cannot be granted, so opening a pull request is always a step the flow decided on.
Neither pack approves, merges, closes or pushes: a flow that could cast an approving review
could approve its own work.

## Scaffold first

```sh
atf create tool deploy/notify   # tools/deploy/notify/: spec.json, tool.md, run.sh
atf create agent reviewer       # agents/reviewer/: spec.json, agent.md
```

Written into `./.arctic` when the workspace keeps one, and the workspace root otherwise: the
top of the search order, so what is created is what then resolves. Nothing is overwritten, and
a name under `arctic/` is refused.

Past the scaffold, copy the nearest thing that ships rather than starting fresh.

## Writing a tool

A directory: a `spec.json`, a markdown doc, and an executable. Any language.

- **One JSON object on stdin**, matching `input_schema`.
- **The result on stdout.** Text, or JSON when a flow needs to switch on a field.
- **Errors on stderr**, one line, with a code listed in the spec's own `exit_codes`. The engine
  turns that code back into a message using your text.
- **Exit 0 means the tool did its job.** A check that rejects still exits 0.
- **No trailing newline on a single-value output.** A digest gets templated mid-line.

The working directory is the workspace root, wherever the tool was found.

| `spec.json` | Required | Is |
| --- | --- | --- |
| `name` | yes | the leaf only. The namespace is the directory it sits in |
| `description` | yes | one sentence. The first thing a model reads about it |
| `run.command` | yes | argv. `[0]` is resolved against the component directory and must be executable |
| `run.timeout_seconds` | no | default 60. Set one well under it, or a hang is yours |
| `input_schema` | yes | a JSON Schema. Set `additionalProperties: false` |
| `permissions.filesystem` | yes | `none`, `read` or `write` |
| `permissions.network` | no | boolean |
| `output_schema`, `exit_codes` | no | a schema, and a mapping of code to sentence |
| `secrets`, `requires` | no | names it expects in the environment, and what must be on `PATH` |
| `run.input`/`output`/`cwd` | no | `stdin_json`, `stdout_text`, `workspace` |

`input_schema` is enforced in two places: against the real payload at run time, and against
the flow's static `input` at lint time. The second needs `additionalProperties: false`.

`permissions` is required and `filesystem` is an enum, because granting a tool that writes
needs `unattended: true`. A free-text `"rw"` would read as "not write" and open that silently.

`tool.md` beside the spec is **what a model is given** when the tool is granted. Write it for
that reader: when to use it, when not to, how it fails. There is no second spec for MCP.

## Writing an agent

A directory: a `spec.json`, and an `agent.md` that is itself the system prompt, read verbatim.

A flow names the graph and nothing else, so model, effort, budget, output shape and granted
tools all live here. Changing a prompt is not a change to the workflow.

```json
{
  "name": "annotator",
  "description": "Reads an incident note and writes an annotated copy.",
  "system_prompt": "agent.md",
  "adapter": "claude_code",
  "model": "sonnet",
  "effort": "medium",
  "output_schema": { "type": "object", "properties": { "verdict": { "enum": ["risky", "clean"] } } },
  "tools": ["arctic/read_file", "arctic/write_file"],
  "unattended": true,
  "timeout_seconds": 900,
  "max_budget_usd": 0.5
}
```

`name`, `description` and `adapter` are required. `model` is required by `claude_code`, because
the CLI's configured default is a per-machine dependency. `effort` is `low`, `medium`, `high`,
`xhigh` or `max`.

Settings are checked against the adapter's own schema at lint time, so an `effort` it does not
accept fails before a turn is paid for.

**Declare an `output_schema` whenever a flow branches on the answer.** The result is then
readable as `{{ steps.triage.json.verdict }}` and the switch matches a fixed set.

```sh
atf inspect agent reviewer     # its settings, and its prompt verbatim
```

That matters: a flow naming an agent inherited from a higher root shows nothing of its prompt,
and the prompt is the whole of what the agent is.

## Granting an agent tools

```json
"tools": ["arctic/read_file", "arctic/write_file"],
"unattended": true
```

**The engine's tools, not the runtime's.** They reach the turn over MCP and run through the
same code a tool step uses, so containment, the schema check and the timeout are unchanged. A
tool's `spec.json` is already an MCP definition; there is no second spec.

`arctic/read_file` is offered to the model as `arctic__read_file`, because MCP names cannot
carry a slash. Granting two names that flatten onto one is refused.

Two gates:

- **A tool with `filesystem: "write"` needs `unattended: true`.** Nothing approves a call an
  agent makes for itself.
- **A granted tool gets no secrets**, in both directions. The adapter is handed the step's
  grant, and a tool the agent calls would inherit the lot.

In-turn calls are reported, one indented line each, so a turn that read nine files does not
look like one silent step. Calls run concurrently, and a cancelled one is really stopped.

**Prefer tool steps** where the flow knows what it needs: free, visible in `inspect flow`, one
trace row each. **Grant** where the agent has to choose and choosing would otherwise cost a
round trip per file.

If a turn succeeds while the output file stays empty, check the file rather than the exit
status. Isolation without granted tools uses `--safe-mode`, which disables MCP servers, so the
adapter substitutes narrower flags when tools are present.

## Cost, and running for free

A tool step is free: a subprocess, and nothing leaves the machine. An agent step is a paid turn.
`effort` sets how hard the model works, and `max_budget_usd` and `timeout_seconds` cap it, all
three on the agent's own spec. `run.max_minutes` bounds the whole run and no flow can raise it,
but it cannot interrupt a turn already started, so the real stop can be one turn later.

`atf run` prints the total on its last line, and `--trace` gives a `cost_usd` per step. A
tool-only run spent nothing and prints no cost line at all.

**Set `"adapter": "echo"` and the agent answers from the request instead of a model.** No
runtime, no network and no key, so nothing is spent. The graph, its branches, its skips and its
loops run exactly as they would, so it is both how to build a flow and how to reproduce a
problem in one before paying to reproduce it twice.

**The $0.01 a turn it reports is notional.** Nothing was charged. It is a flat rate, so a cost
line and a `cost_usd` appear where a real run would have them. `max_budget_usd` and
`timeout_seconds` are accepted and ignored, so a dry run says nothing about whether a real turn
would stay inside them: `claude_code` passes the budget to the CLI as a ceiling for the turn.

An agent that declares an `output_schema` gets the smallest object satisfying it, so a flow
written for a real runtime dry-runs without being edited. Otherwise the turn answers with the
prompt itself, which is what makes a loop observable: the second pass reads the first out of
`steps`, so a guarded prompt really does differ.

The first word of the first line may steer it:

| Directive | Does |
| --- | --- |
| `!fail <detail>` | the turn fails, so a failure path can be exercised |
| `!json <one line>` | answers with exactly that JSON, and overrides `output_schema` |
| `!invocation` | answers with what the engine sent, granted tools and argv included |

`!json {"verdict":"rejected"}` on a check is how you drive a loop without paying for one.
Granted tools are accepted and never dispatched: there is no runtime here to serve them to.
Point every agent at `echo`, run the flow, then set them back.

## Adapters

How the engine talks to a model runtime. One turn in, the same normalised envelope out.

| Adapter | Is |
| --- | --- |
| `claude_code` | spawns the Claude Code CLI. Needs `claude` installed and authenticated |
| `echo` | answers from the request. No runtime, no network, no cost |

Adapters are Python modules in `src/adapters/` plus an entry in `ADAPTERS`. There is no
`~/.arctic/adapters/`: a tool is user-extensible in any language and earns a subprocess, while
an adapter is called in-process and would pay for one and get nothing. The registry is static
imports, because a frozen build misses anything resolved by name.

A module declares `NAME`, `DESCRIPTION`, `INPUT_SCHEMA` and `run(payload, env) -> dict`.
`INPUT_SCHEMA` is the contract that checks an agent spec, so adding a setting means adding it
in two places: there, and to the agent spec schema in `engine/specs.py`.

The envelope carries `ok`, `text`, `stop_reason`, `session_id`, `requested_model`, `num_turns`,
`usage`, `cost_usd`, `duration_ms`, `model_usage` and `adapter`. Failures raise `AdapterError`
rather than returning `ok: False`.

```sh
atf inspect adapter claude_code    # what an agent spec naming it may ask for
```

`claude_code` pins a `VERIFIED_CLI_VERSION`, and the flags move between releases. Check
`claude --help` before adding a parameter and move the constant.

Adding one is a change to arctic-flow itself. Copy `src/adapters/claude_code.py`. The interface
is duck-typed modules on purpose: the second real runtime is what earns a change to it, not the
first.

## Writing a pack

A pack ships inside the engine itself, so adding one is a change to arctic-flow. A directory
under `src/builtin/packs/` with a `pack.json` beside the usual `tools/`, `agents/`, `flows/`.
Nothing has to be registered.

- **Name everything under `arctic/`.** That is the point of a pack rather than a source, and it
  is the only thing a pack has that a cloned repository does not.
- **Say what is deliberately absent**, in its README. What a first-party tool refuses to do is
  half of what makes it worth shipping.
- **Split read from write.** `permissions.filesystem` is one value per tool, which is why
  `git/branch` and `git/checkout` are two tools rather than one with a flag.
- **Share a helper if the tools share a check**, outside `tools/`, since the resolver walks
  that directory for `spec.json`.

A pack that reaches the network owes three more:

- **Declare `secrets`** and read the credential from the environment.
- **Keep it out of `argv`.** Hand curl a config file with mode 600, because `-H` shows the
  credential to `ps`, and `ps` shows a command line to every user on the machine.
- **Answer in JSON with the same field names as its sibling**, so the two are interchangeable
  in a flow's templates.
