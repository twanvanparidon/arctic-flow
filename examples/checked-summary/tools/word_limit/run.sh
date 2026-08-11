#!/usr/bin/env bash
#
# word_limit: answer whether a piece of text is inside a word budget.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the verdict, as one JSON object
#   stderr  a single-line reason when the input cannot be read
#   exit    0 answered | 2 invalid input
#
# Exit 0 on a rejection too, and that is the whole design. Answering "no" is this tool
# doing its job, so the answer goes on stdout where a flow can switch on it. A non-zero
# exit means the tool could not answer at all, and fails the step that ran it.
#
# JSON rather than a sentence, because both halves of the answer are needed in different
# places: `.verdict` is what the switch matches, and `.reason` is what the next draft is
# told. A single line would make the flow parse prose to get at either.

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
  jq -nc --argjson words "$words" --argjson limit "$limit" \
    '{ verdict: "rejected", words: $words, limit: $limit, over: ($words - $limit),
       reason: "\($words) words, \($words - $limit) over the limit of \($limit). Cut it down." }'
else
  jq -nc --argjson words "$words" --argjson limit "$limit" \
    '{ verdict: "approved", words: $words, limit: $limit, over: 0, reason: null }'
fi
