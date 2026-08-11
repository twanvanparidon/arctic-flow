# What a refusal means

`lint` and `run` perform the same checks, so everything here can be reached by either. The
engine prefers failing loudly to doing something plausible: each of these could have been a
default, a guess or a silent no-op.

Match on the identifying phrase. The exact wording may improve between versions.

## Shape of the flow

| Message | Cause | Fix |
| --- | --- | --- |
| `flow is missing required field` | no `flow`, `start` or `steps` key | add it; `start` names the one entry step |
| `'steps' must be a non-empty list` | `steps:` is a mapping, or empty | a YAML list, one item per step |
| `flow file must contain a YAML mapping` | the file parses as a list or a scalar | the top level is `flow:`, `start:`, `steps:` |
| `every step needs an 'id'` | a list item with no `id` | give it one, unique in the flow |
| `duplicate step id` | two steps share an id | rename one; ids are how everything refers to a step |
| `must set exactly one of 'tool' or 'agent'` | both, or neither | a step runs one component |
| `agent step '<id>' needs a 'prompt'` | an agent step with no prompt | the prompt is the turn |
| `'output' must be a mapping with a 'template' key` | `output: "{{ ... }}"` | write `output:` then `  template: ...` |

## Edges

| Message | Cause | Fix |
| --- | --- | --- |
| `pushes to unknown step` | a typo in `push` or a case | check the id against `atf inspect flow` |
| `pushes to itself` | `push: [own_id]` | a step cannot hand to itself; put a check between it and itself, and loop through that |
| `is unreachable: nothing pushes to it` | the step was written but never wired | add it to a `push` or a case, or delete it |
| `sets both 'push' and 'switch'` | both keys on one step | a step hands on unconditionally or chooses one branch |
| `steps form a cycle nothing enters` | a ring of steps no walk from `start` reaches | wire it to the graph, or delete it |

## Branches

| Message | Cause | Fix |
| --- | --- | --- |
| `has 'cases' or 'default' but no 'switch'` | cases without the expression that selects them | add `switch:`, or use `push:` |
| `needs a 'switch' expression` | `switch:` is empty or not a string | e.g. `switch: "{{ this.json.verdict }}"` |
| `has a switch but no 'cases'` | `cases:` missing or empty | at least one case |
| `case key ... is not a string` | YAML read a bare `yes`/`no`/`on`/`off`/`true`/`false` as a boolean | quote it: `"yes":` |
| `case '<key>' must be a list of step ids` | a case whose value is a string | `rejected: [write]`, and `[]` to end the path |
| `default must be a list of step ids` | same, for `default` | as above |

A **run-time** branch failure is different: the rendered value matched no case and there
was no `default`. Print what the step returns, add a `default`, or give the agent an
`output_schema` so the field is a fixed set.

## Loops

| Message | Cause | Fix |
| --- | --- | --- |
| `pushes back to '<head>', which is upstream of it. A loop needs a 'switch'` | a `push` closes the cycle | a `push` always fires, so it could never leave; use a `switch` |
| `That is a loop, so it needs bounding: add 'max_loops'` | an unbounded cycle | `max_loops: <n>` on the step that closes it |
| `has 'max_loops' but no case naming a step upstream of it` | `max_loops` where nothing loops | remove it, or make the case name an upstream step |
| `max_loops must be an integer of 1 or more` | `max_loops: yes`, or a non-integer | YAML reads `yes` as `True` and a bool is an int |
| `is reached from '<head>' inside its loop but does not lead back` | a stranded step in the loop body | have it push to the closing step, or move it after the loop |
| `both re-run '<step>'. Nested and overlapping loops are not supported` | two loops sharing a step | which one's count a pass resets would be undefined |

A loop that **runs out** at run time is a failure by design: it never converged. Before
raising `max_loops`, check that the writing step reads both its own previous answer and the
review. Without that, each pass starts from the inputs and breaks what already passed.

## Checks

There is no `gate` key, so a flow written against one has to be rewritten as a tool step
with a `switch` and a case naming the step that produced the work. The engine reads `gate:`
as no key at all.

| Symptom | Cause | Fix |
| --- | --- | --- |
| the check fails the step instead of rejecting | the tool exits non-zero to mean "no" | answer on stdout and exit 0; a non-zero exit means the tool could not answer |
| `switched on '<value>', which matches no case` | the tool's answer is prose, or a field that is not the verdict | answer in JSON and switch on `{{ this.json.verdict }}` |
| `template references unknown value {{ this.json.verdict }}` | the tool's stdout is not JSON | `.json` is `null` unless stdout parses; check it by hand |
| `max_loops but no case naming a step upstream` | the reject case names a step that is not upstream | the check has to loop back to what produced the work |

A check that **runs out** fails the step. Where the prompt and the check disagree, the prompt
is what the model is writing to, so make the two numbers agree before raising `max_loops`.

## Templates

