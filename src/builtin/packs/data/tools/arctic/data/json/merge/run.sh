#!/usr/bin/env bash
#
# data/json/merge: several JSON documents in, one out.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one JSON document
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 5 malformed data | 6 refused
#
# This is the tool a join reaches for. A step downstream of a branch has two results, and a
# prompt can only template them one after the other; `named` turns them into one document
# with a name on each part, which a later template can then read a field out of.
#
# **Each part is its own `part_<name>` parameter, and a nested `parts:` mapping is what that
# is instead of.** `run_step` renders only the top-level values of a step's `input`, so a
# template written inside a mapping reaches a tool as the literal `{{ steps.x.text }}`.
# `validate` reads references at any depth, so the nicer spelling lints clean and then
# arrives here unrendered. Do not "tidy" this into a mapping without changing that first.
#
# Which is also why `spec.json` closes itself with `unevaluatedProperties` where every other
# tool in the engine uses `additionalProperties`. The names are not known in advance, so they
# are a `patternProperties` entry, and `specs._check_input` reads `additionalProperties`
# against `properties` alone: strictness spelled that way would refuse every part at lint
# time. `unevaluatedProperties` runs after the pattern, so the engine's own validator still
# refuses a key that is neither `strategy` nor `part_<name>` before this script starts. The
# check below is the same refusal for a run that came from somewhere else.
#
# There is no `path` here, unlike the rest of the pack: this one takes several documents at
# once, so no single path names them.
#
# **The parts keep the order they were written in**, because a JSON object keeps its key
# order through the payload. So `shallow` and `deep` resolve a collision in favour of the
# part written last, and nothing sorts them.

set -euo pipefail

TOOL=data/json/merge
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/data.sh
. "$(dirname -- "$0")/../../../../../lib/data.sh"

read_input

# Every program below reads the parts through this, in the order they were written, with the
# prefix off. Prepended to each rather than repeated inside it.
parts='
  def parts:
    [to_entries[] | select(.key | startswith("part_"))
     | {name: (.key | ltrimstr("part_")), text: .value}];
'

stray=$(jq -r '
  first(keys_unsorted[] | select(. != "strategy" and (startswith("part_") | not))) // ""
' <<<"$input")
[[ -z $stray ]] \
  || fail 2 "'$stray' is not a parameter of merge. A document is passed as part_<name>, and the only other key is strategy"

jq -e "$parts"'parts | length > 0' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "name at least one part as part_<name>, e.g. part_review: \"{{ steps.review.text }}\""

jq -e "$parts"'parts | all(.name != "" and (.text | type == "string"))' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'every part needs a name after the underscore and a string value'

strategy=$(jq -r '.strategy // "named"' <<<"$input")
case "$strategy" in
  named | shallow | deep) ;;
  *) fail 2 "parameter 'strategy' must be named, shallow or deep" ;;
esac

# A skipped branch is the case this tool meets most, so it is named before the parse error
# it would otherwise cause. `(not run)` is what a step that did not run renders as
# (`SKIPPED_RESULT` in engine/executor.py).
skipped=$(jq -r "$parts"'first(parts[] | select(.text == "(not run)") | .name) // ""' <<<"$input")
[[ -z $skipped ]] \
  || fail 5 "part '$skipped' is '(not run)', which is what a step that did not run renders as. Guard it in the template, e.g. \"{% if steps.$skipped %}{{ steps.$skipped.text }}{% else %}{}{% endif %}\""

unparsed=$(jq -r "$parts"'
  first(parts[] | select((.text | try (fromjson | true) catch false) | not) | .name) // ""
' <<<"$input")
[[ -z $unparsed ]] \
  || fail 5 "part '$unparsed' is not JSON. Every part is a document, so a step that answered in prose has to be read with json/query first, or left out"

if [[ $strategy != named ]]; then
  # A document that is not an object has no fields to merge, and `{} + 7` is an error rather
  # than a merge. `named` is the strategy that can carry one, so the message says so.
  loose=$(jq -r "$parts"'
    first(parts[] | select((.text | fromjson | type) != "object") | .name) // ""
  ' <<<"$input")
  [[ -z $loose ]] \
    || fail 6 "part '$loose' is not an object, and $strategy merges fields. Use strategy: named to keep each part whole under its own name"
fi

# Everything stays on stdin. Passing the parts in with --argjson would put every document in
# argv, which a merge of two large ones would overflow.
case "$strategy" in
  named) jq "$parts"'parts | map({key: .name, value: (.text | fromjson)}) | from_entries' <<<"$input" ;;
  shallow) jq "$parts"'reduce parts[] as $p ({}; . + ($p.text | fromjson))' <<<"$input" ;;
  deep) jq "$parts"'reduce parts[] as $p ({}; . * ($p.text | fromjson))' <<<"$input" ;;
esac
