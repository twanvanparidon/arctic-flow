#!/usr/bin/env bash
#
# git/checkout: switch to a branch, or create one and switch to it.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  which branch is checked out now, and which it was before
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 4 not permitted | 5 no repository | 6 refused
#
# **Branches only.** `git checkout` in a shell also restores files, which throws away
# uncommitted work and cannot be undone. That half is not here and is not coming: a tool
# an agent can call has no business being the one command in git with no way back. Use
# `git switch`'s half of the job and nothing else.
#
# Which is also why there is no `force`. git refuses to switch when the working tree has
# changes that would be overwritten, and that refusal is the only thing standing between a
# flow and someone's uncommitted work. It is reported as exit 6 with git's own reason, so
# the flow can commit or stash and try again.

set -euo pipefail

TOOL=git/checkout
# shellcheck source-path=SCRIPTDIR source=../../../../lib/git.sh
. "$(dirname -- "$0")/../../../../lib/git.sh"

read_input
open_repo

jq -e '.branch | type == "string" and length > 0' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'branch' must be a non-empty string"
branch=$(jq -r '.branch' <<<"$input")
check_ref "$branch" branch

start=$(field start_point)
[[ -z $start ]] || check_ref "$start" start_point

was=$(run_git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'no branch')

# `switch` rather than `checkout`, because it is the half of the old command that this
# tool is: it refuses a path where `checkout` would have quietly restored one.
args=(switch)

if flag create; then
  args+=(-c "$branch")
  [[ -z $start ]] || args+=("$start")
else
  [[ -z $start ]] \
    || fail 2 "parameter 'start_point' only means something with create true: an existing branch already has a starting point"
  args+=("$branch")
fi

# 6 rather than 3 for a branch that does not exist. It is not a lookup that came up empty:
# something asked to move the working tree and git declined, and the fix is the same as
# for a dirty tree, which is to read what git said.
try_git 6 "${args[@]}" >/dev/null

now=$(run_git rev-parse --abbrev-ref HEAD)
if [[ $now == "$was" ]]; then
  printf 'already on %s\n' "$now"
else
  printf 'on %s, was %s\n' "$now" "$was"
fi
