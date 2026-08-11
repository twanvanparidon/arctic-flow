<!-- Generated from docs/cli.md by packaging/sync_docs.py. Edit that file. -->

# The CLI

Every command and flag. The installed engine is the authority: `atf <command> --help` prints
what your build takes, and every command has an epilog explaining itself.

```
atf [-h] [-v] [--workspace DIR] <command> ...
```

| Global flag | Does |
| --- | --- |
| `-h`, `--help` | help for the engine, or for the command it follows |
| `-v`, `--version` | print the version and exit. **This is version, not verbose** |
| `--workspace DIR` | the [project](projects.md) root. Defaults to the current directory |

`--workspace` **goes before the subcommand**, which is worth checking first when a name will
not resolve.

| Command | Does |
| --- | --- |
| `run` | execute a flow |
| `lint` | validate a flow without running it |
| `inspect` | a flow's graph, an agent's prompt, a tool's contract, an adapter's schema |
| `create` | scaffold a flow, agent or tool |
| `init` | create `$HOME/.arctic` |
| `list` | every name that resolves, and where each was found |
| `completion` | print a shell completion snippet (bash) |
| `vault` | manage an encrypted secrets file |
| `mcp-serve` | serve tools to an agent's turn. An adapter invokes this, you do not |

| Exit code | Means |
| --- | --- |
| 0 | it worked |
| 1 | an expected failure, printed as `engine: <message>` on stderr |
| 2 | the arguments were wrong |
| 130 | interrupted |

## run

```sh
atf run <flow> [--input KEY=VALUE] [--trace] [-q] [--vault FILE] [--vault-password-file FILE]
```

| Flag | Does |
| --- | --- |
| `--input KEY=VALUE` | a flow input. Repeat for several. Beats `$ATF_VAR_KEY` |
| `--trace` | a per-step JSON summary on stderr, after the run. What to capture in CI |
| `-q`, `--quiet` | no live progress, only the flow's output |
| `--vault FILE` | overrides the flow's own. Only opened if something needs it |
| `--vault-password-file FILE` | otherwise `$ATF_VAULT_PASSWORD`, `$ATF_VAULT_PASSWORD_FILE`, or a prompt |

`flow` is a name resolved through the lookup, or a path to a `.yaml` file.

**stdout carries the flow's output and nothing else**, so `atf run f > out.md` gives the
result byte for byte. Progress goes to stderr:

```
→ read_target    tool arctic/read_file
✓ read_target    24ms → summarize, triage
  · annotate     arctic/read_file ok (12ms)
⤼ risk_scan      skipped, its branch was not taken
⟲ review         back to write, loop 2/3
✗ sign           hmac_sign: no key

  4 steps (1 skipped) · 6.2s · $0.0431
```

`→` started, `✓` finished, `⤼` skipped, `⟲` sent the work back, `✗` failed, `  ·` an in-turn
tool call. The cost line is omitted when nothing cost anything.

In a script:

```sh
ATF_VAULT_PASSWORD_FILE=/run/secrets/vault \
  atf run deploy --input target=staging -q > result.txt || exit 1
```

### The trace

`--trace` writes one JSON object to stderr when the run ends, so `-q --trace` gives the flow's
output on stdout and the summary on stderr, with nothing else on either.

```json
{
  "flow": "review_file",
  "cost_usd": 0.0431,
  "steps": [
    { "step": "read_target", "ms": 24, "ok": true, "pushed_to": ["triage"], "cost_usd": null },
    { "step": "risk_scan", "skipped": true },
    { "step": "sign", "ms": 12, "ok": false, "error": "hmac_sign: no key" }
  ]
}
```

`steps` is in the order they finished, and an entry has one of those three shapes. A skipped
step carries `skipped` and nothing else, so read that key before `ok`. `cost_usd` is `null` on
a tool step, which costs nothing, and `pushed_to` is `[]` where a step ended a path.

A step that ran more than once carries `iteration`, counting from 2, so a loop's passes are
separate entries. The key is absent on a step that ran once, rather than `null` on every step
of every flow without a loop.

## lint

