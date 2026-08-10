#!/usr/bin/env bash
#
# git/log: recent commits, oldest last.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one line per commit, or a block per commit when body is true
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted | 5 no repository
#
# An empty repository exits 0 and says so. A flow asking "what has happened here" has to
# survive the answer being "nothing yet", which is the state a fresh `git init` is in.

set -euo pipefail

TOOL=git/log
# shellcheck source-path=SCRIPTDIR source=../../../../lib/git.sh
. "$(dirname -- "$0")/../../../../lib/git.sh"

read_input
open_repo

max_commits=$(count max_commits 20)
ref=$(field ref)
path=$(field path)

[[ -z $ref ]] || check_ref "$ref" ref

# A repository with no commits has no HEAD to resolve, so every git call below would fail
# with a message about a bad revision. That is a state, not an error.
if ! run_git rev-parse --verify --quiet HEAD >/dev/null 2>&1; then
  printf '[%s] no commits yet\n' "$TOOL"
  exit 0
fi

# --date=short and not the author's own format: a log read by a model is compared and
# sorted, and one date shape does that. %an rather than %ae, because a name is what a
# release note or a summary quotes.
#
# One extra commit is asked for, so reaching the cap is distinguishable from landing on it.
args=(log "--max-count=$(( max_commits + 1 ))" --date=short)
if flag body; then
  args+=(--pretty=format:'%h %ad %an%n    %s%n%w(0,4,4)%b')
else
  args+=(--pretty=format:'%h %ad %an  %s')
fi

[[ -z $ref ]] || args+=("$ref")

# `--` separates revisions from paths, so a file named like a branch cannot be read as one.
if [[ -n $path ]]; then
  args+=(-- "$(check_path "$path")")
fi

# 3 rather than 6: everything that fails here is a name that did not resolve, because the
# path is already checked and the ref is the only other thing git is being handed.
found=$(try_git 3 "${args[@]}")

if [[ -z $found ]]; then
  # git exits 0 for a path that matched no commit, which is an honest empty answer.
  printf '[%s] no commits%s\n' "$TOOL" "${path:+ touching $path}"
  exit 0
fi

# Counted in commits rather than lines, so `body` does not change what the cap means.
shown=$(printf '%s\n' "$found" | grep -c '^[0-9a-f]\{7,\} ' || true)
if (( shown > max_commits )); then
  printf '%s\n' "$found" | awk -v keep="$max_commits" '
    /^[0-9a-f]{7,} / { seen++ }
    seen > keep { exit }
    { print }
  '
  printf '[%s] truncated: showing %s commits. Raise max_commits to see more.\n' \
    "$TOOL" "$max_commits"
else
  printf '%s\n' "$found"
fi
