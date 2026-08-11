# Reference

## What a refusal means

`lint` and `run` perform the same checks, so everything here can be reached by either. Match
on the identifying phrase: the exact wording may improve between versions.

### Shape and edges

| Message | Cause and fix |
| --- | --- |
| `flow is missing required field` | no `flow`, `start` or `steps`. `start` names the one entry step |
| `'steps' must be a non-empty list` | a YAML list, one item per step |
| `flow file must contain a YAML mapping` | the top level is `flow:`, `start:`, `steps:` |
| `every step needs an 'id'` / `duplicate step id` | ids are how everything refers to a step |
| `must set exactly one of 'tool' or 'agent'` | both, or neither. A step runs one component |
| `agent step '<id>' needs a 'prompt'` | `prompt:` or `prompt_file:`, never both |
| `'output' must be a mapping with a 'template' key` | `output: "{{ … }}"` is the typo |
| `pushes to unknown step` | a typo. Check it against `atf inspect flow` |
| `pushes to itself` | put a check between the step and itself, and loop through that |
| `is unreachable: nothing pushes to it` | wire it to a `push` or a case, or delete it |
| `sets both 'push' and 'switch'` | a step hands on unconditionally or picks one branch |
| `steps form a cycle nothing enters` | no walk from `start` reaches it |

### Branches

| Message | Cause and fix |
| --- | --- |
| `has 'cases' or 'default' but no 'switch'` | add `switch:`, or use `push:` |
| `needs a 'switch' expression` | e.g. `switch: "{{ this.json.verdict }}"` |
| `has a switch but no 'cases'` | at least one case |
| `case key … is not a string` | YAML read a bare `yes`/`no`/`on`/`off`/`true`/`false`. Quote it |
| `case '<key>' must be a list of step ids` | `rejected: [write]`, and `[]` to end the path |

At **run time**, `switched on '<value>', which matches no case` means the rendered value hit no
case and there was no `default`. Add one, or give the agent an `output_schema`.

### Loops and checks

| Message | Cause and fix |
| --- | --- |
| `pushes back to '<head>' … A loop needs a 'switch'` | a `push` always fires, so it could never leave |
| `That is a loop, so it needs bounding: add 'max_loops'` | on the step that closes it |
| `has 'max_loops' but no case naming a step upstream of it` | remove it, or make the case name an upstream step |
| `max_loops must be an integer of 1 or more` | YAML reads `yes` as `True`, and a bool is an int |
| `is reached from '<head>' inside its loop but does not lead back` | push it to the closing step, or move it after the loop |
| `both re-run '<step>'` | two loops crossing. Nest them, or separate them |

A loop that **runs out** is a failure by design: it never converged. Before raising
`max_loops`, check that the writing step reads both its own previous answer and the review.
Where the prompt and a check disagree on a number, the prompt is what the model is writing to.

A **check that fails the step instead of rejecting** exited non-zero to mean "no". A verdict
goes on stdout as JSON and exits 0. There is no `gate` key: the engine reads `gate:` as no key
at all.

### Templates

| Message | Cause and fix |
| --- | --- |
| `references unknown namespace` | not `inputs`, `steps`, `secrets` or `this`. `{{ input.path }}` is the usual typo |
| `references undeclared input` | declare it under `inputs:`, or fix the name |
| `references unknown step` | a typo. Check `atf inspect flow` |
| `reads from '<x>', which is not upstream of it` | a sideways read. Add an edge, or read something both descend from |
| `uses {{ this.* }} outside its switch` | `this` exists only where that step's result does |
| `puts {{ secrets.NAME }} in an agent prompt` | it would reach the model. Let the adapter read the environment |
| `uses {{ secrets.NAME }} without declaring it` | add it to that step's `secrets` |
| `output references unknown namespace` | the output template reads `inputs` and `steps` only |
| `has an unknown tag '{% … %}'` | the four are `if`, `if not`, `else`, `endif`. Half a tag is refused too |
| `template references unknown value` | at run time: often `.json.field` on a result that is not JSON |

### Components and grants

