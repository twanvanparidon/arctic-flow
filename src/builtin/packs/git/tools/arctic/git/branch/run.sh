#!/usr/bin/env bash
#
# git/branch: which branches exist, and which one is checked out.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one branch per line, the current one marked with *
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 4 not permitted | 5 no repository
#
# **This lists and nothing else.** Creating or switching a branch is git/checkout, which is
# a separate tool because it is a separate permission: a spec declares `filesystem` once,
# so a tool that both reads and writes could only ever be granted as one that writes. That
# is the difference between an agent that may look at the branches and one that may move
# the working tree.

set -euo pipefail

TOOL=git/branch
# shellcheck source-path=SCRIPTDIR source=../../../../lib/git.sh
. "$(dirname -- "$0")/../../../../lib/git.sh"

read_input
open_repo

max_branches=$(count max_branches 100)

# for-each-ref rather than `git branch`, whose output is explicitly a porcelain and pads
# its own columns. Sorted by most recent commit, because a repository with sixty branches
# is asked this question about the handful that are alive.
args=(for-each-ref --sort=-committerdate --format='%(refname:short)|%(committerdate:short)|%(contents:subject)')
if flag remote; then
  args+=(refs/heads refs/remotes)
else
  args+=(refs/heads)
fi

current=$(run_git rev-parse --abbrev-ref HEAD 2>/dev/null || printf '')

listed=$(try_git 5 "${args[@]}")
if [[ -z $listed ]]; then
  printf '[%s] no branches yet\n' "$TOOL"
  exit 0
fi

# The marker is a column of its own rather than a prefix on the name, so a name can be cut
# out of this with a single field split.
formatted=$(printf '%s\n' "$listed" | awk -F'|' -v current="$current" '
  {
    mark = ($1 == current) ? "*" : " "
    printf "%s %-30s %s  %s\n", mark, $1, $2, $3
  }
')

emit_bounded "$max_branches" branches "$formatted"
