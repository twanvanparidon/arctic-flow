#!/usr/bin/env bash
#
# glob: list paths in the workspace matching a shell pattern.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one path per line, relative to the workspace root, sorted
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted
#
# Matching nothing exits 0, for the reason grep's does: the engine fails a step on
# any non-zero exit, and "there are none" is an answer a flow has to be able to get.
#
# **Only what POSIX guarantees**, as in grep. -name and -path are both specified;
# -regex, -printf and -newermt are not, and the GNU and BSD versions differ on
# them. Sorting is `sort` rather than mtime order for the same reason: reading a
# modification time portably needs a flag no two `find`s spell the same way, and a
# sorted list has the better property anyway of being the same twice running.

set -euo pipefail

fail() { # fail <exit-code> <message>
  printf 'glob: %s\n' "$2" >&2
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

kind=$(jq -r '.type // "file"' <<<"$input")
case "$kind" in
  file | dir | any) ;;
  *) fail 2 "parameter 'type' must be one of file, dir, any" ;;
esac

jq -e '(.max_results // 200) | type == "number" and . >= 1 and . == floor' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'max_results' must be an integer >= 1"
max_results=$(jq -r '.max_results // 200' <<<"$input")

# --- resolve the search path and keep it inside the workspace ----------------

root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) \
  || fail 4 'cannot resolve the workspace root'

abs=$(realpath -e -- "$target" 2>/dev/null) \
  || fail 3 "no such file or directory: $target"

# The root itself is allowed: searching the whole workspace is the default.
case "$abs" in
  "$root" | "$root"/*) ;;
  *) fail 4 "path resolves outside the workspace root: $target" ;;
esac

[[ -d $abs ]] || fail 4 "not a directory: $target"
[[ -r $abs ]] || fail 4 "not readable: $target"

# --- search -------------------------------------------------------------------

selector=()
case "$kind" in
  file) selector=(-type f) ;;
  dir) selector=(-type d) ;;
esac

# A pattern with a slash in it is about the path, so it goes to -path; without one
# it is about the file name, and -name is what a caller means by "*.py". The two
# differ in more than what they compare: -path's `*` matches `/` as well, which is
# what makes "src/*_test.py" reach any depth rather than exactly one level.
#
# -path compares against the whole path as `find` printed it, which starts with the
# search root. Prefixing the pattern the same way is what lets a caller write it
# relative to the workspace, as they wrote `path`.
matcher=()
if [[ $pattern == */* ]]; then
  # `find <dir>` prints paths beginning with <dir> exactly as it was given, so the
  # pattern is prefixed the same way. `.` is the default and prints "./x".
  matcher=(-path "${target%/}/$pattern")
else
  matcher=(-name "$pattern")
fi

# One line past the cap, so reaching it is distinguishable from landing on it
# exactly. `sort` before the cap, or which paths survive truncation would depend on
# directory order and two runs would disagree.
#
# `! -path "$target"` drops the search root itself, which `find` reports as its first
# result and which nobody asked about: the question is what is *in* the tree. It only
# ever showed up for type dir or any, and `-mindepth 1` is the other way to say it and
# is not POSIX.
found=$( { find "$target" ! -path "$target" "${selector[@]}" "${matcher[@]}" || true; } \
  | sed 's|^\./||' | LC_ALL=C sort | head -n "$((max_results + 1))" ) || true

if [[ -z $found ]]; then
  printf '[glob] no matches for %s\n' "$pattern"
  exit 0
fi

reported=$(printf '%s\n' "$found" | wc -l)
if (( reported > max_results )); then
  printf '%s\n' "$found" | head -n "$max_results"
  printf '[glob] output truncated at %d paths. Narrow the pattern or the path, or raise max_results.\n' \
    "$max_results"
else
  printf '%s\n' "$found"
fi
