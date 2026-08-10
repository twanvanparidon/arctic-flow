#!/usr/bin/env bash
#
# github/pr/open: open a pull request.
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
# the remote has never seen is a 422 from GitHub with a clear message, which is a better
# answer than a tool that quietly pushed on your behalf.
#
# Opening one twice is refused by GitHub rather than by this tool. Its message names the
# pull request that already exists, which is the useful answer, and a check here would be
# a second request that could still race.

set -euo pipefail

TOOL=github/pr/open
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

# The repository's own default branch, rather than a hardcoded "main". A repository whose
# default is `master`, `trunk` or `develop` is not unusual, and guessing wrong opens the
# pull request against a branch nobody reviews.
if [[ -z $target ]]; then
  target=$(parse "$(api GET "/repos/$repo")" -r '.default_branch')
fi

[[ $source_branch != "$target" ]] \
  || fail 2 "source and target are both '$target', so there is nothing to open a pull request for"

if flag draft; then draft=true; else draft=false; fi

# `body` is omitted rather than sent empty: GitHub renders an empty description as an
# empty box, where an absent one renders as nothing at all.
payload=$(
  jq -n --arg title "$title" --arg head "$source_branch" --arg base "$target" \
    --arg body "$body" --argjson draft "$draft" \
    '{title: $title, head: $head, base: $base, draft: $draft}
     + (if $body == "" then {} else {body: $body} end)'
)

created=$(api POST "/repos/$repo/pulls" "$payload")

# The `$name` below are jq variables bound by --arg/--argjson, not shell ones. Passing
# the program to `parse` rather than to jq itself hides that from the linter, which
# then reads them as shell variables nobody expanded.
# shellcheck disable=SC2016
parse "$created" \
  '{repo: $repo, number: .number, state: "open", title: .title,
    source: .head.ref, target: .base.ref, author: .user.login,
    url: .html_url, draft: .draft}' --arg repo "$repo"
