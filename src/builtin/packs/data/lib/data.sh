#!/usr/bin/env bash
#
# Shared by every tool in the data pack. Sourced, never executed.
#
# A tool sets TOOL to its own name, sources this, then calls `read_input` and `load_data`:
#
#   TOOL=data/json/query
#   . "$(dirname -- "$0")/../../../../../lib/data.sh"
#
# It sits outside `tools/` on purpose: the resolver walks that directory looking for
# `spec.json`, so anything in there that is not a tool would read as an empty namespace.
#
# Exit codes, shared across the pack so one vocabulary covers all of it. A tool's
# spec.json lists only the ones it can actually produce:
#
#   0  ok
#   2  invalid input     stdin was not a JSON object, or a parameter is wrong
#   3  not found         `path` names a file that is not there
#   4  not permitted     `path` resolves outside the workspace
#   5  malformed data    the data is not the format this tool reads
#   6  refused           the transform cannot be done as asked
#   7  no result         the query matched nothing, and no `default` was given
#
# **5 and 6 are not the same failure, and keeping them apart is worth a code.** 5 is the
# data, so the step that produced it is where to look. 6 is the instruction, so the fix is
# in the flow's YAML. One code for both would send half of the readers to the wrong file.

fail() { # fail <exit-code> <message>
  printf '%s: %s\n' "${TOOL:-data}" "$2" >&2
  exit "$1"
}

# --- input --------------------------------------------------------------------

read_input() { # sets $input to the JSON object on stdin
  input=$(cat)
  jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
    || fail 2 'stdin must be a single JSON object matching spec.json'
}

# `has` rather than `//`, and that is the whole reason this is a function. jq's alternative
# operator treats false as absent, so `.header // true` answers true for `header: false` and
# the parameter would do nothing whenever it was used.
flag() { # flag <name> <default>, true when the value is true
  local value
  value=$(jq -r --arg f "$1" --argjson d "$2" 'if has($f) then .[$f] else $d end' <<<"$input")
  case "$value" in
    true | false) ;;
    *) fail 2 "parameter '$1' must be a boolean" ;;
  esac
  [[ $value == true ]]
}

# The document to transform, from wherever this call put it.
#
# Exactly one of `data` and `path`, which `input_schema` also says with a `oneOf`. Checked
# again here because a tool is documented as runnable on its own, and because the message
# names the two parameters where a schema violation names a keyword.
load_data() { # sets $data
  local given abs

  if jq -e 'has("data") and has("path")' >/dev/null 2>&1 <<<"$input"; then
    fail 2 "pass either 'data' or 'path', not both"
  fi

  if jq -e 'has("data")' >/dev/null 2>&1 <<<"$input"; then
    jq -e '.data | type == "string"' >/dev/null 2>&1 <<<"$input" \
      || fail 2 "parameter 'data' must be a string, e.g. data: \"{{ steps.fetch.text }}\""
    # `$( )` drops trailing newlines, from this and from the file below alike. Harmless for
    # every format here: a trailing newline is not a CSV record, and JSON ignores it.
    data=$(jq -j '.data' <<<"$input")
  else
    jq -e '.path | type == "string" and length > 0' >/dev/null 2>&1 <<<"$input" \
      || fail 2 "pass either 'data' or 'path'"
    given=$(jq -r '.path' <<<"$input")
    # Assigned, never passed as an argument: see the warning on `run_query`.
    abs=$(resolve_path "$given")
    data=$(cat -- "$abs")
  fi

  # A UTF-8 byte order mark is invisible and breaks both readers: jq refuses the document,
  # and a CSV's first column would be named "﻿id" rather than "id". Anything exported
  # from Excel carries one.
  data=${data#$'\xef\xbb\xbf'}

  [[ -n $data ]] || fail 5 'the data is empty'

  # What a step that did not run renders as (`SKIPPED_RESULT` in engine/executor.py).
  # Without this the answer is "the data is not JSON", which sends someone reading it to
  # the transform rather than to the branch that never ran.
  if [[ $data == '(not run)' ]]; then
    fail 5 "the data is '(not run)', which is what a step that did not run renders as. Guard this step with {% if steps.<id> %}, or reach it only from a branch that ran"
  fi
}

# A path parameter, resolved and kept inside the workspace. Same rule and the same reason
# as the built-in read_file: a path may come from a model, so canonicalise every component
# and compare against the root rather than pattern-matching the string.
resolve_path() { # resolve_path <path>, prints the absolute path
  local given=$1 abs root
  root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) \
    || fail 4 'cannot resolve the workspace root'

  # realpath -e resolves *every* component, symlinks included, and fails if any is
  # missing. Canonicalising only the parent would let a symlink inside the workspace point
  # outside it and slip past the check below.
  abs=$(realpath -e -- "$given" 2>/dev/null) || fail 3 "no such file: $given"

  case "$abs" in
    "$root"/*) ;;
    *) fail 4 "path resolves outside the workspace root: $given" ;;
  esac
  [[ -f $abs ]] || fail 4 "not a regular file: $given"
  [[ -r $abs ]] || fail 4 "not readable: $given"

  printf '%s' "$abs"
}

# --- jq -----------------------------------------------------------------------

require_json() { # the data has to parse before any of this means anything
  printf '%s' "$data" | jq empty >/dev/null 2>&1 \
    || fail 5 "the data is not JSON. jq's own message names a column in a string nobody printed, so check what the step upstream actually returned: a tool's stdout reaches this as text, whatever shape it had"
}

# A jq program of ours, over the data. One value in, one value out.
transform() { # transform <jq-args...>, prints jq's output
  local out status=0
  out=$(printf '%s' "$data" | jq "$@" 2>/dev/null) || status=$?
  (( status == 0 )) || fail 5 "the data is not shaped the way $TOOL reads it"
  printf '%s' "$out"
}

# The one place in this pack that runs a program written outside it.
#
# `env -i`, because jq's own `env` and `$ENV` read the whole environment. A step that
# declared `secrets`, or a shell that exported `$ATF_VAULT_PASSWORD`, would otherwise be
# readable by `env.ATF_VAULT_PASSWORD` in a query. It also takes `$HOME` away, so an
# `include` cannot reach a module in `~/.jq`. `$PATH` goes with it, which is why jq is
# called by the path it was found at.
#
# The program goes in a file rather than in argv: jq reads `--` as "the rest are positional
# arguments" rather than as end-of-options, so a program starting with a dash has no
# unambiguous spelling on a command line.
#
# **Assign the result, never pass it as an argument.** `out=$(run_query …)` propagates a
# failure, because `set -e` stops the script when the assignment's subshell exits non-zero.
# `something "$(run_query …)"` does not: the subshell dies, its message is printed, and the
# outer script carries on with an empty string and exit 0.
run_query() { # run_query <jq-program>, prints one compact JSON value per line
  local program=$1 jq_path dir out reason status=0

  jq_path=$(command -v jq) || fail 5 'jq is not installed, or is not on PATH'
  dir=$(mktemp -d) || fail 6 'cannot create a temporary directory'
  printf '%s' "$program" >"$dir/query.jq"

  # -c so one output is one line, which is what makes the count below exact.
  out=$(printf '%s' "$data" | env -i "$jq_path" -c -f "$dir/query.jq" 2>"$dir/stderr") \
    || status=$?

  if (( status != 0 )); then
    reason=$(grep -m 1 -v '^[[:space:]]*$' "$dir/stderr" || true)
    rm -rf "$dir"
    fail 6 "the query failed. ${reason:-jq exited $status}"
  fi
  rm -rf "$dir"

  printf '%s' "$out"
}