```sh
atf lint                  # every flow in scope: all answers, non-zero if any failed
atf lint .                # the same
atf lint review_file      # one flow, stopping at its first problem
```

Exactly the checks `run` runs before its first step: the graph, every template reference,
every component spec. Nothing is executed and nothing is paid for.

The two forms differ on purpose. With no flow it reports every problem in every flow before
exiting, which is the shape a pipeline wants: one run, every answer. Naming one flow stops at
its first problem instead.

**Run it before you read the YAML looking for the fault.** Take the message to
[the reference](reference.md).

## inspect

```sh
atf inspect flow <flow> [-o raw|md]
atf inspect agent <name>
atf inspect tool <name>
atf inspect adapter <name>
```

`-o raw` (the default) prints the push edges as text, with `(terminal)` where a step ends the
flow so that does not look like a missing edge.

`-o md` is a whole Markdown document: a Mermaid diagram, then sections for Resolution, Branches,
Loops and Joins. Three things in there are derived rather than stated anywhere in the YAML:

| Section | Answers |
| --- | --- |
| waves | which steps run concurrently |
| always runs | whether every case, without exception, eventually reaches this step |
| joins | which steps wait on more than one edge, and which of those may be skipped |

A secrets column appears when any step declares one. **This is the fastest way to answer "why
did that step not run".**

`inspect agent` prints its settings and its system prompt verbatim, which is the only way to
see the prompt of an agent inherited from a higher root.

## create

```sh
atf create flow <name>          # flows/<name>.yaml
atf create agent <name>         # agents/<name>/: spec.json, agent.md
atf create tool <name>          # tools/<name>/: spec.json, tool.md, run.sh
```

It lands where the lookup reads from first: `./.arctic` when the project has one, and the
project root otherwise, so what is created is what then resolves. Nothing is overwritten.
[Components](components.md#scaffold-first) has what each one contains.

## init

```sh
atf init        # $HOME/.arctic/: tools/, agents/, flows/, config.yaml
```

Idempotent, and it never overwrites, so running it again after an upgrade adds what is missing.
`--workspace` does not apply: this always writes your real home directory.
[Setting up](setup.md#arctic) has what goes in it.

## list

```sh
atf list
```

Every flow, tool, agent and adapter that resolves, beside the definition that won, plus every
pack and whether it is on. [Projects and names](projects.md#seeing-what-resolved) covers how to
read it.

## completion

```sh
eval "$(atf completion bash)"        # in ~/.bashrc, once
```

Prints a snippet for the named shell, and `bash` is the only one so far. It completes the
commands, their flags, and flow names resolved from the `--workspace` on the line.

It completes the installed `atf`. A checkout invoked as `python3 src/main.py` is a `python3`
command as far as the shell is concerned, and is completed as one.

## vault

```sh
atf vault create <file> [--force]     # from a YAML mapping on stdin
atf vault set <file> <name>           # value from stdin, or a prompt
atf vault list <file>                 # names only
atf vault view <file>                 # decrypts to stdout: this prints secrets
```

The password is resolved `--vault-password-file` → `$ATF_VAULT_PASSWORD_FILE` →
`$ATF_VAULT_PASSWORD` → a prompt.

**There is deliberately no `--vault-password` flag, and no `--value` on `set`.** Both would
put a secret in `argv`, where `ps` shows it to every user on the machine.

A secret's name has to work as an environment variable: `^[A-Za-z_][A-Za-z0-9_]*$`. A wrong
password and a tampered file are indistinguishable by design: both mean the ciphertext did not
authenticate.

The file is safe to commit. `examples/sign-release/secrets.vault` is committed on purpose, so
that example runs with nothing to prepare, and its password is `demo`.

## mcp-serve

```sh
atf mcp-serve [--tool NAME] [--events FILE]
```

An adapter spawns this to serve an agent's granted tools over MCP on stdio. You do not type it.
`--tool` names one to expose and repeats; `--events` appends one JSON line per call, which is
how the engine reports in-turn calls as progress.

stdin and stdout carry JSON-RPC framing, so nothing else may be written to either. It is
documented here because it appears in a process list and in `--events` output, not because it
is an entry point. See [granting an agent tools](components.md#granting-an-agent-tools).
