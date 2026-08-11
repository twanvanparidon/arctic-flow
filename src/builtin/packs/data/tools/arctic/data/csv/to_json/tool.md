# data/csv/to_json

Read CSV and answer with JSON.

## Purpose

Make a table something a flow can work with. As CSV it is text a prompt can only
carry whole; as JSON it can be filtered, counted, branched on, and read row by
row by every other tool in this pack.

## When to use it

- A report, an export or a `csv` endpoint is the input to a flow.
- A model has to read a table. It answers about JSON far more reliably than about
  a wall of commas, and `data/json/to_markdown` afterwards is better still.
- You need one number out of a table: read it here, count it with
  `data/json/query`, and branch.

## When not to use it

- The file is a spreadsheet (`.xlsx`) rather than CSV. Export it first.
- You only need to know whether the file has any rows at all. `arctic/grep` is
  cheaper.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter   | Type    | Required     | Default | Notes                                        |
| ----------- | ------- | ------------ | ------- | -------------------------------------------- |
| `data`      | string  | one of these |         | The CSV itself, e.g. a step's result.        |
| `path`      | string  | one of these |         | A CSV file, inside the workspace.            |
| `header`    | boolean | no           | `true`  | First row names the columns.                 |
| `delimiter` | string  | no           | `,`     | One character. A tab is `"\t"`.              |

## Example

```sh
printf 'name,state\nlint,passed\ntests,failed\n' > checks.csv
echo '{"path":"checks.csv"}' \
  | src/builtin/packs/data/tools/arctic/data/csv/to_json/run.sh
```

```json
[
  {
    "name": "lint",
    "state": "passed"
  },
  {
    "name": "tests",
    "state": "failed"
  }
]
```

Then the question the flow actually had:

```yaml
- id: rows
  tool: arctic/data/csv/to_json
  input:
    path: checks.csv
  push: [failing]

- id: failing
  tool: arctic/data/json/query
  input:
    data: "{{ steps.rows.text }}"
    query: '[.[] | select(.state == "failed")] | length'
  switch: "{{ this.text }}"
  cases:
    "0": [ship]
  default: [explain]
```

## Prefer `path` over reading the file first

`arctic/read_file` truncates at `max_lines` and appends a notice saying so. That
notice would arrive here as a row, and a truncated file is a table missing rows
nobody counted. `path` reads the whole file, so a CSV of any size arrives whole.

## Every value is a string

A CSV field has no type. `007` is not `7`, and a column of postcodes that lost its
leading zeros is a bug that surfaces a long way from here. So the JSON carries
strings, and arithmetic asks for a number where it needs one:

```yaml
query: '[.[] | (.seconds | tonumber)] | add'
```

## What it handles, and what it refuses

A quoted field may hold the delimiter, a line break, or a doubled quote, and all
three survive a round trip through `data/json/to_csv` byte for byte:

```csv
id,note
1,"a,b"
2,"say ""hi"""
3,"one
two"
```

A quote that is not at the start of a field is a literal, which is what a
spreadsheet writing `6" pipe` into an unquoted column produces. CRLF line endings
become `\n`. A UTF-8 byte order mark is dropped, so the first column is `id` and
not `﻿id`. A blank line is skipped rather than read as a row holding one empty
field.

Three things are data errors and stop the read:

- **A row that does not fit the header.** Padding it with nulls would answer a
  question the file cannot answer. The message names the line.
- **A header naming one column twice**, since one JSON object cannot carry both.
- **A quoted field that is never closed.** The message names the line it opens on.

## Errors

| Exit | Means                                                                    |
| ---- | ------------------------------------------------------------------------ |
| `2`  | stdin was not a JSON object, or a parameter is the wrong type.            |
| `3`  | `path` does not exist.                                                    |
| `4`  | `path` resolves outside the workspace root.                               |
| `5`  | the CSV is malformed: a ragged row, an unclosed quote, or a bad header.   |

```
$ printf 'id,name\n1,pen\n2\n' | … to_json/run.sh
data/csv/to_json: line 3 has 1 field where the rest have 2. A row that does not
fit is a data error, not a row of nulls
```
