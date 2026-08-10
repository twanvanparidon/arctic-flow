#!/usr/bin/env bash
#
# git/show: one commit, its message and what it changed.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the commit header and message, then its diff
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted | 5 no repository
#
# Separate from git/diff, which answers "what is different between two things". This one
# answers "what did this commit do", which carries the message and the author with it and
# is what a review or a changelog is actually built from.

set -euo pipefail

TOOL=git/show
# shellcheck source-path=SCRIPTDIR source=../../../../lib/git.sh
. "$(dirname -- "$0")/../../../../lib/git.sh"

read_input
open_repo

ref=$(field ref HEAD)
path=$(field path)
max_lines=$(count max_lines 400)

check_ref "$ref" ref

# --date=short and %an for the same reason git/log uses them: one shape to compare.
args=(show --date=short --pretty=format:'commit %H%nauthor %an <%ae>%ndate   %ad%n%n%s%n%n%b')
flag summary && args+=(--stat)

args+=("$ref")

if [[ -n $path ]]; then
  args+=(-- "$(check_path "$path")")
fi

# A merge commit shows no diff by default, because git cannot pick which parent to
# compare against. The message and the parents are still the answer to "what is this
# commit", so that is not treated as an error here.
#
# Assigned rather than passed straight to `emit_bounded`: a `try_git` inside an argument
# runs in a subshell whose exit reaches nothing, so a ref that does not resolve would
# print its error and then succeed with no output. See the note on `try_git`.
commit=$(try_git 3 "${args[@]}")
emit_bounded "$max_lines" lines "$commit"
