#!/usr/bin/env bash
#
# data/json/to_csv: write a JSON array of objects as CSV.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  CSV, one line per row, with a header line unless header: false
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted | 5 malformed data
#           6 refused
#
# **A column that appears halfway down is refused, not dropped.** The columns come from the
# first row, so a key that only later rows carry would silently leave the file. Naming
# `columns` is how a flow says which it wants; then a row missing one is an empty cell,
# because that is a choice somebody made rather than a shape nobody noticed.
#
# **A nested value is refused too.** CSV has one level, so an array in a cell can only be
# some rendering of it, and picking the rendering is not this tool's decision to make.
#
# Quoting is minimal: a cell is quoted when it holds the delimiter, a quote, or a line
# break, and left alone otherwise, which keeps the output readable in a prompt.

set -euo pipefail

TOOL=data/json/to_csv
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/data.sh
. "$(dirname -- "$0")/../../../../../lib/data.sh"

read_input

delimiter=$(jq -r '.delimiter // ","' <<<"$input")
[[ ${#delimiter} -eq 1 ]] || fail 2 "parameter 'delimiter' must be one character"

header=true
flag header true || header=false

columns=null
if jq -e 'has("columns")' >/dev/null 2>&1 <<<"$input"; then
  jq -e '.columns | type == "array" and length > 0 and all(type == "string" and length > 0)' \
    >/dev/null 2>&1 <<<"$input" \
    || fail 2 "parameter 'columns' must be a non-empty array of column names"
  columns=$(jq -c '.columns' <<<"$input")
fi

load_data
require_json

# --- is the document a shape CSV can hold -------------------------------------

# Each check answers with a reason or with nothing, rather than by failing, so the message a
# reader gets is this tool's and names the row.
reason=$(printf '%s' "$data" | jq -r '
  def named: if . == "object" or . == "array" then "an \(.)" else "a \(.)" end;
  def bad_row: first(to_entries[] | select(.value | type != "object")) // null;
  if type != "array" then "the data is \(type | named), and to_csv reads an array of objects"
  elif bad_row != null
  then bad_row | "row \(.key) is \(.value | type | named), and to_csv reads an array of objects"
  else "" end
')
[[ -z $reason ]] || fail 5 "$reason"

rows=$(printf '%s' "$data" | jq 'length')

if [[ $columns == null ]]; then
  if (( rows == 0 )); then
    # Nothing to derive them from, and a header nobody asked for would be a guess. This is
    # the one case where an empty array is not simply an empty answer.
    [[ $header == false ]] || fail 6 "the array is empty, so there are no columns to take from its first row. Name them with 'columns' if this is a case the flow expects"
    exit 0
  fi
  columns=$(printf '%s' "$data" | jq -c '.[0] | keys_unsorted')

  reason=$(printf '%s' "$data" | jq -r --argjson cols "$columns" '
    first(
      to_entries[] | .key as $row | (.value | keys_unsorted[]) as $key
      | select($cols | index($key) | not)
      | "row \($row) has a column '"'"'\($key)'"'"' that row 0 does not, and it would leave the file without a word. Name the columns you want with '"'"'columns'"'"'"
    ) // ""
  ')
  [[ -z $reason ]] || fail 6 "$reason"
fi

# The cell is bound before `$cols` is piped into: inside that pipe `.` is the column list,
# so a bare `.key` there would be read against the array and not against the cell.
reason=$(printf '%s' "$data" | jq -r --argjson cols "$columns" '
  first(
    to_entries[] | .key as $row | .value | to_entries[] as $cell
    | select(($cols | index($cell.key)) and ($cell.value | type == "object" or type == "array"))
    | "the value at row \($row), column '"'"'\($cell.key)'"'"' is an \($cell.value | type), and a CSV cell cannot carry one. Flatten it first, e.g. with a json/query program"
  ) // ""
')
[[ -z $reason ]] || fail 6 "$reason"

# --- write it -----------------------------------------------------------------

printf '%s' "$data" | jq -r --arg d "$delimiter" --argjson cols "$columns" \
  --argjson with_header "$header" '
  def cell:
    if . == null then ""
    elif type == "string" then .
    else tostring
    end
    | if index($d) or index("\"") or index("\n") or index("\r")
      then "\"" + gsub("\""; "\"\"") + "\""
      else .
      end;

  (if $with_header then [$cols | map(cell) | join($d)] else [] end)
  + [.[] | [.[$cols[]] | cell] | join($d)]
  | .[]
'
