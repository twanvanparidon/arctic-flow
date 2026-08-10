#!/usr/bin/env bash
#
# github/pr/status: one pull request, its reviews and its checks, as JSON.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one JSON object, the shape in spec.json's output_schema
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted
#           | 5 unreachable | 6 http error | 7 no credential
#
# JSON rather than prose, because the engine parses a tool's stdout and offers it to
# templates as `.json`. So a flow reads `{{ steps.pr.json.state }}` in a switch and
# `{{ steps.pr.text }}` in a prompt, and neither needs this to be two tools.
#
# **The field names are the bitbucket pack's field names.** A flow that switches on
# `.json.state` or counts `.json.checks.failure` works against either forge unchanged.
# That is worth more than mirroring whatever each API happens to call things, so the
# vocabulary here is normalised and the mapping is written down in tool.md.

set -euo pipefail

TOOL=github/pr/status
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/api.sh
. "$(dirname -- "$0")/../../../../../lib/api.sh"

read_input
resolve_repo
resolve_number

pull=$(api GET "/repos/$repo/pulls/$number")
head_sha=$(parse "$pull" -r '.head.sha')

# Reviews and checks are separate calls because REST keeps them on separate resources.
# Three requests is the price of the answer a person actually wants, which is "can this
# be merged", not "what does the pull request record say about itself".
reviews=$(api GET "/repos/$repo/pulls/$number/reviews?per_page=100")
checks=$(api GET "/repos/$repo/commits/$head_sha/check-runs?per_page=100")

# A review is superseded by the same person's next one, so the state of a pull request is
# the *latest* review per reviewer. Counting every review would report "1 approved,
# 1 changes requested" for one person who asked for a change and then approved it.
# COMMENTED is dropped: it is a remark, not a verdict.
# The `$name` below are jq variables bound by --arg/--argjson, not shell ones. Passing
# the program to `parse` rather than to jq itself hides that from the linter, which
# then reads them as shell variables nobody expanded.
# shellcheck disable=SC2016
review_counts=$(
  parse "$reviews" \
    '[.[] | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED")]
     | group_by(.user.login) | map(last)
     | {approved: ([.[] | select(.state == "APPROVED")] | length),
        changes_requested: ([.[] | select(.state == "CHANGES_REQUESTED")] | length)}'
)

# GitHub splits a check into a status and a conclusion, and lists six conclusions. A flow
# wants three answers, so: anything not finished is pending, anything finished that did
# not object is success, and the rest is failure. `neutral` and `skipped` are successes
# because neither is a check saying no.
#
# The run is bound to $run first. `index(.conclusion)` would evaluate `.conclusion`
# against the array it is indexing into rather than against the check.
# The `$name` below are jq variables bound by --arg/--argjson, not shell ones. Passing
# the program to `parse` rather than to jq itself hides that from the linter, which
# then reads them as shell variables nobody expanded.
# shellcheck disable=SC2016
check_counts=$(
  parse "$checks" \
    '[.check_runs[] as $run
      | if $run.status != "completed" then {state: "pending", name: $run.name}
        elif (["success", "neutral", "skipped"] | index($run.conclusion)) then
          {state: "success", name: $run.name}
        else {state: "failure", name: $run.name} end]
     | {success: ([.[] | select(.state == "success")] | length),
        failure: ([.[] | select(.state == "failure")] | length),
        pending: ([.[] | select(.state == "pending")] | length),
        failing: [.[] | select(.state == "failure") | .name]}'
)

jq -n \
  --argjson pull "$pull" \
  --argjson reviews "$review_counts" \
  --argjson checks "$check_counts" \
  --arg repo "$repo" \
  '{
     repo: $repo,
     number: $pull.number,
     # open | merged | closed, which is the vocabulary both packs answer in. GitHub says
     # "closed" for a merged pull request too, so `merged` has to be read first.
     state: (if $pull.merged then "merged" else $pull.state end),
     title: $pull.title,
     source: $pull.head.ref,
     target: $pull.base.ref,
     author: $pull.user.login,
     url: $pull.html_url,
     draft: $pull.draft,
     # null while GitHub is still working it out, which is a third answer and not a false.
     mergeable: $pull.mergeable,
     reviews: $reviews,
     checks: $checks
   }'
