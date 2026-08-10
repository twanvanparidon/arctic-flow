#!/usr/bin/env bash
#
# git/diff: what changed, as a unified diff or as a summary.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  a unified diff, or one line per file when summary is true
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted | 5 no repository
#
# No changes exits 0. A flow that diffs before deciding whether to commit has to be able
# to read "nothing" as an answer rather than as a failure.
#
# There is deliberately no way to ask for the whole diff. `max_lines` has a default and no
# "unlimited": a diff is the one tool output here with no natural bound, and an unbounded
# one goes straight into a prompt and is paid for by the token.

set -euo pipefail

TOOL=git/diff
# shellcheck source-path=SCRIPTDIR source=../../../../lib/git.sh
. "$(dirname -- "$0")/../../../../lib/git.sh"

read_input
open_repo

max_lines=$(count max_lines 400)
ref=$(field ref)
path=$(field path)

[[ -z $ref ]] || check_ref "$ref" ref

args=(diff)
flag summary && args+=(--stat)

# Three questions, and they are not the same one. Staged is what a commit would record,
# the default is what a commit would leave behind, and a ref is the difference from
# somewhere else. `--cached` and a ref together is a legitimate combination: it is
# "what would this commit look like against main".
flag staged && args+=(--cached)
[[ -z $ref ]] || args+=("$ref")

if [[ -n $path ]]; then
  args+=(-- "$(check_path "$path")")
fi

changed=$(try_git 3 "${args[@]}")

if [[ -z $changed ]]; then
  printf '[%s] no changes\n' "$TOOL"
  exit 0
fi

emit_bounded "$max_lines" lines "$changed"
