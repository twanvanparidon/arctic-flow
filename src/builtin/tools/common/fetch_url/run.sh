#!/usr/bin/env bash
#
# fetch_url: fetch an http(s) URL and return the response body.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  the response body, verbatim
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 5 unreachable | 6 http error
#
# The body is written undecorated, so a JSON response is still parseable JSON
# when it reaches whatever asked for it. Only truncation adds a line, and that is
# said out loud because it breaks exactly that property.

set -euo pipefail

fail() { # fail <exit-code> <message>
  printf 'fetch_url: %s\n' "$2" >&2
  exit "$1"
}

input=$(cat)

# --- parse and validate input -------------------------------------------------

jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'stdin must be a single JSON object matching spec.json'

jq -e '.url | type == "string" and length > 0' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'url' must be a non-empty string"
url=$(jq -r '.url' <<<"$input")

# Checked here as well as in input_schema, because this script is documented as
# runnable on its own. A scheme other than http(s) is the way a fetch turns into
# a local file read, so it is refused rather than handed to curl to interpret.
case "$url" in
  http://* | https://*) ;;
  *) fail 2 "parameter 'url' must be an http:// or https:// URL: $url" ;;
esac

jq -e '(.max_bytes // 200000) | type == "number" and . >= 1 and . == floor' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'max_bytes' must be an integer >= 1"
max_bytes=$(jq -r '.max_bytes // 200000' <<<"$input")

jq -e '(.timeout_seconds // 30) | type == "number" and . > 0' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'timeout_seconds' must be a number greater than 0"
timeout=$(jq -r '.timeout_seconds // 30' <<<"$input")

accept=$(jq -r '.accept // empty' <<<"$input")

# --- fetch --------------------------------------------------------------------

# Both from mktemp, not a name built from $$: a predictable path in a shared /tmp is a
# symlink someone else can plant before this runs.
body=$(mktemp) || fail 5 'cannot create a temporary file for the response'
problem=$(mktemp) || fail 5 'cannot create a temporary file for the response'
trap 'rm -f "$body" "$problem"' EXIT

options=(
  --silent --show-error
  # Follow redirects, but only ever to another http(s) URL. Without --proto-redir a
  # redirect is a way to reach a scheme the check above just refused.
  --location --max-redirs 5
  --proto '=http,https'
  --proto-redir '=http,https'
  --max-time "$timeout"
  # Identifiable rather than anonymous, so an operator reading their logs can see
  # what this is. Some hosts also refuse curl's default outright.
  --user-agent 'arctic-flow/fetch_url'
  --output "$body"
  --write-out '%{http_code}'
)
[[ -n $accept ]] && options+=(--header "Accept: $accept")

# The status has to come back separately from the body, which is why the body goes
# to a file: --write-out and the response would otherwise both be on stdout with
# nothing to tell them apart.
#
# max_bytes bounds what is *returned*, not what is downloaded. --max-time is what
# bounds the latter, since a body's size is not reliably known before it arrives.
if ! status=$(curl "${options[@]}" -- "$url" 2>"$problem"); then
  # --show-error put curl's own words in there, and they name the actual cause:
  # DNS, refused, TLS, timed out. Better than anything this could infer.
  detail=$(tr '\n' ' ' <"$problem" | sed 's/  */ /g; s/ *$//')
  fail 5 "${detail:-could not reach $url}"
fi

if [[ $status -ge 400 ]]; then
  # The body of an error usually says what was wrong, so a little of it goes in the
  # message. The engine surfaces the last stderr line as the failure, and a bare
  # status code leaves a model with nothing to act on.
  said=$(head -c 200 <"$body" | tr '\n' ' ' | sed 's/  */ /g; s/ *$//')
  fail 6 "$url returned HTTP $status${said:+. $said}"
fi

# --- emit the body ------------------------------------------------------------

# One byte past the cap, so reaching it is distinguishable from landing on it
# exactly. `head -c` is not in POSIX, but GNU, BSD, macOS and busybox all have it,
# and counting bytes any other way costs a process per byte.
size=$(wc -c <"$body" | tr -d '[:space:]')
if (( size > max_bytes )); then
  head -c "$max_bytes" <"$body"
  printf '\n[fetch_url] response truncated: showing %d of %d bytes. This is no longer valid JSON if it was. Raise max_bytes, or fetch a narrower resource.\n' \
    "$max_bytes" "$size"
else
  cat <"$body"
fi
