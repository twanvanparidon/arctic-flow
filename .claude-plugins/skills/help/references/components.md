# Components

A component is a directory with a contract, found by name. Nothing about one lives outside
its own directory.

| Kind | Is | Holds |
| --- | --- | --- |
| tool | a directory | `spec.json`, a markdown doc, an executable `run.sh` |
| agent | a directory | `spec.json` plus `agent.md`, which **is** the system prompt |
| flow | one YAML file | the graph and nothing else |
| adapter | a Python module | shipped with the engine, not user-extensible |

## How a name resolves

Roots are searched in precedence order and the first match wins:

```
$ATF_PATH  →  ./.arctic  →  the workspace root  →  ~/.arctic  →  what ships with the engine
```

Under any root, components live in `tools/`, `agents/` and `flows/`.

Overriding is per name and total. A project's `arctic/read_file` replaces the built-in and
inherits nothing from it. Where a component is *found* never changes where it *runs*: a
tool executes with its working directory set to the workspace root.

A name may carry a namespace at any depth. A directory holding a `spec.json` is a
component; any other directory is a namespace. There is nothing to declare.

```
tools/
   arctic/read_file/       ->  tool: arctic/read_file
   git/commit/             ->  tool: git/commit
   git/worktree/add/       ->  tool: git/worktree/add
```

The name is the whole path. `arctic/read_file` and a bare `read_file` are two tools, and
overriding one does not touch the other. Everything the engine ships is under `arctic/`.

```sh
atf list          # every name that resolves, and what shadows what
```

A shadowed definition is why an edit can appear to do nothing. A name that does not resolve
reports every path it was looked for.

## Tool spec.json

```json
{
  "name": "greet",
  "version": 1,
  "description": "One sentence: what it does and when to reach for it.",
  "doc": "tool.md",

  "run": {
    "command": ["./run.sh"],
    "input": "stdin_json",
    "output": "stdout_text",
    "cwd": "workspace",
    "timeout_seconds": 10
  },

  "input_schema": {
    "type": "object",
    "properties": { "text": { "type": "string", "minLength": 1, "description": "..." } },
    "required": ["text"],
    "additionalProperties": false
  },
  "output_schema": { "type": "string" },

  "exit_codes": {
    "0": "success (the result is on stdout)",
    "2": "invalid input"
  },

  "permissions": { "filesystem": "none", "network": false },
  "requires": ["bash", "jq"]
}
```

Required: `name`, `description`, `run`, `input_schema`, `permissions`. `run` requires
`command`.

`name` is the leaf only. The namespace is which directory the component sits in, which a
spec has no way of knowing.

`permissions.filesystem` is one of `none`, `read`, `write`. It is checked rather than
described, because it is the gate on granting the tool to an agent. Spelled `"rw"` or left
out, that gate would open silently.

`additionalProperties: false` is what lets `lint` catch a flow passing a key the tool does
not accept.

`run.command[0]` is resolved against the component's own directory, and has to exist and be
executable. A lost `chmod +x` is the most common way a tool fails on another machine.

`timeout_seconds` defaults to 60. Give anything that could hang a shorter one.

### The run.sh contract

- One JSON object on stdin, matching `input_schema`.
- The result on stdout, and nothing else.
- One line on stderr when the exit code is non-zero, with a code listed in `exit_codes`.
  The engine turns the number back into your own sentence.
- No trailing newline after a single-value result. A digest gets templated mid-line.

The script stays runnable by hand, which is how it is worth debugging:

```sh
echo '{"text":"hello"}' | ./tools/greet/run.sh
```

### The tools that ship

| Tool | Does |
| --- | --- |
| `arctic/read_file` | one file verbatim, or several with a header each |
| `arctic/write_file` | writes a file; refuses to clobber unless told to |
| `arctic/glob` | the paths matching a shell pattern |
| `arctic/grep` | a pattern across the tree, as `path:line:text` |
| `arctic/fetch_url` | an `http(s)` body, undecorated |

The first four cannot reach outside the workspace root: a path is canonicalised before use,
so `..` and a symlink pointing out are both refused. `fetch_url` touches the network and
nothing else.

`glob` finds files, `grep` finds text in them, `read_file` returns them. In that order.

