# The data pack

Five tools for transforming a step's result on the way to the next one: JSON, CSV and
markdown, with no model, no network and nothing written.

Ships with the engine and is **off** until `~/.arctic/config.yaml` says otherwise:

```yaml
packs:
  - data
```

`atf list` shows every pack and whether it is on. A flow naming a tool from a pack that is
off fails with the line to add, at `lint` time as well as at `run` time.

## What is in it

| Tool | Does |
| --- | --- |
| `arctic/data/json/query` | Reads one value out of a JSON document with a jq program |
| `arctic/data/csv/to_json` | Reads CSV, quoted fields and all, into JSON |
| `arctic/data/json/to_csv` | Writes an array of objects as CSV |
| `arctic/data/json/to_markdown` | Renders a document as a table, a field list, or a list |
| `arctic/data/json/merge` | Combines several documents into one, at a join |

Each has a `tool.md` beside its `spec.json`, which is what a model reads and where the
edge cases are written down.

## Why a pack of transformers at all

**Because a `switch` needs an exact value, and data arrives as a document.** The rendered
switch value is compared to each case whole, so a flow could branch on a tool's whole
report or on nothing. `json/query` is the step in between: it takes the document and
answers with one field, so the graph can decide on it.

```yaml
- id: failing
  tool: arctic/data/json/query
  input:
    data: "{{ steps.pr.text }}"
    query: '[.checks[] | select(.state == "failed")] | length'
  switch: "{{ this.text }}"
  cases:
    "0": [ship]
  default: [explain]
```

The other half is cost. Filtering a hundred rows down to four, counting failures, or
turning a table into markdown are all things a model will do if asked, slowly, for money,
and occasionally wrong. Here they are a few milliseconds of jq that cannot be wrong about
what the document said.

## Two rules the whole pack shares

**Every tool takes `data` or `path`, and exactly one of them.** `data` is the usual one and
is where a step's result goes: `data: "{{ steps.rows.text }}"`. `path` reads a file in the
workspace instead, resolved and kept inside it the way `arctic/read_file` does it.

`path` is not a convenience. `read_file` truncates at its own line limit and appends a
notice saying so, and that notice would arrive at `csv/to_json` as a row. Reading the file
here reads all of it.

`json/merge` is the exception and has neither: it takes several documents at once, so no
single path names them.

**Nothing here touches anything.** No tool writes, none reaches the network, and none
declares `secrets`. `permissions.filesystem` is `read` where a tool accepts a `path` and
`none` for `merge`, so every one of them can be granted to an agent without
`unattended: true`. That is what makes this pack different from `git`, where three of the
eight tools write and the gate exists for a reason.

## What is deliberately not in it

**No XML.** There is no XML parser in a POSIX shell, and the two ways to get one are a
`python3` the tools would then depend on or an `xmllint` that is not installed by default
on Debian, Ubuntu or Fedora. Beyond the parser, XML to JSON has no one mapping: attributes
against children, a single element against a list of one, text mixed with elements. Every
answer to that is a convention somebody has to learn. It is worth doing on purpose, with
the mapping written down, rather than as the fifth tool in a pack about JSON and CSV.

**No YAML.** The engine reads YAML for flows and specs, and a tool that also read it would
suggest a flow can transform its own configuration. If a flow needs a YAML file as data,
that is a tool of its own with a clear reason.

**Nothing truncates.** Every other reader that ships bounds its output: `read_file` stops
at `max_lines`, `grep` at `max_matches`, the git tools at `max_files`. These do not, because
half a JSON document is not a JSON document, and a table missing rows nobody counted is
worse than a large one. Narrow the data with a query instead, which is what a query is for.

**Nothing guesses a type.** A CSV field has no type, so `csv/to_json` answers with strings:
`007` stays `007`. Reading a number is `tonumber` in a query, where it is visible.

**Nothing fills a gap.** A CSV row that does not fit its header, a column that only later
rows carry, a value CSV cannot hold: each of those is refused with the row named, rather
than padded with nulls, dropped, or rendered as something. A quiet answer is the failure
that surfaces a long way from its cause.

## Changing one

`lib/data.sh` is shared by all five: the exit code vocabulary, reading the input, loading
`data` or `path`, and the sandbox `json/query` runs a program in. It sits outside `tools/`
on purpose, since the resolver walks that directory looking for `spec.json` and anything
else in there reads as an empty namespace.

Four traps it documents, and each of them cost something already:

- **`run_query`'s result must be assigned, never passed as an argument.**
  `out=$(run_query …)` propagates a failure; `f "$(run_query …)"` runs it in a subshell
  whose exit reaches nothing, so the script carries on with an empty string and exit `0`.
  The git pack's `try_git` carries the same warning for the same reason.
- **`//` is not a default.** jq's alternative operator treats `false` as absent, so
  `.header // true` answers `true` for `header: false`. `flag` uses `has` instead, and it is
  a function so that nothing has to remember.
- **A jq program from a flow runs under `env -i`.** jq's own `env` and `$ENV` read the
  process environment, so a step that declared `secrets`, or a shell that exported
  `$ATF_VAULT_PASSWORD`, would otherwise be readable from a query.
- **The sourcing line carries `# shellcheck source-path=SCRIPTDIR`**, and the gate runs
  `shellcheck -x`. Without both, the pre-push gate fails on SC1091.

The CSV parser inside `csv/to_json/run.sh` is the one real algorithm here, and it is a
character scanner rather than `cut -d,` because a quoted field can hold the delimiter, a
line break and a doubled quote. It has a fast path for a line with no quote on it, which is
almost every line of almost every file; the scanner is correct either way and several times
slower. Both paths have to agree about blank lines, CRLF endings and the field count, so a
change to one is a change to both.
