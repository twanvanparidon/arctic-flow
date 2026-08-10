#!/usr/bin/env bash
#
# Shared by every tool in the github pack. Sourced, never executed.
#
# A tool sets TOOL to its own name, sources this, then calls `read_input` and whichever of
# `resolve_repo` / `resolve_number` it needs.
#
#   TOOL=github/pr/status
#   . "$(dirname -- "$0")/../../../../../lib/api.sh"
#
# Deliberately not shared with the bitbucket pack, which carries its own copy of the same
# shape. A pack is a unit you can read top to bottom, copy, or delete, and the price of
# that is roughly seventy duplicated lines. The git pack's `lib/git.sh` exists for a
# different reason: it holds a *security* check, where a second copy is a second thing to
# forget. These are input helpers and one curl call.
#
# Exit codes, shared across the pack. A tool's spec.json lists only the ones it produces:
#
#   0  ok
#   2  invalid input          stdin was not a JSON object, or a parameter is wrong
#   3  not found              no such pull request, branch or repository (404)
#   4  not permitted          the git repository is not the workspace
#   5  unreachable            DNS, connection, TLS or the transfer timed out
#   6  http error             the API answered, with a status that is not success
#   7  no credential          $GITHUB_TOKEN is not in the environment

# Where the API is. `$GITHUB_API_URL` is what GitHub Actions already exports, and it is
# how GitHub Enterprise is reached: there the root is https://<host>/api/v3 rather than a
# different path shape, so one variable covers it. It is also how the test suite points
# these tools at a double.
API_ROOT=${GITHUB_API_URL:-https://api.github.com}
API_ROOT=${API_ROOT%/}

# The host a remote must name for `resolve_repo` to trust it. Derived from the API root so
# an Enterprise install checks against its own host rather than github.com.
api_host() {
  local without=${API_ROOT#*://}
  printf '%s' "${without%%/*}"
}

fail() { # fail <exit-code> <message>
  printf '%s: %s\n' "${TOOL:-github}" "$2" >&2
  exit "$1"
}

# --- input --------------------------------------------------------------------

read_input() { # sets $input to the JSON object on stdin
  input=$(cat)
  jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
    || fail 2 'stdin must be a single JSON object matching spec.json'
}

field() { # field <name> [default]
  jq -r --arg f "$1" --arg d "${2-}" '.[$f] // $d | tostring' <<<"$input"
}

required() { # required <name>
  jq -e --arg f "$1" '.[$f] | type == "string" and length > 0' >/dev/null 2>&1 <<<"$input" \
    || fail 2 "parameter '$1' must be a non-empty string"
  field "$1"
}

flag() { # flag <name>
  [[ $(jq -r --arg f "$1" '.[$f] // false' <<<"$input") == true ]]
}

# --- the credential -----------------------------------------------------------

require_token() {
  [[ -n ${GITHUB_TOKEN:-} ]] || fail 7 \
    "GITHUB_TOKEN is not in this step's environment. Put the token in the vault and declare it: secrets: [GITHUB_TOKEN]"
}

# --- calling the API ----------------------------------------------------------

# One request. Prints the response body on stdout and maps everything that went wrong onto
# this pack's exit codes.
#
# **The token never reaches argv.** `-H "Authorization: ..."` would put it in the command
# line, where `ps` shows it to every user on the machine for as long as the request takes.
# curl reads headers from a config file instead, written with a private mode and removed
# on the way out. The body goes in a second file for the same reason: a comment is not
# secret, but `--data` on argv has the same shape and it is one rule rather than two.
api() { # api <method> <path> [json-body]
  local method=$1 path=$2 body=${3-} config data response code
  require_token

  config=$(mktemp) || fail 5 'cannot create a temporary file'
  chmod 600 "$config"
  {
    printf 'header = "Authorization: Bearer %s"\n' "$GITHUB_TOKEN"
    printf 'header = "Accept: application/vnd.github+json"\n'
    # Pinned, because GitHub's REST responses are versioned by this header and an
    # unpinned client is one whose output changes without the tool changing.
    printf 'header = "X-GitHub-Api-Version: 2022-11-28"\n'
    printf 'header = "User-Agent: arctic-flow"\n'
  } >"$config"

  local arguments=(--silent --show-error --config "$config" --max-time 30
                   --request "$method" --write-out '\n%{http_code}')

  if [[ -n $body ]]; then
    data=$(mktemp) || fail 5 'cannot create a temporary file'
    printf '%s' "$body" >"$data"
    arguments+=(--header 'Content-Type: application/json' --data "@$data")
  fi

  if ! response=$(curl "${arguments[@]}" "$API_ROOT$path" 2>&1); then
    rm -f "$config" "${data:-}"
    # curl's own failure: it never reached a status. Its message is on the last line,
    # and it is the one that names a refused connection or an expired certificate.
    fail 5 "cannot reach $(api_host): $(printf '%s' "$response" | tail -n 1)"
  fi
  rm -f "$config" "${data:-}"

  # `--write-out` appended a newline and the status, so the last line is always the code
  # and everything before it is the body, whether or not the body ended in a newline.
  code=${response##*$'\n'}
  response=${response%$'\n'*}

  case "$code" in
    2*) printf '%s' "$response" ;;
    404) fail 3 "$(api_reason "$response" "no such pull request or repository")" ;;
    401 | 403) fail 6 "$(api_reason "$response" "GITHUB_TOKEN was refused ($code)")" ;;
    *) fail 6 "$(api_reason "$response" "the API answered $code")" ;;
  esac
}