| Message | Cause and fix |
| --- | --- |
| `is not a runnable tool spec` / `agent spec` | the location in the message is the JSON path |
| `run.command points at <x>, which does not exist` | check the tool's own directory |
| `is not executable. chmod +x it` | the file lost its mode in a copy or a zip |
| `is not a valid JSON Schema` | checked against Draft 2020-12's meta-schema |
| `is not valid JSON` | a trailing comma in a `spec.json` |
| `would be rejected by adapter <name>` | `atf inspect adapter <name>` prints what it takes |
| `unknown adapter '<name>'` | two ship: `claude_code` and `echo` |
| `has an empty system prompt` | `agent.md` is missing or blank |
| `passes <key> to <tool>, which does not accept it` | the message lists what the tool allows |
| `does not pass <key> to <tool>, which requires it` | a template counts as supplied; a missing key does not |
| `passes an invalid <key> to <tool>` | a literal of the wrong type. Templated values are not examined |
| `grants <tool>, which changes the workspace` | set `"unattended": true` to say it is intended |
| `grants <tool>, which expects a secret` | run it as a tool step instead |
| `declares secrets and runs agent '<x>', which is granted tools` | move the secret to a tool step |
| `grants both '<a>' and '<b>', which a model sees as one tool` | two names flatten onto one with `__` |
| `wants secrets but no vault is open` | set `vault:` on the flow, or pass `--vault` |

### Lookup and config

A name that does not resolve reports every path it was looked for. Read that list. Common
causes in order: the component is in the wrong directory; the namespace is part of the name
(`arctic/read_file` and `read_file` are two tools); something higher is shadowing it, which
`atf list` marks; the workspace is not what you think.

A tool in a **pack that is off** says so instead, with the line to add:

```
tool 'arctic/git/log' is in the 'git' pack, which is not enabled.
Add it to $HOME/.arctic/config.yaml:  packs: [git]
```

In `config.yaml`, an unknown key, a relative `sources` entry and an unknown pack name are all
refused, and a broken config stops every command rather than just the one you ran.

### Not an error

- **A step reported as skipped.** Its branch was not taken. It still resolves as `(not run)`.
- **A check rejecting.** A verdict, not a failure. The step fails only when the passes run out.
- **Empty stdout.** A flow with no `output` prints nothing on purpose.
- **Progress on stderr.** stdout carries the flow's output and nothing else.

## Environment

| Variable | Is |
| --- | --- |
| `ATF_PATH` | extra search roots, above everything else |
| `ATF_VAR_<NAME>` | a flow input. `--input` wins where both are set |
| `ATF_VAULT_PASSWORD` | the vault password |
| `ATF_VAULT_PASSWORD_FILE` | a file holding it, which beats `ATF_VAULT_PASSWORD` |
| `HOME` | where `~/.arctic` is found and `atf init` writes |
| `NO_COLOR` | any value switches off colour |
| `ATF_PREFIX`, `ATF_VERSION` | read by `install.sh`, since its piped form takes no arguments |

The prefix is `ATF_VAR_` and not a bare `ATF_` because `$ATF_PATH` and `$ATF_VAULT_PASSWORD`
are the engine's own: an input named `path` would have collided.

`$ATF_ROOT` is missing from that table on purpose. It appears in `atf list` output as a label
meaning "this came with the engine", and there is nothing to set. Use `$ATF_PATH` to add a root.

Nothing else in the environment reaches a step. A tool gets exactly the names its step
declared under `secrets`. The forge packs read `GITHUB_TOKEN` and `BITBUCKET_TOKEN` that way,
`github` reads `GITHUB_API_URL` for Enterprise, and `bitbucket` reads `BITBUCKET_API_URL` for a
proxy, Cloud being the only Bitbucket it speaks to.

## Glossary

| Word | Means |
| --- | --- |
| **flow** | a graph of steps, in one YAML file. Names the graph and nothing else |
| **step** | one node, running a tool or an agent once every inbound edge has arrived |
| **push** | a step declaring where its result goes next. `push: [a, b]` runs both |
| **edge** | a push or a case. Derived in reverse by the engine |
| **switch** / **case** | a step choosing one branch from its own result, and one branch of it |
| **check** | a tool step with a switch. There is no `gate` key |
| **loop** | a case naming a step already upstream. The only cycle a flow may have |
| **wave** | how deep a step is. Steps in a wave run concurrently |
| **join** | a step named by more than one place. It runs once, when all have arrived |
| **skipped** | an untaken branch's step. Resolves as `(not run)`, and is false in a guard |
| **workspace** | the project root: the top search layer, and where components run |
| **root** | one place the engine looks for components, searched in a fixed order |
| **source** | an extra root named in `config.yaml`, below your home layer |
| **pack** | first-party components shipped in the binary, switched off until named |
| **namespace** | a directory that holds components rather than being one. Part of the name |
| **bundle** | a directory holding a flow of its own name, so its prompts sit beside it |
| **component** | a tool, an agent or a flow: anything found by name rather than path |
| **adapter** | how the engine talks to a model runtime. A Python module, not a directory |
| **grant** | a tool an agent may call inside its own turn, over MCP |
| **turn** | one call to a model. What an agent step costs |
| **vault** | an encrypted file of named secrets, opened lazily |
