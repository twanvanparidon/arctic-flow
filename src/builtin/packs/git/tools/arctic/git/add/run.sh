#!/usr/bin/env bash
#
# git/add: stage paths for the next commit.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  what is staged after the call, one path per line
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted | 5 no repository | 6 refused
#
# **There is no "stage everything".** `git add -A` is how an unrelated file, a build
# artefact or a stray credentials file ends up in a commit nobody reviewed, and an agent
# calling this has not read the working tree the way a person about to type it has. Paths
# are named, every one is checked against the workspace, and git/status is how you find
# out what there is to name.
#
# Every path is checked before anything is staged. A call naming one bad path changes
# nothing, rather than staging the good ones and reporting an error about the rest.

set -euo pipefail

TOOL=git/add
# shellcheck source-path=SCRIPTDIR source=../../../../lib/git.sh
. "$(dirname -- "$0")/../../../../lib/git.sh"

read_input
open_repo

# One path or a list of them, normalised to a list so there is one shape to handle. The
# engine validates the payload against input_schema before spawning this, and the check is
# repeated because the script is documented as runnable on its own.
jq -e '(if (.path | type) == "array" then .path else [.path] end)
       | length > 0 and all(type == "string" and length > 0)' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'path' must be a non-empty string, or an array of them"

# NUL-delimited, so a path holding a newline stays one element. Written as the escape in
# the jq program rather than as a raw byte, which keeps this file plain text.
# `--raw-output0` says the same thing in one flag but arrived in jq 1.7, and 1.6 is still
# widely installed.
requested=()
while IFS= read -r -d '' one; do
  requested+=("$one")
done < <(
  jq -j '(if (.path | type) == "array" then .path else [.path] end)
         | .[] + "\u0000"' <<<"$input"
)

checked=()
for one in "${requested[@]}"; do
  checked+=("$(check_path "$one")")
done

# `--` before the paths: a file named `-f` is a file, not a flag.
try_git 6 add -- "${checked[@]}" >/dev/null

staged=$(try_git 6 diff --cached --name-only)
if [[ -z $staged ]]; then
  # Not a failure. Staging a file that already matches the index is a no-op, and a flow
  # that adds then commits should reach the commit and be told there is nothing in it.
  printf '[%s] nothing staged: the named paths already match the index\n' "$TOOL"
  exit 0
fi

printf '%s\n' "$staged"
