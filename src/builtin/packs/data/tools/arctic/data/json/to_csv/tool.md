# data/json/to_csv

Write a JSON array of objects as CSV.

## Purpose

Get data out of a flow in the shape something else reads. A spreadsheet, an
upload, a file appended to by run after run: all of them want rows and columns,
and none of them want JSON.

## When to use it

- The end of a flow whose result is a table, going to
  `arctic/write_file`.
- Appending to a file that already exists, with `header: false`.
- Handing rows to a command outside the engine.

## When not to use it

- The reader is a prompt. `data/json/to_markdown` is easier for a model to quote a
  row of, and costs fewer tokens.
- The data is one object rather than a list of them. Wrap it in an array first, or
  render it with `to_markdown`.
- Any value is nested. Flatten it with `data/json/query` first; this refuses
  rather than choosing a rendering for you.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter   | Type     | Required     | Default | Notes                                     |
| ----------- | -------- | ------------ | ------- | ----------------------------------------- |
| `data`      | string   | one of these |         | The array, e.g. a step's result.          |
| `path`      | string   | one of these |         | A JSON file, inside the workspace.        |
| `columns`   | string[] | no           |         | Which fields, in which order.             |
| `header`    | boolean  | no           | `true`  | Write the column names first.             |
| `delimiter` | string   | no           | `,`     | One character. A tab is `"\t"`.           |

## Example

```sh
echo '{"data":"[{\"name\":\"lint\",\"state\":\"passed\"},{\"name\":\"tests\",\"state\":\"failed\"}]"}' \
  | src/builtin/packs/data/tools/arctic/data/json/to_csv/run.sh
```

```csv
name,state
lint,passed
tests,failed
```

Writing it out is the next step, and `write_file` takes the text as it stands:

```yaml
- id: csv
  tool: arctic/data/json/to_csv
  input:
    data: "{{ steps.rows.text }}"
    columns: [name, state, seconds]
  push: [save]

- id: save
  tool: arctic/write_file
  input:
    path: var/checks.csv
    content: "{{ steps.csv.text }}"
```

## The columns are a decision, so it will not guess

Without `columns` they are the keys of the **first row**. A row further down
carrying a key the first row does not is refused:

```
data/json/to_csv: row 3 has a column 'notes' that row 0 does not, and it would
leave the file without a word. Name the columns you want with 'columns'
```

That is the whole reason the check exists: the alternative is a file that is
missing a column nobody noticed was there. Naming `columns` settles it, and then a
row that *lacks* one is an empty cell, because leaving it out was a choice
somebody made rather than a shape nobody looked at.

A nested value is refused for the same reason. CSV has one level, so an array in a
cell can only be some rendering of it, and which rendering is not this tool's
decision:

```yaml
# make it text first, then write it
- id: flat
  tool: arctic/data/json/query
  input:
    data: "{{ steps.rows.text }}"
    query: '[.[] | .labels = (.labels | join(" "))]'
```

## Quoting

A cell is quoted only when it has to be: it holds the delimiter, a quote, or a
line break. Everything else is written as it is, which keeps the output readable
when it lands in a prompt or a diff. A quote inside a quoted cell is doubled,
which is the only escape CSV has.

`null` is an empty cell. A number or a boolean is written the way JSON writes it,
so `3.5` stays `3.5` and `true` stays `true`.

## Errors

| Exit | Means                                                                     |
| ---- | ------------------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, or a parameter is the wrong type.             |
| `3`  | `path` does not exist.                                                     |
| `4`  | `path` resolves outside the workspace root.                                |
| `5`  | the data is not JSON, or not an array of objects.                          |
| `6`  | a value is nested, a row carries an unnamed column, or the array is empty. |

An empty array is exit `6` only when the columns were not named: there is no first
row to take them from, and a header nobody asked for would be a guess. With
`columns` it writes the header and no rows, which is a CSV file with no rows.
