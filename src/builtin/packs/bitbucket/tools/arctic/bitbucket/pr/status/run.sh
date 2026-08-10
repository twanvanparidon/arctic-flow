#!/usr/bin/env bash
#
# bitbucket/pr/status: one pull request, its approvals and its build statuses, as JSON.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one JSON object, the shape in spec.json's output_schema
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted
#           | 5 unreachable | 6 http error | 7 no credential
#
# **The field names are the github pack's field names.** A flow that switches on
# `.json.state` or counts `.json.checks.failure` works against either forge unchanged, and
# the normalising happens here rather than in whoever reads it. Two fields Bitbucket does
# not answer are `null` rather than invented: see tool.md.

set -euo pipefail

TOOL=bitbucket/pr/status
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/api.sh
. "$(dirname -- "$0")/../../../../../lib/api.sh"

read_input
resolve_repo
resolve_number

pull=$(api GET "/repositories/$repo/pullrequests/$number")

# Unlike GitHub, the reviewers come embedded in the pull request, so this is one call
# rather than two. Build statuses are their own resource and are the second.
statuses=$(api GET "/repositories/$repo/pullrequests/$number/statuses?pagelen=100")

# A participant is a reviewer or just somebody who commented, and `approved` is a flag
# rather than a state, so "changes requested" is the separate `state` field. Both are
# already the latest per person: Bitbucket keeps one participant record each, so there is
# no superseding to do here the way there is on GitHub.
# The `$name` below are jq variables bound by --arg/--argjson, not shell ones. Passing
# the program to `parse` rather than to jq itself hides that from the linter, which
# then reads them as shell variables nobody expanded.
# shellcheck disable=SC2016
review_counts=$(
  parse "$pull" \
    '(.participants // [])
     | {approved: ([.[] | select(.approved == true)] | length),
        changes_requested: ([.[] | select(.state == "changes_requested")] | length)}'
)

# Bitbucket's four build states onto the three every pack answers with. STOPPED is a
# failure rather than a pending: a build somebody cancelled is not one still running, and
# treating it as pending would make a flow wait for something that already stopped.
# The `$name` below are jq variables bound by --arg/--argjson, not shell ones. Passing
# the program to `parse` rather than to jq itself hides that from the linter, which
# then reads them as shell variables nobody expanded.
# shellcheck disable=SC2016
check_counts=$(
  parse "$statuses" \
    '[(.values // [])[] as $build
      | if $build.state == "SUCCESSFUL" then {state: "success", name: $build.key}
        elif $build.state == "INPROGRESS" then {state: "pending", name: $build.key}
        else {state: "failure", name: $build.key} end]
     | {success: ([.[] | select(.state == "success")] | length),
        failure: ([.[] | select(.state == "failure")] | length),
        pending: ([.[] | select(.state == "pending")] | length),
        failing: [.[] | select(.state == "failure") | .name]}'
)

# The `$name` below are jq variables bound by --arg/--argjson, not shell ones. Passing
# the program to `parse` rather than to jq itself hides that from the linter, which
# then reads them as shell variables nobody expanded.
# shellcheck disable=SC2016
parse "$pull" \
  '{
     repo: $repo,
     number: .id,
     # OPEN, MERGED, DECLINED and SUPERSEDED, onto the three words the github pack uses.
     # Declined and superseded both mean "closed without merging", which is what GitHub
     # calls closed, and collapsing them is what lets one flow read either forge.
     state: (if .state == "OPEN" then "open"
             elif .state == "MERGED" then "merged"
             else "closed" end),
     title: .title,
     source: .source.branch.name,
     target: .destination.branch.name,
     author: (.author.nickname // .author.display_name),
     url: .links.html.href,
     draft: (.draft // false),
     # Bitbucket does not say. A dry-run merge would, at the cost of a third request and a
     # write-shaped call from a tool that reads, so this answers "not known" rather than
     # guessing. `checks` and `reviews` are what a flow should gate on anyway.
     mergeable: null,
     reviews: $reviews,
     checks: $checks
   }' \
  --arg repo "$repo" --argjson reviews "$review_counts" --argjson checks "$check_counts"
