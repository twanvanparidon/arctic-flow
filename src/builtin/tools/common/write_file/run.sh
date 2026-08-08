#!/usr/bin/env bash
#
# write_file: write a text file into the workspace.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one line naming what was written, with no trailing newline
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 no such directory | 4 not permitted
#           | 5 already exists
#
# The engine runs this with cwd set to the workspace root, so relative paths in
# the input resolve the way the model expects.

set -euo pipefail

fail() { # fail <exit-code> <message>
  printf 'write_file: %s\n' "$2" >&2
  exit "$1"
}

input=$(cat)

# --- parse and validate input -------------------------------------------------

jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'stdin must be a single JSON object matching spec.json'

path=$(jq -re '.path // empty' <<<"$input") \
  || fail 2 "missing required parameter 'path'"
[[ -n $path ]] || fail 2 "parameter 'path' must be a non-empty string"

# has(), not '.content // empty': an empty string is a legitimate file to write,
# and jq's // treats it as absent.
jq -e 'has("content") and (.content | type == "string")' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'content' must be a string"

jq -e '(.overwrite // false) | type == "boolean"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'overwrite' must be a boolean"
overwrite=$(jq -r '.overwrite // false' <<<"$input")

# --- resolve the path and keep it inside the workspace -----------------------
#
# A model-supplied path is untrusted input, so resolve to a canonical absolute
# path first and compare against the root rather than pattern-matching the raw
# string.
#
# read_file uses `realpath -e`, which resolves every component and fails if any
# is missing. That is wrong here: the file being written usually does not exist
# yet, so -e would refuse the ordinary case. Canonicalising only the parent is
# the obvious way out and is exactly the hole read_file's comment warns about,
# because a final component that is a symlink out of the tree would then have an
# acceptable parent and an escaping target.
#
# -m is the split that is wanted. It resolves every symlink it finds, including
# a final component that is one and including a dangling one, and takes what is
# genuinely missing lexically. So `link -> /etc/hosts` still resolves to
# /etc/hosts and is refused below, while `notes/new.md` resolves inside.
#
# Lexical collapse is also why the check runs on the result and never on the raw
# string: "ghost/../../../etc/passwd" only reads as an escape once -m has folded
# it.

root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) \
  || fail 4 'cannot resolve the workspace root'

abs=$(realpath -m -- "$path" 2>/dev/null) \
  || fail 2 "cannot resolve the path: $path"

case "$abs" in
  "$root"/*) ;;
  *) fail 4 "path resolves outside the workspace root: $path" ;;
esac

# The parent has to exist. Creating it would make this tool do a second thing,
# and then a typo in a path leaves a stray directory behind instead of being
# reported as a typo.
parent=${abs%/*}
[[ -d $parent ]] || fail 3 "no such directory for: $path"
[[ -w $parent ]] || fail 4 "directory is not writable for: $path"

if [[ -e $abs ]]; then
  [[ -f $abs ]] || fail 4 "not a regular file: $path"
  [[ $overwrite == true ]] || fail 5 "file exists: $path. Set overwrite to replace it"
  [[ -w $abs ]] || fail 4 "not writable: $path"
fi

# --- write it ----------------------------------------------------------------

# Straight to the target rather than to a temporary file that is then renamed.
# Renaming would be atomic, but it also replaces the inode, so an existing
# file's mode would become mktemp's 0600. Truncating in place keeps whatever
# permissions the file already had. What that costs is stated in tool.md: a
# write that fails part way leaves a partial file.
#
# -j, not -r: -r appends a newline, and what the caller asked to write is what
# should land on disk.
jq -j '.content' <<<"$input" >"$abs" \
  || fail 4 "could not write: $path"

# No trailing newline: a single-value output gets templated mid-line.
printf 'wrote %s bytes to %s' "$(wc -c <"$abs")" "$path"