# What the API said went wrong, in its own words where it gave any. GitHub answers an
# error with {"message": "..."}, and that sentence is more use than the status alone.
api_reason() { # api_reason <body> <fallback>
  local said
  said=$(jq -r '.message // empty' 2>/dev/null <<<"$1" || true)
  printf '%s' "${said:-$2}"
}

# jq over a response, with its failure reported as this pack's rather than as jq's.
#
# Without this, `set -e` propagates jq's own exit status, and jq exits 5 on a program
# error. 5 is `unreachable` in spec.json, so a response this tool could not read would be
# reported as a network that is down, which sends whoever reads it somewhere else entirely.
parse() { # parse <json> <jq-program> [jq-args...]
  local document=$1 program=$2
  shift 2
  jq "$@" "$program" <<<"$document" \
    || fail 6 "the API answered something this tool could not read. Its shape may have changed"
}

# --- which repository ---------------------------------------------------------

# `owner/name`, from the input or from the checkout the flow is running in.
#
# The default is the whole reason these tools are usable in a flow, and it is also the one
# that could act on the wrong repository, so it carries the git pack's containment rule:
# the repository has to *be* the workspace. Without that a flow run in `myrepo/subproject`
# would read `myrepo`'s remote and open a pull request there.
resolve_repo() { # sets $repo
  repo=$(field repo)
  if [[ -n $repo ]]; then
    [[ $repo == */* && $repo != */*/* ]] \
      || fail 2 "parameter 'repo' must be written 'owner/name'"
    return
  fi

  command -v git >/dev/null 2>&1 \
    || fail 2 "no 'repo' given and git is not installed, so there is no remote to read it from"

  local root toplevel remote
  root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) || fail 4 'cannot resolve the workspace root'
  unset GIT_DIR GIT_WORK_TREE

  toplevel=$(git -C "$root" rev-parse --show-toplevel 2>/dev/null) \
    || fail 2 "no 'repo' given and the workspace is not a git repository, so there is no remote to read it from"
  [[ $(cd -- "$toplevel" && pwd -P) == "$root" ]] \
    || fail 4 "the git repository is $toplevel, which is above the workspace. Name the repository with 'repo', or run atf with --workspace $toplevel"

  remote=$(git -C "$root" remote get-url origin 2>/dev/null) \
    || fail 2 "no 'repo' given and this repository has no 'origin' remote to read it from"
  repo=$(parse_remote "$remote")
}

# `owner/name` out of a remote URL, in either spelling git writes:
#
#   git@github.com:owner/name.git
#   https://github.com/owner/name.git
#
# The host is checked rather than ignored. A github tool deriving its repository from a
# bitbucket remote would build a plausible URL for the wrong service, and the 404 it got
# back would say nothing about why.
parse_remote() { # parse_remote <url>
  local url=$1 host path
  case "$url" in
    *://*)
      host=${url#*://}
      host=${host#*@}
      path=${host#*/}
      host=${host%%/*}
      ;;
    *:*)
      host=${url%%:*}
      host=${host#*@}
      path=${url#*:}
      ;;
    *) fail 2 "cannot read a repository out of the origin remote '$url'" ;;
  esac

  host=${host%%:*}
  [[ $host == "$(api_host)" || $host == github.com ]] \
    || fail 2 "the origin remote points at $host, not $(api_host). Name the repository with 'repo'"

  path=${path%.git}
  path=${path#/}
  [[ $path == */* && $path != */*/* ]] \
    || fail 2 "cannot read 'owner/name' out of the origin remote '$url'"
  printf '%s' "$path"
}

# --- which pull request -------------------------------------------------------

current_branch() {
  local root
  root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) || fail 4 'cannot resolve the workspace root'
  git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null \
    || fail 2 "no 'number' given and the workspace is not a git repository, so there is no branch to look one up by"
}

# The pull request to act on: the number given, or the open one for the current branch.
#
# Looking it up rather than requiring a number is what lets a flow say "comment on the
# pull request for what I just pushed" without a step in between to find it. Exactly one
# match is required: several open pull requests from one branch means the guess would be a
# guess, and commenting on the wrong one is not something to do quietly.
resolve_number() { # sets $number
  number=$(field number)
  if [[ -n $number ]]; then
    [[ $number =~ ^[0-9]+$ ]] || fail 2 "parameter 'number' must be a positive integer"
    return
  fi

  local branch found count
  branch=$(current_branch)
  [[ $branch != HEAD ]] \
    || fail 2 "no 'number' given and the workspace is on a detached head, so there is no branch to look one up by"

  found=$(api GET "/repos/$repo/pulls?state=open&head=${repo%%/*}:$branch&per_page=100")
  count=$(jq 'length' <<<"$found")

  case "$count" in
    0) fail 3 "no open pull request from '$branch' in $repo. Give 'number' to name one" ;;
    1) number=$(jq -r '.[0].number' <<<"$found") ;;
    *) fail 2 "$count open pull requests come from '$branch' in $repo, so 'number' has to say which" ;;
  esac
}
