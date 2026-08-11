# data/json/to_markdown

Render a JSON document as markdown, for a prompt to carry.

## Purpose

Give a model a table instead of a document. The same rows as markdown cost fewer
tokens than as JSON, and a model quoting "the tests row" is quoting something that
exists on one line rather than reassembling it from six.

## When to use it

- Anything going into a prompt that a model has to read across: rows to compare,
  fields to check, a list to work through.
- A summary a person will read, in a comment or a file.
- After `data/json/query` has narrowed a large document to the part worth showing.

## When not to use it

- The next reader is a tool. Markdown is for reading; keep JSON between steps.
- A cell holds something long. A table with a paragraph in it is worse than the
  JSON was; render the fields separately.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type     | Required     | Notes                                            |
| --------- | -------- | ------------ | ------------------------------------------------ |
| `data`    | string   | one of these | The document, e.g. a step's result.              |
| `path`    | string   | one of these | A JSON file, inside the workspace.               |
| `columns` | string[] | no           | Which fields, in which order. Tables only.       |

## The shape picks the rendering

There is one sensible rendering for each, so nothing has to be chosen:

An array of objects is a table.

```sh
echo '{"data":"[{\"name\":\"lint\",\"state\":\"passed\"},{\"name\":\"tests\",\"state\":\"failed\"}]"}' \
  | src/builtin/packs/data/tools/arctic/data/json/to_markdown/run.sh
```

```markdown
| name | state |
| --- | --- |
| lint | passed |
| tests | failed |
```

An object is its fields down the page, which is what a status document wants:

```markdown
| field | value |
| --- | --- |
| state | open |
| mergeable |  |
```

An array of values is a list:

```markdown
- lint
- tests
```

Anything else is refused: a bare number has no rendering worth having, and an
array mixing objects and values has no one rendering at all.

## In a prompt

```yaml
- id: table
  tool: arctic/data/json/to_markdown
  input:
    data: "{{ steps.rows.text }}"
    columns: [name, state, seconds]
  push: [explain]

- id: explain
  agent: reviewer
  prompt: |
    These checks ran on the branch. Say which need attention and why.

    {{ steps.table.text }}
```

`columns` is what makes a wide document into a table worth reading. Naming three
fields out of twenty is the point of it, and the order is the order they appear in.

## Cells

A row is always one line, so a line break in a value becomes `<br>` and a pipe is
escaped. `null` is an empty cell.

A nested value goes in as compact JSON rather than being refused, which is where
this differs from `data/json/to_csv`. Nothing parses markdown back, and a table
showing `["a","b"]` has still shown it, so the reason to refuse is not there.

Where columns are derived from the first row, a row carrying a key the first row
does not is refused, exactly as in `to_csv`: a column that quietly left the table
is worse than a failure that names it. `columns` settles it.

## Errors

| Exit | Means                                                                     |
| ---- | ------------------------------------------------------------------------- |
| `2`  | a parameter is the wrong type, or `columns` was passed for a non-table.     |
| `3`  | `path` does not exist.                                                     |
| `4`  | `path` resolves outside the workspace root.                                |
| `5`  | the data is not JSON, or is a shape with no rendering.                     |
| `6`  | a row carries an unnamed column, or the array is empty and none were named. |

An empty array with `columns` is a header and no rows, and exits 0. Without them
there is no first row to take the columns from, so it is exit `6`.
