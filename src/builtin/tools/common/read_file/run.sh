#!/usr/bin/env bash
#
# read_file: return the contents of one or more text files from the workspace.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the contents, verbatim for one path and headed per file for several
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

# One path or a list of them, normalised to a list so everything below has a single
# shape to handle. Checked element by element rather than just at the top level: the
# engine validates the payload against input_schema before spawning this, but the
# script is documented as runnable on its own and has to stand up to that.
jq -e '(if (.path | type) == "array" then .path else [.path] end)
       | length > 0 and all(type == "string" and length > 0)' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'path' must be a non-empty string, or an array of them"

# NUL-delimited, so a path holding a newline stays one element. Written as \u0000 in the
# jq program rather than as a raw byte, which keeps this file plain text. `--raw-output0`
# says the same thing in one flag but arrived in jq 1.7, and 1.6 is still widely installed.
requested=()
while IFS= read -r -d '' one; do
  requested+=("$one")
done < <(
  jq -j '(if (.path | type) == "array" then .path else [.path] end)
         | .[] + "\u0000"' <<<"$input"
)

jq -e '(.max_lines // 500) | type == "number" and . >= 1 and . == floor' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'max_lines' must be an integer >= 1"
max_lines=$(jq -r '.max_lines // 500' <<<"$input")

# --- resolve the paths and keep them inside the workspace --------------------
#
# A model-supplied path is untrusted input: "../../etc/passwd" and a symlink
# pointing out of the tree both have to be rejected, so resolve to a canonical
# absolute path first and compare against the root rather than pattern-matching
# the raw string.
#
# Every path is resolved before anything is printed. A read of several files that
# emitted the good ones and then failed would hand the model a partial answer with
# no sign that it was one.

root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) \
  || fail 4 'cannot resolve the workspace root'

resolved=()
for one in "${requested[@]}"; do
  # realpath -e resolves *every* component, symlinks included, and fails if any of
  # them is missing. Canonicalising only the parent directory would let a symlink
  # inside the workspace point outside it and slip past the check below.
  abs=$(realpath -e -- "$one" 2>/dev/null) \
    || fail 3 "no such file: $one"

  case "$abs" in
    "$root"/*) ;;
    *) fail 4 "path resolves outside the workspace root: $one" ;;
  esac

  [[ -f $abs ]] || fail 4 "not a regular file: $one"
  [[ -r $abs ]] || fail 4 "not readable: $one"

  resolved+=("$abs")
done

# --- emit the contents -------------------------------------------------------

emit() { # emit <absolute-path> <what-the-notice-calls-it>
  # Read via redirection rather than passing the path as an argument: awk would
  # treat a filename containing '=' as a variable assignment, and doesn't accept '--'.
  local total
  total=$(awk 'END { print NR }' <"$1")

  if (( total > max_lines )); then
    head -n "$max_lines" <"$1"
    printf '[read_file] %s truncated: showing %d of %d lines. Raise max_lines to read the rest.\n' \
      "$2" "$max_lines" "$total"
  else
    cat <"$1"
  fi
}

# One path asked for is that file's contents and nothing else, byte for byte. A header
# there would land in whatever the flow templates the result into.
if (( ${#resolved[@]} == 1 )); then
  emit "${resolved[0]}" 'output'
  exit 0
fi

# Several, so each section has to say which file it is. The `==> name <==` form is what
# `head` uses for the same job, so it is a shape a model has already seen.
for index in "${!resolved[@]}"; do
  (( index == 0 )) || printf '\n'
  printf '==> %s <==\n' "${requested[index]}"
  emit "${resolved[index]}" "${requested[index]}"
done
