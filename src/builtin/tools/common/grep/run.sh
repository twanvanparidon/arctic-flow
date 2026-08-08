#!/usr/bin/env bash
#
# grep: report lines in the workspace matching a pattern.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one match per line, "<path>:<line>:<text>"
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted
#
# Finding nothing exits 0. The engine fails a step on any non-zero exit, so a
# search that came back empty has to succeed or a flow could not ask a question
# whose honest answer is "nowhere".
#
# **Only options POSIX guarantees.** The host's grep may be GNU, BSD, busybox or
# macOS's, and they disagree beyond that line: -r recurses differently, --include
# and -P are GNU-only, and -o is not in POSIX either. So recursion comes from
# `find` and the file list is handed over explicitly. What is used here is
# -E -F -i -n -e, all of which POSIX specifies.

set -euo pipefail

fail() { # fail <exit-code> <message>
  printf 'grep: %s\n' "$2" >&2
  exit "$1"
}

input=$(cat)

# --- parse and validate input -------------------------------------------------

jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'stdin must be a single JSON object matching spec.json'

jq -e '.pattern | type == "string" and length > 0' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'pattern' must be a non-empty string"
pattern=$(jq -r '.pattern' <<<"$input")

target=$(jq -r '.path // "."' <<<"$input")
[[ -n $target ]] || fail 2 "parameter 'path' must be a non-empty string"

glob=$(jq -r '.glob // empty' <<<"$input")

for flag in fixed ignore_case; do
  jq -e --arg f "$flag" '(.[$f] // false) | type == "boolean"' >/dev/null 2>&1 <<<"$input" \
    || fail 2 "parameter '$flag' must be a boolean"
done

jq -e '(.max_matches // 200) | type == "number" and . >= 1 and . == floor' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'max_matches' must be an integer >= 1"
max_matches=$(jq -r '.max_matches // 200' <<<"$input")

# --- resolve the search path and keep it inside the workspace ----------------
#
# Same rule and the same reason as read_file: the path comes from a model, so
# canonicalise every component and compare against the root rather than looking
# at the string.

root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) \
  || fail 4 'cannot resolve the workspace root'

abs=$(realpath -e -- "$target" 2>/dev/null) \
  || fail 3 "no such file or directory: $target"

# The root itself is allowed, which read_file has no reason to permit: searching
# the whole workspace is this tool's default.
case "$abs" in
  "$root" | "$root"/*) ;;
  *) fail 4 "path resolves outside the workspace root: $target" ;;
esac

[[ -r $abs ]] || fail 4 "not readable: $target"

# --- build the grep options ---------------------------------------------------

asked() { # asked <flag-name>, true when the input set it
  [[ $(jq -r --arg f "$1" '.[$f] // false' <<<"$input") == true ]]
}

# -n unconditionally: a match is a line, and where it is is half of what was asked.
# There is deliberately no -l here. "Which files" by name is glob's question, and by
# content it is this output with the first field read off it.
options=(-n)
if asked fixed; then options+=(-F); else options+=(-E); fi
if asked ignore_case; then options+=(-i); fi

# --- search -------------------------------------------------------------------

search() {
  # /dev/null is handed to grep as an extra file on purpose. Given a single file,
  # grep prints matches without the filename prefix, so a batch that happened to
  # end with one file would emit lines in a different shape from all the others.
  # A second file that never matches forces the prefix on unconditionally.
  if [[ -d $abs ]]; then
    if [[ -n $glob ]]; then
      find "$target" -type f -name "$glob" -exec grep "${options[@]}" -e "$pattern" /dev/null -- {} +
    else
      find "$target" -type f -exec grep "${options[@]}" -e "$pattern" /dev/null -- {} +
    fi
  else
    grep "${options[@]}" -e "$pattern" /dev/null -- "$target"
  fi
}

# One line past the cap, so reaching it is distinguishable from landing on it
# exactly. `head` bounds what is held in memory: a bare pattern over a large tree
# can match a great deal, and none of it past the cap is ever printed.
#
# `|| true` covers grep exiting 1 for no match, which is not a failure here, and
# grep taking SIGPIPE when head closes the pipe after the cap.
found=$( { search || true; } | sed 's|^\./||' | head -n "$((max_matches + 1))" ) || true

if [[ -z $found ]]; then
  printf '[grep] no matches for %s\n' "$pattern"
  exit 0
fi

reported=$(printf '%s\n' "$found" | wc -l)
if (( reported > max_matches )); then
  printf '%s\n' "$found" | head -n "$max_matches"
  printf '[grep] output truncated at %d matches. Narrow the pattern or the path, or raise max_matches.\n' \
    "$max_matches"
else
  printf '%s\n' "$found"
fi
