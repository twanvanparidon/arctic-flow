# Templates

A template is `{{ dotted.path }}`, resolved when the step runs. An unresolvable path is an
error, never an empty string.

## The four namespaces

| Namespace | Is | Legal in |
| --- | --- | --- |
| `inputs` | what the caller supplied | anywhere |
| `steps` | a step's result | anywhere, for a step transitively upstream |
| `secrets` | a granted secret's value | a tool's `input` |
| `this` | the step's own result | a `switch` expression |

Anything else is refused as an unknown namespace, so a typo like `{{ input.path }}` is
caught at lint time rather than at run time.

## A step's result

Every result has two fields:

```
{{ steps.read_target.text }}        the result as text
{{ steps.triage.json.verdict }}     a field out of it, when the result parses as JSON
```

`.json` is the parsed result, or nothing when the output is not JSON. A tool's stdout is
parsed if it can be. An agent's answer is parsed the same way, which is why an agent that
gets switched on should declare an `output_schema` in its `spec.json`: the adapter then
asks the model for a document matching it, and `.json.verdict` is a field rather than a
guess.

`{{ steps.x.json.field }}` fails when the result is not JSON, and fails before `x` has run.

## Upstream only

A step may read a step that is transitively upstream of it. Reading sideways is refused:

```
step 'report' reads from 'risk_scan', which is not upstream of it.
'risk_scan' may not have run when 'report' does
```

The fix is an edge. Have the step you want to read push to the step that wants to read it,
or read something they both descend from.

A loop makes a step its own ancestor, so inside a loop a step may read itself and may read
a step further down the body. That permission arrives with the loop and goes away with it.

## `(not run)`

A skipped step resolves to the literal `(not run)`, so a prompt downstream of a branch can
mention the gap instead of failing:

```yaml
prompt: |
  The risk scan, or "(not run)" if triage found nothing to scan:
  {{ steps.risk_scan.text }}
```

The same literal is what a loop body step reads on its first pass, before anything upstream
of it in the loop has produced an answer.

It applies to `.text` only. There is nothing to reach into for `.json.field`, so a first
pass or a skipped branch reads the prose, not a field.

## `this`

`this` is the result the step just produced. It exists only where that result already does,
which is the step's own `switch`:

```yaml
- id: check
  tool: word_limit
  input:
    text: "{{ steps.draft.text }}"    # `steps`, because draft is another step
    max_words: 60
  switch: "{{ this.json.verdict }}"   # `this`, because it is this step's own answer
  max_loops: 3
  cases:
    approved: []
    rejected: [draft]
```

`{{ this.* }}` anywhere else is refused, a tool's own `input` included: the input is built
before the tool runs, so there is no result to read yet.

What a check said reaches the next pass through `steps`, like any other step's result:

```
{% if steps.check %}
Rejected: {{ steps.check.json.reason }}

It said:

{{ steps.draft.text }}

Write it again, inside the limit.
{% endif %}
```

## Secrets in a template

`{{ secrets.NAME }}` resolves only for a name the step declared in its own `secrets` list,
and only in a tool's input:

```yaml
- id: sign
  tool: hmac_sign
  secrets: [signing_key]
  input:
    payload: "{{ steps.read_artifact.text }}"
    key: "{{ secrets.signing_key }}"
```

Two refusals to expect:

- A secret anywhere on an **agent step**, its `prompt` and its `switch` included. It would
  reach the model and stay in the session.
- A secret the step did not declare, so what a step can read stays visible where the step
  is defined.

A secret's value is scrubbed from errors and traces. It is **not** scrubbed from a step's
result, so never template one into something that ends up in `output`.

## Where templates are resolved

- A tool step's `input`, value by value.
- An agent step's `prompt`, whether written inline or read from `prompt_file`.
- A `switch` expression, on a tool step or an agent step.
- The flow's `output.template`, which may read `inputs` and `steps` only.

The output template is checked separately and more narrowly: `this` and `secrets` are both
unknown namespaces there.