| Message | Cause | Fix |
| --- | --- | --- |
| `references unknown namespace` | a root that is not `inputs`, `steps`, `secrets` or `this` | `{{ input.path }}` is the usual typo |
| `references undeclared input` | the flow has no such `inputs:` entry | declare it, or fix the name |
| `references unknown step` | a typo in a step id | check `atf inspect flow` |
| `reads from '<x>', which is not upstream of it` | a sideways read | add an edge, or read something both descend from |
| `uses {{ this.* }} outside its switch` | `this` in a prompt or a tool input | `this` is the step's own result, so it exists only where that result does |
| `puts {{ secrets.NAME }} in an agent prompt` | a secret templated anywhere on an agent step | declare it in `secrets` and let the adapter read the environment |
| `uses {{ secrets.NAME }} without declaring it` | the step has no such name in its `secrets` | add it, so what a step can read is visible where the step is defined |
| `output references unknown namespace` | `this` or `secrets` in `output.template` | the output template reads `inputs` and `steps` only |
| `template references unknown value` | raised at run time | the path did not resolve; often `.json.field` on a result that is not JSON |

## Components

| Message | Cause | Fix |
| --- | --- | --- |
| `is not a runnable tool spec` / `agent spec` | a required key is missing or mistyped | the location in the message is the JSON path |
| `run.command points at <x>, which does not exist` | the script was never committed, or the name is wrong | check the tool's own directory |
| `is not executable. chmod +x it` | the file lost its mode in a copy or a zip | `chmod +x` the script |
| `is not a valid JSON Schema` | a typo such as `"type": "objekt"` | checked against Draft 2020-12's meta-schema |
| `would be rejected by adapter <name>` | a setting the adapter does not accept | `atf inspect adapter <name>` prints what it takes |
| `unknown adapter '<name>'` | a typo, or an adapter that does not ship | two ship: `claude_code` and `echo` |
| `has an empty system prompt` | `agent.md` is missing or blank | the prompt is the whole of what an agent is |
| `is not valid JSON` | a trailing comma in a `spec.json` | JSON has no trailing commas and no comments |

### The step's input against the tool

| Message | Cause | Fix |
| --- | --- | --- |
| `passes <key> to <tool>, which does not accept it` | a key not in `input_schema` | the message lists what the tool allows |
| `does not pass <key> to <tool>, which requires it` | a required key left out | a template counts as supplied; a missing key does not |
| `passes an invalid <key> to <tool>` | a literal of the wrong type, e.g. `max_lines: many` | templated values are not examined, because a lint that guesses is one people switch off |

## Grants and secrets

| Message | Cause | Fix |
| --- | --- | --- |
| `grants <tool>, which changes the workspace` | a granted tool with `filesystem: "write"` | set `"unattended": true` on the agent spec to say it is intended |
| `grants <tool>, which expects a secret` | a granted tool declaring `secrets` | run it as a tool step, which declares its own `secrets` |
| `declares secrets and runs agent '<x>', which is granted tools` | the same rule from the other side | move the secret to a tool step |
| `grants both '<a>' and '<b>', which a model sees as one tool` | two names flatten onto one with `__` | rename one |
| `secrets must be a list of names` / `lists a secret more than once` | shape of the `secrets` key | a flat list of unique strings |
| `wants secrets but no vault is open` | no `vault:` on the flow and no `--vault` | set one, or pass the file |

## Lookup

A name that does not resolve reports every path it was looked for. Read that list: it
answers which root was searched and in what order.

```sh
atf list
```

Common causes, in the order to check them:

1. The component is in the wrong directory. Tools go in `tools/`, agents in `agents/`,
   flows in `flows/`, under the workspace root or under `./.arctic`.
2. The namespace is part of the name. `arctic/read_file` and `read_file` are two tools.
3. Something higher in the search order is shadowing it. `list` marks that.
4. The workspace is not what you think. `--workspace DIR` goes before the subcommand.

`check_name` refuses a name whose segments would leave the root: `..`, an absolute path, or
an empty segment. That check covers `run`, a grant and `mcp-serve` alike.

A tool that is in a **pack** reports that instead, because every root really was searched
and the fix is a config file rather than a directory:

```
tool 'arctic/git/log' is in the 'git' pack, which is not enabled.
Add it to $HOME/.arctic/config.yaml:  packs: [git]
```

`lint` says the same thing, so this arrives before a flow spends anything. A `packs:` entry
that is not a pack that shipped is refused by name, and the refusal lists the ones there
are.

## Not an error

- **A step reported as skipped.** A branch that is not taken has its edges marked skipped,
  and skipping propagates. The step still resolves in templates, as `(not run)`.
- **A check rejecting.** It is a verdict, not a failure: the tool exits 0 and the flow takes
  the branch that goes back. The step fails only when the passes run out.
- **Progress and the output frame on stderr.** Stdout carries the flow's output and nothing
  else, so `atf run f > out.md` gives the result byte for byte.
