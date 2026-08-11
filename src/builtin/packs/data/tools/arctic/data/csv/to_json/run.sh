#!/usr/bin/env bash
#
# data/csv/to_json: read CSV, answer with JSON.
#
# Contract (authoritative version lives in spec.json):
#   stdin   one JSON object matching spec.json's input_schema
#   stdout  a JSON array: one object per row with a header, one array per row without
#   stderr  a single-line error message when the exit code is non-zero
#   exit    0 ok | 2 invalid input | 3 not found | 4 not permitted | 5 malformed data
#
# **Every value is a string.** A CSV field has no type: "007" is not 7, and a column of
# postcodes that lost its leading zeros is a bug nobody sees until much later. Read a
# number out with `json/query` and `tonumber` where you need arithmetic.
#
# **A row that does not fit the header is refused, not padded.** Filling the gap with nulls
# would answer a question the data cannot answer, and the row number is what someone needs
# to go and look at the file.
#
# The parser is the awk program below rather than `cut -d,`, because a quoted field may hold
# the delimiter, a newline, or a doubled quote, and all three appear in real exports.

set -euo pipefail

TOOL=data/csv/to_json
# shellcheck source-path=SCRIPTDIR source=../../../../../lib/data.sh
. "$(dirname -- "$0")/../../../../../lib/data.sh"

read_input

delimiter=$(jq -r '.delimiter // ","' <<<"$input")
# One character, because a delimiter is one character in every CSV anyone writes. The four
# refused ones are refused for the parser's sake: a quote is the escape, a backslash is
# taken as an escape by awk's -v, and `]` or `^` would change the meaning of the bracket
# expression the fast path splits on.
# The backslash is written `\\` rather than as a quoted `'\'`. Same pattern either way, and
# SC1003 reads the quoted spelling as a mistake.
case "$delimiter" in
  '"' | \\ | ']' | '^') fail 2 "parameter 'delimiter' must not be $delimiter" ;;
esac
[[ ${#delimiter} -eq 1 ]] || fail 2 "parameter 'delimiter' must be one character"

header=1
flag header true || header=0

load_data

# awk writes its own message to a file rather than to stderr directly, so the one line a
# reader sees carries the tool's name like every other failure in the pack does.
scratch=$(mktemp -d) || fail 5 'cannot create a temporary directory'
trap 'rm -rf "$scratch"' EXIT

status=0
printf '%s\n' "$data" | awk -v delim="$delimiter" -v use_header="$header" '
function bail(message) { print message > "/dev/stderr"; failed = 1; exit 5 }

# JSON needs \\, \" and the control characters escaped. The fast path is the common one:
# a field with none of them is already its own JSON string.
#
# Escaping by hand rather than with gsub, because gsub reprocesses its replacement and
# doubling a backslash there takes eight of them in the source. A loop says what it does.
function esc(s,   i, c, o) {
  if (index(s, "\\") == 0 && index(s, "\"") == 0 && index(s, "\n") == 0 \
      && index(s, "\r") == 0 && index(s, "\t") == 0) return "\"" s "\""
  o = ""
  for (i = 1; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (c in ctl) o = o ctl[c]
    else o = o c
  }
  return "\"" o "\""
}

function push(v) { nf++; fields[nf] = v }

# One record, character by character, continuing where the last line left off when a quoted
# field ran over the end of it.
function scan(s,   i, n, c, nxt) {
  n = length(s)
  for (i = 1; i <= n; i++) {
    c = substr(s, i, 1)
    if (inq) {
      if (c != "\"") { cur = cur c; continue }
      nxt = substr(s, i + 1, 1)
      # A doubled quote inside a quoted field is one quote, and the only escape CSV has.
      if (nxt == "\"") { cur = cur "\""; i++; continue }
      inq = 0
      continue
    }
    # A quote opens a field only at its start. Anywhere else it is a literal, which is what
    # a spreadsheet writing `6" pipe` into an unquoted column produces.
    if (c == "\"" && !started) { inq = 1; started = 1; opened_at = NR; continue }
    if (c == delim) { push(cur); cur = ""; started = 0; continue }
    cur = cur c
    started = 1
  }
}

function emit(   i, out) {
  if (use_header && !have_header) {
    for (i = 1; i <= nf; i++) {
      if (fields[i] == "") bail("column " i " of the header row has no name, so a row would have a field nothing could read it by")
      if (fields[i] in named) bail("the header names \"" fields[i] "\" twice, and one JSON object cannot carry both. Rename a column, or pass header: false to read the rows as arrays")
      named[fields[i]] = 1
      keys[i] = fields[i]
    }
    width = nf
    have_header = 1
    nf = 0
    return
  }

  if (width == 0) width = nf   # without a header the first row is what sets the shape
  if (nf != width) bail("line " NR " has " nf " " (nf == 1 ? "field" : "fields") " where the rest have " width ". A row that does not fit is a data error, not a row of nulls")

  out = ""
  for (i = 1; i <= nf; i++) {
    if (i > 1) out = out ","
    if (use_header) out = out esc(keys[i]) ":" esc(fields[i])
    else out = out esc(fields[i])
  }
  printf "%s", (rows++ ? "," : "")
  printf (use_header ? "{%s}" : "[%s]"), out
  nf = 0
}

BEGIN {
  # No portable ord(), so a table instead. From 1 because a NUL byte cannot reach here:
  # the shell drops it out of a command substitution long before awk sees it.
  for (i = 1; i < 32; i++) ctl[sprintf("%c", i)] = sprintf("\\u%04x", i)
  ctl["\b"] = "\\b"; ctl["\t"] = "\\t"; ctl["\n"] = "\\n"
  ctl["\f"] = "\\f"; ctl["\r"] = "\\r"
  ctl["\\"] = "\\\\"; ctl["\""] = "\\\""
  printf "["
}

{
  line = $0
  sub(/\r$/, "", line)   # CRLF endings, including inside a quoted field, become \n

  # A blank line is skipped rather than read as a row holding one empty field. Every CSV
  # writer ends the file with a newline, and a row of nulls in the middle of the answer is
  # not what a blank line in a spreadsheet means.
  if (!inq && line == "") next

  # No quote on the line means the delimiter cannot be inside a field, so one split does the
  # whole record. The scanner below is right either way and several times slower, and this
  # is the case almost every file is.
  if (!inq && index(line, "\"") == 0) {
    nf = 0
    n = split(line, part, "[" delim "]")
    for (i = 1; i <= n; i++) push(part[i])
    emit()
    next
  }

  if (inq) cur = cur "\n"
  scan(line)
  if (inq) next          # the field runs on into the next line
  push(cur); cur = ""; started = 0
  emit()
}

END {
  if (failed) exit 5
  if (inq) bail("a quoted field opens on line " opened_at " and is never closed")
  printf "]\n"
}
' >"$scratch/out" 2>"$scratch/err" || status=$?

if (( status != 0 )); then
  fail 5 "$(grep -m 1 -v '^[[:space:]]*$' "$scratch/err" || printf 'awk exited %s' "$status")"
fi

# jq is the second half of the escaping above: the awk fast path leaves a control character
# other than a tab, a newline or a return unescaped, and this is where that stops being
# something a reader downstream has to notice. Pretty-printed, because the result is read by
# a person or a prompt as often as by another tool.
jq . <"$scratch/out" 2>/dev/null \
  || fail 5 'the CSV could not be encoded as JSON. A control character inside a field is the usual cause'
