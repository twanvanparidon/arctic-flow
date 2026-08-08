#!/usr/bin/env bash
#
# __NAME__: returns the text it was given.
#
# Contract (the authoritative version is spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the result, and nothing else
#   stderr  a single-line message when the exit code is non-zero
#   exit    0 ok | 2 invalid input
#
# The engine runs this with the working directory set to the project root, so a
# relative path in the input resolves against the project rather than against this
# directory, wherever the tool itself was found.

set -euo pipefail

fail() { # fail <exit-code> <message>
  printf '__NAME__: %s\n' "$2" >&2
  exit "$1"
}

input=$(cat)

# The engine validates the payload against input_schema before it spawns this, so these
# two checks are for the other caller: a person running the script by hand. Keeping them
# is what makes the script runnable on its own, which is how it is worth debugging.
jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'stdin must be a single JSON object matching spec.json'

jq -e '(.text | type) == "string" and (.text | length) > 0' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'text' must be a non-empty string"

text=$(jq -r '.text' <<<"$input")

# Replace everything below with what the tool does. Anything it cannot do, report with
# fail and an exit code listed in spec.json's exit_codes, so the engine turns the number
# back into your own sentence.
#
# No trailing newline on a single-value result: it gets templated into the middle of a
# line somewhere, and a stray newline breaks the line it lands in.
printf '%s' "$text"
