#!/usr/bin/env bash
#
# edit_file: replace an exact string in a text file in the workspace.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  one line naming what changed, with no trailing newline
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 no such file | 4 not permitted
#           | 5 not a text file | 6 no match | 7 several matches
#
# The engine runs this with cwd set to the workspace root, so relative paths in
# the input resolve the way the model expects.

set -euo pipefail

fail() { # fail <exit-code> <message>
  printf 'edit_file: %s\n' "$2" >&2
  exit "$1"
}

input=$(cat)

# --- parse and validate input -------------------------------------------------
#
# old_string and new_string are never lifted into shell variables. Command
# substitution strips trailing newlines, so an old_string ending in one would
# stop matching the text it was copied from. Every jq call below reads them from
# the input object instead, where they stay exactly as they arrived.

jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'stdin must be a single JSON object matching spec.json'

path=$(jq -re '.path // empty' <<<"$input") \
  || fail 2 "missing required parameter 'path'"
[[ -n $path ]] || fail 2 "parameter 'path' must be a non-empty string"

# An empty old_string matches everywhere and nowhere in particular, so it is
# refused rather than read as "the start of the file". Putting whole contents on
# disk is write_file's job.
jq -e '(.old_string | type) == "string" and (.old_string | length) > 0' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'old_string' must be a non-empty string"

# has(), not '.new_string // empty': an empty string is a legitimate replacement,
# and deleting the matched text is the reason to pass one.
jq -e 'has("new_string") and (.new_string | type) == "string"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'new_string' must be a string"

# Refused rather than reported as a successful no-op, which would tell a caller
# its edit landed when the file never changed.
jq -e '.old_string != .new_string' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'old_string and new_string are identical, so there is nothing to change'

jq -e '(.replace_all // false) | type == "boolean"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "parameter 'replace_all' must be a boolean"
replace_all=$(jq -r '.replace_all // false' <<<"$input")

# --- resolve the path and keep it inside the workspace -----------------------
#
# A model-supplied path is untrusted input, so resolve to a canonical absolute
# path first and compare against the root rather than pattern-matching the raw
# string. Lexical collapse is why the check runs on the result and never on the
# raw string: "ghost/../../../etc/passwd" only reads as an escape once realpath
# has folded it.
#
# `-m` rather than read_file's `-e`, even though this tool also needs the file to
# exist. `-e` fails on a missing component, so it would answer "../absent.txt"
# with "no such file" and only report an escape for a path that happens to exist,
# which both buries the useful half of the message and answers a question about
# outside the workspace. `-m` canonicalises either way, so containment is decided
# first and existence is a separate check below.
#
# -m is safe to check against: it resolves every symlink it finds, including a
# final component that is one and including a dangling one, so a link out of the
# tree is caught here rather than followed.

root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) \
  || fail 4 'cannot resolve the workspace root'

abs=$(realpath -m -- "$path" 2>/dev/null) \
  || fail 2 "cannot resolve the path: $path"

case "$abs" in
  "$root"/*) ;;
  *) fail 4 "path resolves outside the workspace root: $path" ;;
esac

# There has to be a file already. Creating one is write_file's job, and a typo in
# a path should be reported as a typo rather than quietly starting a new file.
[[ -e $abs ]] || fail 3 "no such file: $path"
[[ -f $abs ]] || fail 4 "not a regular file: $path"
[[ -r $abs ]] || fail 4 "not readable: $path"
[[ -w $abs ]] || fail 4 "not writable: $path"

# --- refuse anything jq cannot carry byte for byte ---------------------------
#
# The edit is done in jq, which works on decoded text rather than bytes, and what
# it does with a byte it cannot decode depends on the version installed: 1.8
# passes it through, older ones substitute U+FFFD. On those, editing one line of
# a Latin-1 file would rewrite every other line as replacement characters and
# report success.
#
# So rather than trust the version, ask this jq about this file: read it back
# through the same call the edit uses and compare. What survives can be edited,
# and what does not is refused on every version alike.
#
# -jn, and the -n is load-bearing. Without it jq waits for an input value on a
# stdin the engine's caller already drained to EOF, then emits nothing at all, so
# every file would compare unequal and be refused as binary.
jq -jn --rawfile content "$abs" '$content' 2>/dev/null | cmp -s - "$abs" \
  || fail 5 "not a text file: $path. Only text this tool can read back unchanged may be edited"

# --- count the matches -------------------------------------------------------
#
# split/1 splits on a literal string, so the match needs no escaping and an
# old_string full of regex metacharacters means what it says. sub/gsub would read
# it as a pattern instead.
#
# `. as $in` first, because `$content | split(.old_string)` would look up
# old_string on the piped string rather than on the input object, and fail with
# "cannot index string with string".

count=$(jq -r --rawfile content "$abs" \
  '. as $in | ($content | split($in.old_string) | length) - 1' <<<"$input") \
  || fail 5 "could not read: $path"

(( count > 0 )) \
  || fail 6 "old_string does not appear in $path. Read the file and copy the text to replace exactly"

if (( count > 1 )) && [[ $replace_all != true ]]; then
  fail 7 "old_string appears $count times in $path. Include the surrounding lines to make it unique, or set replace_all"
fi

# --- apply it ----------------------------------------------------------------

tmp=$(mktemp) || fail 4 'cannot create a temporary file'

# The engine cancels a step with TERM before KILL, and bash's default action for
# a signal does not run an EXIT trap, so removing the temporary file takes both.
trap 'rm -f -- "$tmp"' EXIT
trap 'exit 143' HUP INT TERM

# Staged through a temporary file rather than redirected at the target. `>"$abs"`
# truncates before jq runs, so --rawfile would read the file it was meant to edit
# as empty and the result would be an empty file. Staging also means a jq that
# fails leaves the original untouched.
#
# -j, not -r: -r appends a newline the file never had.
jq -j --rawfile content "$abs" '
  . as $in
  | ($content | split($in.old_string)) as $parts
  | if ($in.replace_all // false)
    then $parts | join($in.new_string)
    # Everything after the first match is rejoined with the original, so only
    # the leading occurrence changes.
    else $parts[0] + $in.new_string + ($parts[1:] | join($in.old_string))
    end
' <<<"$input" >"$tmp" \
  || fail 5 "could not edit: $path"

# Copied over the target rather than renamed onto it. A rename would be atomic,
# but it replaces the inode, so the file's mode would become mktemp's 0600.
# Writing in place keeps whatever permissions the file already had. What that
# costs is stated in tool.md, and write_file makes the same trade for the same
# reason: a write that fails part way leaves a partial file.
cat <"$tmp" >"$abs" \
  || fail 4 "could not write: $path"

# No trailing newline: a single-value output gets templated mid-line.
if (( count == 1 )); then
  printf 'replaced 1 occurrence in %s' "$path"
else
  printf 'replaced %d occurrences in %s' "$count" "$path"
fi
