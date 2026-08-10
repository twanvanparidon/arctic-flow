#!/usr/bin/env bash
#
# github/pr/comment: leave a comment on a pull request.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one JSON object, the shape in spec.json's output_schema
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted
#           | 5 unreachable | 6 http error | 7 no credential
#
# A comment on the conversation, not a review and not a line note. `/issues/{n}/comments`
# is the endpoint for that, and the number is shared with pull requests, which is why a
# tool about pull requests posts to a path that says issues.
#
# Deliberately not a review. Approving or requesting changes is a verdict with weight in
# a branch protection rule, and a flow that could cast one could approve its own work.
# Leaving a comment says the same thing without counting as a signature.

set -euo pipefail

TOOL=github/pr/comment
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/api.sh
. "$(dirname -- "$0")/../../../../../lib/api.sh"

read_input
resolve_repo
resolve_number

body=$(required body)

# Before the request, because GitHub accepts a whitespace-only comment and posts it. A
# flow whose agent produced nothing should fail here rather than leave an empty remark
# under somebody's pull request.
[[ -n ${body//[[:space:]]/} ]] \
  || fail 2 "parameter 'body' is only whitespace, so there is no comment to leave"

posted=$(api POST "/repos/$repo/issues/$number/comments" "$(jq -n --arg body "$body" '{body: $body}')")

# The `$name` below are jq variables bound by --arg/--argjson, not shell ones. Passing
# the program to `parse` rather than to jq itself hides that from the linter, which
# then reads them as shell variables nobody expanded.
# shellcheck disable=SC2016
parse "$posted" \
  '{repo: $repo, number: $number, id: .id, url: .html_url, author: .user.login}' \
  --arg repo "$repo" --argjson number "$number"
