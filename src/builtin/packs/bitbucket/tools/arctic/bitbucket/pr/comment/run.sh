#!/usr/bin/env bash
#
# bitbucket/pr/comment: leave a comment on a pull request.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one JSON object, the shape in spec.json's output_schema
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted
#           | 5 unreachable | 6 http error | 7 no credential
#
# A comment on the conversation, not an inline note on a line. An inline comment needs a
# path and a line number, which is a different tool with a different contract, and a flow
# that has read a diff can quote the line in prose instead.
#
# Deliberately not an approval. Approving is a verdict with weight in a merge check, and a
# flow that could cast one could approve its own work.

set -euo pipefail

TOOL=bitbucket/pr/comment
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/api.sh
. "$(dirname -- "$0")/../../../../../lib/api.sh"

read_input
resolve_repo
resolve_number

body=$(required body)

# Before the request, because Bitbucket accepts a whitespace-only comment and posts it. A
# flow whose agent produced nothing should fail here rather than leave an empty remark
# under somebody's pull request.
[[ -n ${body//[[:space:]]/} ]] \
  || fail 2 "parameter 'body' is only whitespace, so there is no comment to leave"

# `{content: {raw: ...}}`, because Bitbucket takes a rendered-content object rather than a
# string. `raw` is the markdown source, which is what was written.
payload=$(jq -n --arg body "$body" '{content: {raw: $body}}')
posted=$(api POST "/repositories/$repo/pullrequests/$number/comments" "$payload")

# The `$name` below are jq variables bound by --arg/--argjson, not shell ones. Passing
# the program to `parse` rather than to jq itself hides that from the linter, which
# then reads them as shell variables nobody expanded.
# shellcheck disable=SC2016
parse "$posted" \
  '{repo: $repo, number: $number, id: .id, url: .links.html.href,
    author: (.user.nickname // .user.display_name)}' \
  --arg repo "$repo" --argjson number "$number"
