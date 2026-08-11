# data/json/merge

Several JSON documents in, one out.

## Purpose

This is the tool a join reaches for. A step downstream of a branch has two results
and a prompt can only template them one after the other, which gives a model two
blobs and no way to say which field came from where. One document with a name on
each part reads better, and a later template can take a field out of it.

## When to use it

- A join, where two branches have each produced something and the step after them
  needs both.
- Layering configuration: a default document, then an override, merged.
- Gathering several tool results into one document to hand to an agent, or to write
  to a file.

## When not to use it

- One of the parts is prose. Every part is a JSON document; an agent that answered
  in sentences goes into a prompt as text, not through here.
- The template already says it clearly. Two results in one prompt, one after the
  other under two headings, is fine and needs no step.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter     | Type   | Required | Default | Notes                                      |
| ------------- | ------ | -------- | ------- | ------------------------------------------ |
| `part_<name>` | string | at least one |     | One document. The name is what follows `part_`. |
| `strategy`    | enum   | no       | `named` | `named`, `shallow` or `deep`.              |

## Example

```sh
echo '{"part_review":"{\"verdict\":\"pass\"}","part_tests":"{\"failed\":0}"}' \
  | src/builtin/packs/data/tools/arctic/data/json/merge/run.sh
```

```json
{
  "review": {
    "verdict": "pass"
  },
  "tests": {
    "failed": 0
  }
}
```

In the flow, at the join:

```yaml
- id: summary
  tool: arctic/data/json/merge
  input:
    part_review: "{{ steps.review.text }}"
    part_tests: "{{ steps.tests.text }}"
  push: [decide]

- id: decide
  tool: arctic/data/json/query
  input:
    data: "{{ steps.summary.text }}"
    query: 'if .tests.failed > 0 then "fix" else .review.verdict end'
  switch: "{{ this.text }}"
  cases:
    fix: [explain]
    pass: [ship]
```

## Why `part_<name>` and not a `parts:` mapping

Because a mapping would not work. The engine renders the **top-level** values of a
step's `input`, so a template written inside a nested mapping arrives here as the
literal `{{ steps.review.text }}`. It lints clean, since references are read at any
depth, and then fails on a document that is a template.

So each part is its own parameter and the name is in the key. If the engine grows
nested rendering, this tool can grow the nicer spelling; until then the nicer
spelling is a trap.

## The three strategies

```
part_a: {"o": {"x": 1}, "n": 1}      part_b: {"o": {"y": 2}, "n": 9}

named    {"a": {"o": {"x": 1}, "n": 1}, "b": {"o": {"y": 2}, "n": 9}}
shallow  {"o": {"y": 2}, "n": 9}
deep     {"o": {"x": 1, "y": 2}, "n": 9}
```

`named` keeps each part whole and is the one a join wants. It is also the only one
that can carry a part which is not an object: a number, a string or an array goes
under its name unchanged.

`shallow` and `deep` merge fields, so every part has to be an object. **The parts
keep the order they were written in**, and a collision goes to the part written
last. Nothing sorts them.

## A part that did not run

The commonest failure here is a join whose other branch was skipped, and it is
named rather than left as a parse error:

```
data/json/merge: part 'review' is '(not run)', which is what a step that did not
run renders as. Guard it in the template, e.g.
"{% if steps.review %}{{ steps.review.text }}{% else %}{}{% endif %}"
```

The guard is the fix, and `{}` or `null` is the part to substitute. A conditional's
branch that is not taken is never rendered, so the reference inside it is safe.

## Errors

| Exit | Means                                                                    |
| ---- | ------------------------------------------------------------------------ |
| `2`  | no `part_<name>` was given, a part is not a string, or `strategy` is wrong. |
| `5`  | a part is not JSON, or is the result of a step that did not run.           |
| `6`  | `shallow` or `deep` was asked for and a part is not an object.             |

There is no `3` or `4` here: this is the one tool in the pack with no `path`, since
it takes several documents and no single file names them.
