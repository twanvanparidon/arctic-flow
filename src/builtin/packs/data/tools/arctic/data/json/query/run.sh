#!/usr/bin/env bash
#
# data/json/query: read one value out of a JSON document with a jq program.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the value: a string as itself, anything else as JSON
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted | 5 malformed data
#           6 refused | 7 no result
#
# **One value, never a stream.** jq programs emit streams, and a step's result is one
# value: `.items[]` over three items would leave three JSON documents on stdout, which
# parses as none. So a stream is refused with the fix in the message rather than
# concatenated into something that looks like an answer.
#
# **A missing field is a failure, not an empty string.** That is the engine's own rule for
# `{{ steps.x.json.field }}`, and a transform that quietly answered "" would hand the next
# step a wrong value instead of stopping. `default` is how a flow says absence is expected.

set -euo pipefail

TOOL=data/json/query
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/data.sh
. "$(dirname -- "$0")/../../../../../lib/data.sh"

read_input

# The parameters before the data, so a call that is wrong about both is told about the one
# it can fix without looking at another step.
jq -e '.query | type == "string" and (gsub("\\s"; "") | length) > 0' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'query' must be a non-empty jq program, e.g. \".verdict\""
query=$(jq -r '.query' <<<"$input")

has_default=false
if jq -e 'has("default")' >/dev/null 2>&1 <<<"$input"; then
  jq -e '.default | type == "string"' >/dev/null 2>&1 <<<"$input" \
    || fail 2 "parameter 'default' must be a string"
  default=$(jq -j '.default' <<<"$input")
  has_default=true
fi

load_data
require_json

# Answer with the default, or say what was missing and how to allow it.
nothing() { # nothing <reason>
  if [[ $has_default == true ]]; then
    printf '%s\n' "$default"
    exit 0
  fi
  fail 7 "$1. Set 'default' to answer with a value instead when that is a case the flow expects"
}

out=$(run_query "$query")

# An empty stream is a different thing from a null, and both mean the flow asked for
# something the document does not have. They are reported apart because the queries that
# cause them are different: a `select` that matched nothing, against a field that is absent.
if [[ -z $out ]]; then
  nothing "the query matched nothing"
fi

lines=$(($(printf '%s\n' "$out" | wc -l)))
if (( lines > 1 )); then
  fail 6 "the query produced $lines values and a step's result is one. Wrap the program in [ ] to return them as one array, or narrow it with first(…)"
fi

if [[ $out == null ]]; then
  nothing 'the query read a field that is not there'
fi

# `jq -r .` is what turns the compact value back into the answer: a string prints as itself
# so a `switch` can compare it, and anything else prints as JSON the next step reads as
# `.json`. Ours, over a value jq itself just wrote, so nothing here needs the sandbox.
printf '%s' "$out" | jq -r .