## Agent spec.json

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
  "output_schema": { "type": "object", "properties": { "verdict": { "enum": ["approved", "rejected"] } } },
  "max_budget_usd": 0.50,
  "timeout_seconds": 300,
  "tools": [],
  "unattended": false
}
```

Required: `name`, `description`, `adapter`. Everything else is forwarded to the adapter,
which declares its own schema for it. `atf inspect adapter <name>` prints that schema.

`agent.md` is read verbatim and is the whole of what the agent is. Write the job and the
shape of the answer. Nothing in it is about one flow: a prompt naming a step or an input
cannot be reused, and the flow already carries both.

An empty `agent.md`, or a missing one, is refused at lint time.

`output_schema` is what makes a `switch` reliable. It reaches the adapter as its own
parameter, and the answer comes back as a JSON document, so `{{ this.json.verdict }}` is a
field rather than a guess.

## Adapters

Two ship, and there is no `~/.arctic/adapters/`. An adapter is engine infrastructure called
in process, so adding one means a module in the engine's source.

| Adapter | Is |
| --- | --- |
| `claude_code` | calls a model through the `claude` CLI, which must be installed and authenticated |
| `echo` | answers from the request: no runtime, no network, no cost |

`claude_code` requires `model`, because the CLI's configured default is a per-machine
dependency. It is an alias (`opus`, `sonnet`, `haiku`, `fable`) or a full id. `effort` is
one of `low`, `medium`, `high`, `xhigh`, `max`.

`echo` is the dry run. Point an agent at it and the graph, the branches, the gates and
every template run for free. The prompt steers it:

| First word of the prompt | Does |
| --- | --- |
| `!fail <detail>` | the runtime refused, so a failure path can be exercised |
| `!json {...}` | answers with exactly that JSON, so a switch can be driven |
| `!invocation` | answers with a report of what the engine sent, including the tool server argv |

Anything else is answered with the prompt itself, which is what makes a gate loop
observable: the feedback is appended, so the second turn genuinely differs from the first.

An agent declaring `output_schema` gets the smallest object satisfying it, unless `!json`
overrides that.

`lint` checks an agent's settings by building the payload the engine would send and
validating it against the adapter's own schema, so a bad `effort` is caught by the rule
that would have rejected it mid-run.

## Granting an agent tools

An agent's `tools` are the engine's tools, not the runtime's. They reach the turn over MCP,
served by `atf mcp-serve`, and run through the same code a tool step uses, so the sandbox,
the schema check and the timeout are unchanged. A tool spec is already an MCP tool
definition; there is no second spec to write.

```json
"tools": ["arctic/read_file", "arctic/write_file"],
"unattended": true
```

Four rules, all refused at lint time:

- A granted tool whose `permissions.filesystem` is `write` needs `"unattended": true` on
  the agent spec. Nothing approves a call an agent makes for itself.
- A granted tool gets no secrets. Granting one that declares `secrets` is refused, and so
  is a step that declares `secrets` and runs a tool-granted agent.
- Two grants that flatten onto one name are refused. A granted tool reaches the model as
  its name with `__` for the separator, because `mcp__atf__<tool>` cannot carry a slash, so
  `arctic/read_file` is offered as `arctic__read_file`.
- The grant is on the agent, not the step. A flow names the graph and nothing else.

In-turn calls are reported: each one prints its own line under the step, so a turn that
read nine files does not look like one silent step. Calls run concurrently, so a turn takes
the longest rather than the sum.

Grant tools when the number of files is genuinely not knowable in advance. Wire steps when
it is.

## The vault

```sh
atf vault create secrets.vault < secrets.yaml   # a YAML mapping on stdin
atf vault set secrets.vault signing_key         # value from stdin or a prompt
atf vault list secrets.vault                    # names, without values
atf vault view secrets.vault                    # decrypts to stdout: this prints secrets
```

A flow points at one with a top-level `vault:` key, relative to the working directory.
`atf run --vault FILE` overrides it. The file is opened only if something needs it.

The password comes from `--vault-password-file`, `$ATF_VAULT_PASSWORD`,
`$ATF_VAULT_PASSWORD_FILE`, or a prompt. There is deliberately no `--vault-password` flag,
so it stays out of shell history and out of `ps`.
