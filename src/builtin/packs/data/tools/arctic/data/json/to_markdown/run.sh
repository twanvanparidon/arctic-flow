#!/usr/bin/env bash
#
# data/json/to_markdown: render a JSON document as markdown, for a prompt to carry.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  a markdown table, or a list, depending on the shape of the data
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted | 5 malformed data
#           6 refused
#
# The shape of the data picks the rendering, because there is only one sensible one for
# each: an array of objects is a table, an object is its fields down the page, an array of
# scalars is a list. Anything else is refused rather than rendered as something.
#
# **This is the one tool in the pack whose output is for reading.** A nested value goes into
# its cell as compact JSON instead of being refused the way `json/to_csv` refuses it: a
# table that shows `["a","b"]` has still shown it, and nothing downstream parses this back.

set -euo pipefail

TOOL=data/json/to_markdown
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/data.sh
. "$(dirname -- "$0")/../../../../../lib/data.sh"

read_input

columns=null
if jq -e 'has("columns")' >/dev/null 2>&1 <<<"$input"; then
  jq -e '.columns | type == "array" and length > 0 and all(type == "string" and length > 0)' \
    >/dev/null 2>&1 <<<"$input" \
    || fail 2 "parameter 'columns' must be a non-empty array of column names"
  columns=$(jq -c '.columns' <<<"$input")
fi

load_data
require_json

# One name per rendering, so the dispatch below is a case statement over an answer jq gave
# rather than a chain of tests in shell.
shape=$(printf '%s' "$data" | jq -r '
  if type == "array" then
    if length == 0 then "empty"
    elif all(.[]; type == "object") then "objects"
    elif all(.[]; type == "object" or type == "array" | not) then "scalars"
    else "mixed"
    end
  else type
  end
')

# Shared by all three renderings. A pipe would end the cell early and a line break would end
# the row, so both are neutralised; everything else markdown treats as text.
cell='
  def cell:
    if . == null then ""
    elif type == "string" then .
    elif type == "number" or type == "boolean" then tostring
    else tojson
    end
    | gsub("\\|"; "\\|")
    | gsub("\r?\n"; "<br>");
'

case "$shape" in
  objects | empty)
    if [[ $columns == null ]]; then
      [[ $shape == objects ]] \
        || fail 6 "the array is empty, so there are no columns to take from its first row. Name them with 'columns' if this is a case the flow expects"
      columns=$(printf '%s' "$data" | jq -c '.[0] | keys_unsorted')

      # Same rule and the same reason as json/to_csv: a key that only later rows carry
      # would leave the table without a word, and which columns to show is a decision.
      reason=$(printf '%s' "$data" | jq -r --argjson cols "$columns" '
        first(
          to_entries[] | .key as $row | (.value | keys_unsorted[]) as $key
          | select($cols | index($key) | not)
          | "row \($row) has a column '"'"'\($key)'"'"' that row 0 does not, and it would leave the table without a word. Name the columns you want with '"'"'columns'"'"'"
        ) // ""
      ')
      [[ -z $reason ]] || fail 6 "$reason"
    fi

    printf '%s' "$data" | jq -r --argjson cols "$columns" "$cell"'
      ($cols | map(cell)) as $head
      | ["| " + ($head | join(" | ")) + " |",
         "| " + ($head | map("---") | join(" | ")) + " |"]
        + [.[] | "| " + ([.[$cols[]] | cell] | join(" | ")) + " |"]
      | .[]
    '
    ;;

  object)
    [[ $columns == null ]] \
      || fail 2 "parameter 'columns' names the columns of a table, and the data is one object. Drop it, or wrap the object in an array"
    printf '%s' "$data" | jq -r "$cell"'
      ["| field | value |", "| --- | --- |"]
      + [to_entries[] | "| " + (.key | cell) + " | " + (.value | cell) + " |"]
      | .[]
    '
    ;;

  scalars)
    [[ $columns == null ]] \
      || fail 2 "parameter 'columns' names the columns of a table, and the data is a list of values"
    printf '%s' "$data" | jq -r "$cell"'.[] | "- " + cell'
    ;;

  mixed)
    fail 5 "the array holds more than one kind of thing, so there is no one rendering for it. Make it an array of objects with the same keys, or a list of values, e.g. with a json/query program"
    ;;

  *)
    fail 5 "the data is a $shape, and to_markdown renders an array of objects, an object, or a list of values"
    ;;
esac
