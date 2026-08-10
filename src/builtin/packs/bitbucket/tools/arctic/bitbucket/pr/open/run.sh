#!/usr/bin/env bash
#
# bitbucket/pr/open: open a pull request.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one JSON object, the shape in spec.json's output_schema
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted
#           | 5 unreachable | 6 http error | 7 no credential
#
# The branch has to be pushed already. Nothing in these packs pushes: that is the one
# thing the git pack deliberately refuses to do, and opening a pull request for a branch
# the remote has never seen is a 400 from Bitbucket with a clear message, which is a
# better answer than a tool that quietly pushed on your behalf.

set -euo pipefail

TOOL=bitbucket/pr/open
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/api.sh
. "$(dirname -- "$0")/../../../../../lib/api.sh"

read_input
resolve_repo

title=$(required title)
body=$(field body)
source_branch=$(field source)
target=$(field target)

# The branch you are on is what you almost always mean, and looking it up is what makes
# this one step rather than two.
if [[ -z $source_branch ]]; then
  command -v git >/dev/null 2>&1 \
    || fail 2 "no 'source' given and git is not installed, so there is no branch to read"
  source_branch=$(current_branch)
  [[ $source_branch != HEAD ]] \
    || fail 2 "no 'source' given and the workspace is on a detached head, so there is no branch to open from"
fi

# The repository's own main branch, rather than a hardcoded "main". A repository whose
# default is `master`, `develop` or anything else is not unusual, and guessing wrong opens
# the pull request against a branch nobody reviews.
if [[ -z $target ]]; then
  target=$(parse "$(api GET "/repositories/$repo")" -r '.mainbranch.name')
fi

[[ $source_branch != "$target" ]] \
  || fail 2 "source and target are both '$target', so there is nothing to open a pull request for"

# `description` rather than `body`, and the branches nested two levels down. The input
# keys stay the github pack's, so a flow that swaps one tool for the other changes the
# tool name and nothing else.
payload=$(
  jq -n --arg title "$title" --arg source "$source_branch" --arg target "$target" \
    --arg body "$body" \
    '{title: $title,
      source: {branch: {name: $source}},
      destination: {branch: {name: $target}}}
     + (if $body == "" then {} else {description: $body} end)'
)

created=$(api POST "/repositories/$repo/pullrequests" "$payload")

# The `$name` below are jq variables bound by --arg/--argjson, not shell ones. Passing
# the program to `parse` rather than to jq itself hides that from the linter, which
# then reads them as shell variables nobody expanded.
# shellcheck disable=SC2016
parse "$created" \
  '{repo: $repo, number: .id, state: "open", title: .title,
    source: .source.branch.name, target: .destination.branch.name,
    author: (.author.nickname // .author.display_name),
    url: .links.html.href, draft: (.draft // false)}' \
  --arg repo "$repo"
