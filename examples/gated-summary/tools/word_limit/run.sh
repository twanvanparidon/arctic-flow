#!/usr/bin/env bash
#
# word_limit: accept a piece of text only if it is inside a word budget.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the count, when the text is inside the limit
#   stderr  a single-line reason when it is not
#   exit    0 inside | 1 over | 2 invalid input
#
# Written to be used as a step's `gate`, so exit 1 is an ordinary outcome rather than
# a breakage: the engine puts that stderr line in front of the writer and asks again.
# Everything needed to fix the text has to fit on it.

set -euo pipefail

fail() { printf 'word_limit: %s\n' "$2" >&2; exit "$1"; }

input=$(cat)

jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'stdin must be a single JSON object matching spec.json'
jq -e 'has("text") and (.text | type == "string")' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "missing required parameter 'text'"
jq -e '.max_words | type == "number" and . >= 1 and . == floor' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'max_words' must be an integer >= 1"

limit=$(jq -r '.max_words' <<<"$input")

# awk rather than `wc -w`, which pads its count with leading spaces on some platforms.
# This number is compared, printed, and templated into a prompt.
words=$(jq -j '.text' <<<"$input" | awk '{ total += NF } END { print total + 0 }')

if (( words > limit )); then
  fail 1 "$words words, $((words - limit)) over the limit of $limit. Cut it down."
fi

# No trailing newline: a single value, so it can be templated mid-line.
printf '%d words, inside the limit of %d' "$words" "$limit"
