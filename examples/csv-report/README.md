# csv-report

One CSV of check results, three flows over it, and no model anywhere. Deterministic, free,
offline.

## Setup

This is the one example that needs something switched on. Every tool it uses ships with the
engine, but they are in the `data` pack, and a pack does nothing until you say so:

```yaml
# ~/.arctic/config.yaml
packs:
  - data
```

`atf list` will then show them. Without it, `lint` names the pack and the line to add.

## Run it

```sh
atf --workspace examples/csv-report run report
```

```markdown
# Check report

| name | state | seconds |
| --- | --- | --- |
| lint | passed | 4 |
| unit tests | failed | 91 |
| integration | failed | 240 |
| build | passed | 1200 |
| docs | passed | 12 |

## Failures, slowest first

| name | seconds | notes |
| --- | --- | --- |
| integration | 240 | timed out, then "connection reset" |
| unit tests | 91 | 3 assertions in loader_test.py |
```

Edit `checks.csv` so nothing says `failed`, and the same flow answers with the other half of
the report instead. Two steps are skipped, and the run says so.

```
⤼ failures       skipped, its branch was not taken
⤼ table          skipped, its branch was not taken
```

## Three flows, one file

| Flow | Answers with | For |
| --- | --- | --- |
| `report` | markdown | a person, or a prompt |
| `summary` | one JSON document | whatever runs next |
| `failures_csv` | CSV | a file the next job reads |

```sh
atf --workspace examples/csv-report run summary
```

```json
{
  "passed": 3,
  "failed": 2,
  "slowest": "build"
}
```

```sh
atf --workspace examples/csv-report run failures_csv > failures.csv
```

stdout carries the flow's output and nothing else, so the redirect is exact: progress and the
output frame go to stderr.

## What to look at

**`report`'s `failing` step is why the pack exists.** A `switch` compares the whole rendered
value to each case, so without a step in between a flow can branch on a tool's entire report
or on nothing at all. `arctic/data/json/query` turns the document into one value, and the
graph gets a decision:

```yaml
- id: failing
  tool: arctic/data/json/query
  input:
    data: "{{ steps.rows.text }}"
    query: '[.[] | select(.state == "failed")] | length'
  switch: "{{ this.text }}"
  cases:
    "0": [slowest]
  default: [failures]
```

Counting five rows costs about 40ms here. Asking a model to count them costs a turn, and it
will occasionally be wrong.

**`summary`'s `slowest` step ends in `| tojson`, and that is not decoration.** A query
answering with a string prints it *without quotes*, which is what a `switch` compares and what
a prompt should carry. So `build` would reach `json/merge` as a document that is not one.
`tojson` makes it `"build"`. It is the one rule in the pack worth reading twice.

**`failures_csv` names its `columns`.** Without them they come from the first row, and an
empty array has no first row, so a header nobody asked for would be a guess. Named, a run
where nothing failed answers with the header and no rows, which is a CSV file with no rows in
it rather than a failure.

**`checks.csv` is deliberately awkward.** One field holds a comma, one holds doubled quotes,
one is empty. They survive `csv/to_json` and come back out of `json/to_csv` quoted exactly as
they went in.

## Where the model would go

Nothing here calls one, and that is the point: the deterministic part is done before anything
is paid for. The report is already what a prompt wants, so an agent step reads it rather than
the raw file:

```yaml
- id: explain
  agent: reviewer
  prompt: |
    These checks failed. Say what to look at first and why.

    {{ steps.table.text }}
```

A table costs fewer tokens than the same rows as JSON, and a model quoting "the integration
row" is quoting something that exists on one line.
