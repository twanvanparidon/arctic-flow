#!/usr/bin/env bash
#
# git/status: what is staged, what is changed, and what is untracked.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the branch, then the changes grouped by what would be committed
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 4 not permitted | 5 no repository
#
# A clean tree exits 0 and says so. The engine fails a step on any non-zero exit, so
# "nothing has changed" has to be a success or a flow could not branch on it.
#
# Porcelain v1 rather than `git status` itself, because the human format is explicitly not
# a stable interface and changes between releases. The two-letter code is stable, and the
# words below are this tool's own so a model does not have to know what `MM` means.

set -euo pipefail

TOOL=git/status
# shellcheck source-path=SCRIPTDIR source=../../../../lib/git.sh
. "$(dirname -- "$0")/../../../../lib/git.sh"

read_input
open_repo

max_files=$(count max_files 200)
untracked=all
flag no_untracked && untracked=no

# --- the branch ---------------------------------------------------------------

branch=$(run_git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'HEAD')
if [[ $branch == HEAD ]]; then
  # A detached head has no branch name to report, and saying "HEAD" would read as one.
  printf 'detached at %s\n' "$(run_git rev-parse --short HEAD)"
else
  printf 'branch %s\n' "$branch"
fi

# An upstream is optional, and a branch that has none is the ordinary case for a branch
# that was just created. Silence rather than "no upstream", which reads as a problem.
if upstream=$(run_git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null); then
  counts=$(run_git rev-list --left-right --count "$upstream...HEAD" 2>/dev/null || printf '0\t0')
  behind=${counts%%$'\t'*}
  ahead=${counts##*$'\t'}
  printf 'upstream %s, ahead %s, behind %s\n' "$upstream" "$ahead" "$behind"
fi

# --- the changes --------------------------------------------------------------

describe() { # describe <porcelain-letter>
  case "$1" in
    M) printf 'modified' ;;
    A) printf 'added' ;;
    D) printf 'deleted' ;;
    R) printf 'renamed' ;;
    C) printf 'copied' ;;
    T) printf 'typechange' ;;
    U) printf 'conflicted' ;;
    *) printf 'changed' ;;
  esac
}

staged=()
unstaged=()
untracked_paths=()

while IFS= read -r line; do
  [[ -n $line ]] || continue
  index=${line:0:1}
  worktree=${line:1:1}
  path=${line:3}

  if [[ $index == '?' ]]; then
    untracked_paths+=("$path")
    continue
  fi
  # A conflict is both letters at once and belongs in neither list, so it is reported
  # first and on its own. Committing one is what git refuses, and this is where a flow
  # finds out before it tries.
  if [[ $index == U || $worktree == U ]]; then
    staged+=("conflicted $path")
    continue
  fi
  [[ $index == ' ' ]] || staged+=("$(describe "$index") $path")
  [[ $worktree == ' ' ]] || unstaged+=("$(describe "$worktree") $path")
done < <(run_git status --porcelain=v1 "--untracked-files=$untracked")

total=$(( ${#staged[@]} + ${#unstaged[@]} + ${#untracked_paths[@]} ))
if (( total == 0 )); then
  printf 'clean\n'
  exit 0
fi

# One budget across the three groups rather than one each, so `max_files` bounds the whole
# answer. Kept as a countdown, and a group with nothing left to spend prints no heading at
# all: a heading over a truncation notice reads as a group that is empty, which is the
# opposite of what happened.
remaining=$max_files

emit() { # emit <heading> <entries...>
  local heading=$1
  shift
  (( $# > 0 && remaining > 0 )) || return 0
  printf '\n%s:\n' "$heading"
  for entry in "$@"; do
    (( remaining > 0 )) || return 0
    # Two fields for a change and one for an untracked path, so the word is padded here
    # rather than in the caller.
    if [[ $heading == untracked ]]; then
      printf '  %s\n' "$entry"
    else
      printf '  %-11s %s\n' "${entry%% *}" "${entry#* }"
    fi
    remaining=$(( remaining - 1 ))
  done
}

emit staged ${staged[@]+"${staged[@]}"}
emit unstaged ${unstaged[@]+"${unstaged[@]}"}
emit untracked ${untracked_paths[@]+"${untracked_paths[@]}"}

# One notice for the whole report rather than one per group, and it names the total, so
# "is this everything" is answered without adding up three lists.
if (( remaining <= 0 && total > max_files )); then
  printf '\n[%s] truncated: showing %s of %s paths. Raise max_files to see the rest.\n' \
    "$TOOL" "$max_files" "$total"
fi
