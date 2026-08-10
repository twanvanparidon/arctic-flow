#!/usr/bin/env bash
#
# Shared by every tool in the bitbucket pack. Sourced, never executed.
#
# A tool sets TOOL to its own name, sources this, then calls `read_input` and whichever of
# `resolve_repo` / `resolve_number` it needs.
#
#   TOOL=bitbucket/pr/status
#   . "$(dirname -- "$0")/../../../../../lib/api.sh"
#
# **Bitbucket Cloud only.** Server and Data Center speak a different API entirely, under
# /rest/api/1.0/ with different paths, different response shapes and a different auth
# scheme. Supporting both would be two packs wearing one name, and the failure mode of
# pretending otherwise is a tool that builds a plausible URL and gets a 404 that explains
# nothing.
#
# Deliberately not shared with the github pack, which carries its own copy of the same
# shape. A pack is a unit you can read top to bottom, copy, or delete, and the price of
# that is roughly seventy duplicated lines.
#
# Exit codes, shared across the pack. A tool's spec.json lists only the ones it produces:
#
#   0  ok
#   2  invalid input          stdin was not a JSON object, or a parameter is wrong
#   3  not found              no such pull request, branch or repository (404)
#   4  not permitted          the git repository is not the workspace
#   5  unreachable            DNS, connection, TLS or the transfer timed out
#   6  http error             the API answered, with a status that is not success
#   7  no credential          $BITBUCKET_TOKEN is not in the environment

# `$BITBUCKET_API_URL` exists for a proxy and for the test suite's double. There is no
# Enterprise host to point it at, which is why it is not advertised the way the github
# pack advertises $GITHUB_API_URL: Cloud is the only thing this speaks.
API_ROOT=${BITBUCKET_API_URL:-https://api.bitbucket.org/2.0}
API_ROOT=${API_ROOT%/}

# Where a repository lives, as opposed to where the API does. Bitbucket serves the two
# from different hosts, so a remote is checked against this one.
GIT_HOST=bitbucket.org

fail() { # fail <exit-code> <message>
  printf '%s: %s\n' "${TOOL:-bitbucket}" "$2" >&2
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

# --- the credential -----------------------------------------------------------

require_token() {
  [[ -n ${BITBUCKET_TOKEN:-} ]] || fail 7 \
    "BITBUCKET_TOKEN is not in this step's environment. Put the token in the vault and declare it: secrets: [BITBUCKET_TOKEN]"
}

# --- calling the API ----------------------------------------------------------

# One request. Prints the response body on stdout and maps everything that went wrong onto
# this pack's exit codes.
#
# **The token never reaches argv.** `-H "Authorization: ..."` would put it in the command
# line, where `ps` shows it to every user on the machine for as long as the request takes.
# curl reads headers from a config file instead, written with a private mode and removed
# on the way out.
#
# Bearer, so the token is a workspace, project or repository access token. An app password
# would need Basic and a username to go with it, which is a second thing to configure and
# a credential Atlassian is moving away from.
api() { # api <method> <path> [json-body]
  local method=$1 path=$2 body=${3-} config data response code
  require_token

  config=$(mktemp) || fail 5 'cannot create a temporary file'
  chmod 600 "$config"
  {
    printf 'header = "Authorization: Bearer %s"\n' "$BITBUCKET_TOKEN"
    printf 'header = "Accept: application/json"\n'
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
    fail 5 "cannot reach ${API_ROOT#*://}: $(printf '%s' "$response" | tail -n 1)"
  fi
  rm -f "$config" "${data:-}"

  # `--write-out` appended a newline and the status, so the last line is always the code
  # and everything before it is the body, whether or not the body ended in a newline.
  code=${response##*$'\n'}
  response=${response%$'\n'*}

  case "$code" in
    2*) printf '%s' "$response" ;;
    404) fail 3 "$(api_reason "$response" "no such pull request or repository")" ;;
    401 | 403) fail 6 "$(api_reason "$response" "BITBUCKET_TOKEN was refused ($code)")" ;;
    *) fail 6 "$(api_reason "$response" "the API answered $code")" ;;
  esac
}

# What the API said went wrong, in its own words where it gave any. Bitbucket nests it one
# level deeper than GitHub does: {"error": {"message": "..."}}.
api_reason() { # api_reason <body> <fallback>
  local said
  said=$(jq -r '.error.message // .message // empty' 2>/dev/null <<<"$1" || true)
  printf '%s' "${said:-$2}"
}

# --- which repository ---------------------------------------------------------

# `workspace/repo_slug`, from the input or from the checkout the flow is running in.
#
# Bitbucket calls the first segment a *workspace*, which is a word this engine already
# uses for the project root a flow runs in. They are unrelated, and `repo` is spelled
# `workspace/repo_slug` here only because that is what the URL wants. Everything else in
# these tools means the engine's workspace when it says workspace.
#
# The default carries the git pack's containment rule: the repository has to *be* the
# workspace. Without that a flow run in `myrepo/subproject` would read `myrepo`'s remote
# and comment on a pull request in the wrong repository.
resolve_repo() { # sets $repo
  repo=$(field repo)
  if [[ -n $repo ]]; then
    [[ $repo == */* && $repo != */*/* ]] \
      || fail 2 "parameter 'repo' must be written 'workspace/repository'"
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

# `workspace/repo_slug` out of a remote URL, in either spelling git writes:
#
#   git@bitbucket.org:workspace/repo.git
#   https://someone@bitbucket.org/workspace/repo.git
#
# The host is checked rather than ignored. A bitbucket tool deriving its repository from a
# github remote would build a plausible URL for the wrong service, and the 404 it got back
# would say nothing about why.
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
  [[ $host == "$GIT_HOST" ]] \
    || fail 2 "the origin remote points at $host, not $GIT_HOST. Name the repository with 'repo'"

  path=${path%.git}
  path=${path#/}
  [[ $path == */* && $path != */*/* ]] \
    || fail 2 "cannot read 'workspace/repository' out of the origin remote '$url'"
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
# `number`, not Bitbucket's own `id`, because the github pack spells it that way and a
# flow swapping one tool for the other should change the tool name and nothing else.
#
# Exactly one match is required. Several open pull requests from one branch means the
# guess would be a guess, and commenting on the wrong one is not something to do quietly.
resolve_number() { # sets $number
  number=$(field number)
  if [[ -n $number ]]; then
    [[ $number =~ ^[0-9]+$ ]] || fail 2 "parameter 'number' must be a positive integer"
    return
  fi

  local branch query found count
  branch=$(current_branch)
  [[ $branch != HEAD ]] \
    || fail 2 "no 'number' given and the workspace is on a detached head, so there is no branch to look one up by"

  # Bitbucket filters with its own query language rather than with plain parameters, and
  # the branch has to be quoted inside it. --data-urlencode is not available here because
  # this is a GET through the same helper every other call uses, so the value is encoded
  # by jq's @uri, which is the one part of the string that can carry a slash.
  query=$(jq -rn --arg branch "$branch" '"source.branch.name=\"\($branch)\"" | @uri')
  found=$(api GET "/repositories/$repo/pullrequests?state=OPEN&q=$query&pagelen=50")
  count=$(parse "$found" '.values | length')

  case "$count" in
    0) fail 3 "no open pull request from '$branch' in $repo. Give 'number' to name one" ;;
    1) number=$(parse "$found" -r '.values[0].id') ;;
    *) fail 2 "$count open pull requests come from '$branch' in $repo, so 'number' has to say which" ;;
  esac
}
