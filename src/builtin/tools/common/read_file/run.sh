#!/usr/bin/env bash
#
# read_file: return the contents of a text file from the workspace.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the file's contents, plus a truncation notice if it was cut off
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted
#
# The engine runs this with cwd set to the workspace root, so relative paths in
# the input resolve the way the model expects.

set -euo pipefail

fail() { # fail <exit-code> <message>
  printf 'read_file: %s\n' "$2" >&2
  exit "$1"
}

input=$(cat)

# --- parse and validate input -------------------------------------------------

jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'stdin must be a single JSON object matching spec.json'

path=$(jq -re '.path // empty' <<<"$input") \
  || fail 2 "missing required parameter 'path'"
[[ -n $path ]] || fail 2 "parameter 'path' must be a non-empty string"

jq -e '(.max_lines // 500) | type == "number" and . >= 1 and . == floor' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'max_lines' must be an integer >= 1"
max_lines=$(jq -r '.max_lines // 500' <<<"$input")

# --- resolve the path and keep it inside the workspace -----------------------
#
# A model-supplied path is untrusted input: "../../etc/passwd" and a symlink
# pointing out of the tree both have to be rejected, so resolve to a canonical
# absolute path first and compare against the root rather than pattern-matching
# the raw string.

root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) \
  || fail 4 'cannot resolve the workspace root'

# realpath -e resolves *every* component, symlinks included, and fails if any of
# them is missing. Canonicalising only the parent directory would let a symlink
# inside the workspace point outside it and slip past the check below.
abs=$(realpath -e -- "$path" 2>/dev/null) \
  || fail 3 "no such file: $path"

case "$abs" in
  "$root"/*) ;;
  *) fail 4 "path resolves outside the workspace root: $path" ;;
esac

[[ -f $abs ]] || fail 4 "not a regular file: $path"
[[ -r $abs ]] || fail 4 "not readable: $path"

# --- emit the contents -------------------------------------------------------

# Read via redirection rather than passing $abs as an argument: awk would treat a
# filename containing '=' as a variable assignment, and doesn't accept '--'.
total=$(awk 'END { print NR }' <"$abs")

if (( total > max_lines )); then
  head -n "$max_lines" <"$abs"
  printf '[read_file] output truncated: showing %d of %d lines. Raise max_lines to read the rest.\n' \
    "$max_lines" "$total"
else
  cat <"$abs"
fi
