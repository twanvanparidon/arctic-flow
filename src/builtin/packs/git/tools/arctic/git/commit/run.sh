#!/usr/bin/env bash
#
# git/commit: record what is staged.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the short sha, the subject, and what the commit touched
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 4 not permitted | 5 no repository | 6 refused | 7 no identity
#
# **Nothing is staged for you.** git/add is a separate call, so what goes into a commit is
# a decision that was made and can be read back, rather than whatever the working tree
# happened to hold when this ran. An empty index is exit 6 and not a silent no-op.
#
# **The identity is never invented.** A commit carries an author, that author's name goes
# into history, and guessing one attributes work to someone who did not do it. So this
# uses the repository's configured identity, or the one the call names, and exits 7 saying
# what to configure when there is neither. The one thing it will not do is make one up.

set -euo pipefail

TOOL=git/commit
# shellcheck source-path=SCRIPTDIR source=../../../../lib/git.sh
. "$(dirname -- "$0")/../../../../lib/git.sh"

read_input
open_repo

jq -e '.message | type == "string" and (gsub("\\s";"") | length) > 0' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'message' must be a non-empty string"
message=$(jq -r '.message' <<<"$input")

author=$(field author)
max_files=$(count max_files 100)

# --- the identity -------------------------------------------------------------

args=(commit)

if [[ -n $author ]]; then
  # git parses this itself and refuses a malformed one, but its message names neither the
  # parameter nor the shape, so the check is here where both are known.
  [[ $author == *"<"*"@"*">"* ]] \
    || fail 2 "parameter 'author' must be written 'Name <email@example.com>'"
  args+=(--author "$author")
fi

# `--author` sets who wrote it, never who recorded it, so a committer identity is required
# either way. Checked here rather than left to git, whose own failure is a nine-line
# message ending in a suggestion to edit a global config, which is the wrong fix on a
# build machine.
if ! run_git config --get user.email >/dev/null 2>&1 \
  || ! run_git config --get user.name >/dev/null 2>&1; then
  fail 7 "git has no identity to record this commit under. Set one in the repository (git config user.name / user.email) or export GIT_COMMITTER_NAME and GIT_COMMITTER_EMAIL"
fi

# --- the commit ---------------------------------------------------------------

if [[ -z $(run_git diff --cached --name-only) ]]; then
  # Before git, so the answer names the tool that fixes it. git's own wording for this is
  # the whole of `git status`, which buries the one sentence that matters.
  fail 6 'nothing is staged, so there is nothing to commit. Stage paths with git/add first'
fi

# --no-verify is deliberately not offered. A repository's hooks are the checks its owner
# decided a commit must pass, and a tool that skipped them would let a flow write commits
# a person could not.
#
# --cleanup=whitespace rather than the default `strip`, which deletes every line starting
# with `#`. That default exists for a message typed into an editor over a commented
# template, and there is no editor here: a message arriving as a parameter has no comment
# lines in it, only `#123` written against an issue that would silently vanish.
args+=(--cleanup=whitespace -m "$message")

try_git 6 "${args[@]}" >/dev/null

sha=$(run_git rev-parse --short HEAD)
subject=$(run_git log -1 --pretty=format:%s)
printf '%s %s\n' "$sha" "$subject"

# What it recorded, so a flow can check the commit holds what it meant to commit without
# a second call. Bounded, because a generated change can touch a great many files.
files=$(run_git show --pretty=format: --name-only HEAD | grep -v '^$' || true)
if [[ -n $files ]]; then
  printf '\n'
  emit_bounded "$max_files" files "$files"
fi
