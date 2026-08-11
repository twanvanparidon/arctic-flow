# data/json/query

Read one value out of a JSON document with a jq program.

## Purpose

Turn data into a decision. A tool answers with a document, a flow needs one field
out of it, and this is the step in between: it costs milliseconds, it cannot be
wrong about what the document said, and it is the only way a `switch` gets an
exact value to compare.

## When to use it

- A `switch` has to branch on a field. `switch` compares the whole rendered
  value, so it needs one word and not a report.
- A document is far larger than the question. Narrowing it before a prompt is
  cheaper than a model reading past the rest.
- Counting, filtering or summing. Nothing here needs a model, and a model asked to
  count will sometimes get it wrong.
- Reading a field out of an agent's JSON answer before another step uses it.

## When not to use it

- The value is already reachable in a template. `{{ steps.pr.json.state }}` needs
  no step at all; use this when the value has to *be* the result, which is what a
  `switch` and the next step's `input` both read.
- The data is not JSON. Read CSV with `data/csv/to_json` first.
- You want several values at once. Ask for them as one document (`{a: .x, b: .y}`)
  rather than running this twice.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type   | Required     | Notes                                              |
| --------- | ------ | ------------ | -------------------------------------------------- |
| `data`    | string | one of these | The document, usually `"{{ steps.x.text }}"`.      |
| `path`    | string | one of these | A JSON file, inside the workspace.                 |
| `query`   | string | yes          | A jq program. Must produce exactly one value.      |
| `default` | string | no           | What to answer when it matches nothing.            |

## Example

```sh
echo '{"data":"{\"state\":\"open\",\"checks\":[{\"state\":\"failed\"}]}","query":".state"}' \
  | src/builtin/packs/data/tools/arctic/data/json/query/run.sh
```

```
open
```

Which is what a branch wants:

```yaml
- id: state
  tool: arctic/data/json/query
  input:
    data: "{{ steps.pr.text }}"
    query: ".state"
  switch: "{{ this.text }}"
  cases:
    open: [review]
    merged: [note]
```

Counting, so the flow decides rather than the model:

```yaml
- id: failures
  tool: arctic/data/json/query
  input:
    data: "{{ steps.pr.text }}"
    query: '[.checks[] | select(.state == "failed")] | length'
  switch: "{{ this.text }}"
  cases:
    "0": [approve_note]
  default: [explain]
```

## A string prints as itself, everything else as JSON

`"open"` comes out as `open`, with no quotes, because that is what a `switch`
compares and what a prompt should carry. A number, a boolean, an object or an
array comes out as JSON, so the next step reads it as `.json`.

**So a string result is not a JSON document**, and a step that hands its result
to `json/merge` or another JSON reader has to say so. jq already spells that:

```yaml
query: '.title'                    # → Fix the loader          (for a switch, a prompt)
query: '.title | tojson'           # → "Fix the loader"        (for another JSON step)
query: '[.checks[].name]'          # → ["lint", "tests"]       (already JSON)
```

## One value, never a stream

jq programs emit streams. `.checks[]` over three checks is three values, and
three JSON documents on stdout parse as none, so it is refused with the fix in
the message rather than run together into something that looks like an answer:

```
$ … '{"data":"{\"checks\":[1,2,3]}","query":".checks[]"}'
data/json/query: the query produced 3 values and a step's result is one. Wrap the
program in [ ] to return them as one array, or narrow it with first(…)
```

## Nothing to answer with

A field that is not there is exit `7`, not an empty answer. That is the engine's
own rule for `{{ steps.x.json.field }}`, and the reason is the same: a transform
that quietly answered `""` would hand the next step a wrong value instead of
stopping.

Where absence is a real case, name what it means:

```yaml
- id: label
  tool: arctic/data/json/query
  input:
    data: "{{ steps.pr.text }}"
    query: ".labels[0]"
    default: "none"
  switch: "{{ this.text }}"
  cases:
    none: [ask_for_one]
    urgent: [page]
```

`default` covers both shapes of nothing: a query that read a field which is not
there, and one whose `select` matched no element.

## A query cannot read the environment

The program runs with an empty environment, so `env.GITHUB_TOKEN` and `$ENV`
answer nothing. A jq program is the one thing here that comes from outside the
pack, and a step that declared `secrets`, or a shell that exported
`$ATF_VAULT_PASSWORD`, would otherwise be readable through it.

It also means `include` cannot reach a module in `~/.jq`. A query is the program,
whole.

## Errors

| Exit | Means                                                                     |
| ---- | ------------------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, or a parameter is missing or the wrong type.  |
| `3`  | `path` does not exist.                                                     |
| `4`  | `path` resolves outside the workspace root.                                |
| `5`  | the data is not JSON.                                                      |
| `6`  | the query does not compile, failed on this document, or produced a stream. |
| `7`  | it matched nothing, and no `default` was given.                            |

`5` and `6` are kept apart on purpose. `5` is the data, so the step that produced
it is where to look. `6` is the query, so the fix is in the flow's YAML.
