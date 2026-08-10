#!/usr/bin/env bash
#
# Shared by every tool in the git pack. Sourced, never executed.
#
# The one thing in this pack that is not inside a tool directory, and the reason is the
# containment check below. It is a security check, and eight copies of a security check is
# eight places to fix it and seven places to forget. It lives outside `tools/` so that
# nothing under there is anything but a tool: the resolver walks that directory looking for
# `spec.json`, and a `lib` beside the tools would read as a namespace holding none.
#
# A tool sets TOOL to its own name, sources this, then calls `read_input` and `open_repo`.
#
#   TOOL=log
#   . "$(dirname -- "$0")/../../../../lib/git.sh"
#
# Exit codes, shared across the pack so one vocabulary covers all of it. A tool's
# spec.json lists only the ones it can actually produce:
#
#   0  ok
#   2  invalid input          stdin was not a JSON object, or a parameter is wrong
#   3  not found              no such ref, commit or path
#   4  not permitted          the repository is not the workspace, or a path leaves it
#   5  no repository          the workspace is not a git repository, or git is missing
#   6  refused                git declined: nothing staged, a conflict, a protected branch
#   7  no identity            a commit was asked for and git has no name and email to sign it

fail() { # fail <exit-code> <message>
  printf '%s: %s\n' "${TOOL:-git}" "$2" >&2
  exit "$1"
}

# --- input --------------------------------------------------------------------

read_input() { # sets $input to the JSON object on stdin
  input=$(cat)
  jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
    || fail 2 'stdin must be a single JSON object matching spec.json'
}

field() { # field <name> [default], the value or the default, empty for absent
  jq -r --arg f "$1" --arg d "${2-}" '.[$f] // $d | tostring' <<<"$input"
}

flag() { # flag <name>, true when the input set it to true
  [[ $(jq -r --arg f "$1" '.[$f] // false' <<<"$input") == true ]]
}

count() { # count <name> <default>, a positive integer parameter
  jq -e --arg f "$1" --argjson d "$2" \
    '(.[$f] // $d) | type == "number" and . >= 1 and . == floor' >/dev/null 2>&1 <<<"$input" \
    || fail 2 "parameter '$1' must be an integer >= 1"
  jq -r --arg f "$1" --argjson d "$2" '.[$f] // $d' <<<"$input"
}

# A ref is passed straight to git, so anything starting with a dash would be read as an
# option instead. Every call below also uses `--` where git accepts it, and this is the
# other half: `--end-of-options` is not in the git that ships on every LTS.
check_ref() { # check_ref <value> <parameter-name>
  case "$1" in
    -*) fail 2 "parameter '$2' must not start with '-'" ;;
  esac
}

# --- the repository -----------------------------------------------------------

open_repo() { # sets $root to the workspace, which must be the repository root
  root=$(cd -- "${AGENT_WORKSPACE:-$PWD}" && pwd -P) \
    || fail 4 'cannot resolve the workspace root'

  command -v git >/dev/null 2>&1 || fail 5 'git is not installed, or is not on PATH'

  # These redirect git at another repository entirely, and a flow run from a hook or
  # from a parent that exported one would silently act on it. Unset rather than
  # respected: the workspace is what a flow is about, and it is checked one line down.
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY

  local toplevel
  toplevel=$(git -C "$root" rev-parse --show-toplevel 2>/dev/null) \
    || fail 5 'the workspace is not a git repository'
  toplevel=$(cd -- "$toplevel" && pwd -P)

  # A repository *above* the workspace is the case this exists for, and it is the one
  # that is easy to miss: running a flow in `myrepo/subproject` would otherwise log,
  # diff and commit the whole of `myrepo`, which is not what the workspace says the flow
  # is about. Nothing can put the repository below the workspace, since git searches
  # upward from the directory it is given.
  [[ $toplevel == "$root" ]] \
    || fail 4 "the git repository is $toplevel, which is above the workspace. Run atf with --workspace $toplevel to work on it"
}

# Every git call in the pack goes through this.
#
# --no-pager because a pager on a terminal would wait for a keypress nobody is there to
# give. color.ui=false because the engine hands stdout to a template or a model, and
# escape codes there are noise that survives into a prompt. core.quotepath=false so a
# non-ASCII path comes out as itself rather than as octal escapes.
run_git() { # run_git <args...>
  git -C "$root" --no-pager -c color.ui=false -c core.quotepath=false "$@"
}

# run_git, with a failure turned into this pack's vocabulary rather than git's exit code.
#
# The exit code is the caller's to choose, because the same git failure means different
# things per tool: an unknown ref is `not found` to `show` and `refused` to `checkout`,
# which was asked to create it.
#
# **Assign the result, never pass it as an argument.** `found=$(try_git ...)` propagates a
# failure, because `set -e` stops the script when the assignment's subshell exits non-zero.
# `something "$(try_git ...)"` does not: the subshell dies, its message is printed, and the
# outer script carries on with an empty string and exit 0.
#
# stderr goes to a file rather than through a pipe, because a pipeline would leave `$?`
# holding the exit status of the reader instead of git's.
try_git() { # try_git <exit-code-on-failure> <git-args...>
  local code=$1 out err status=0
  shift

  err=$(mktemp) || fail "$code" 'cannot create a temporary file'
  out=$(run_git "$@" 2>"$err") || status=$?
  (( status == 0 )) || fail "$code" "$(git_reason "$err" "$status")"
  rm -f "$err"

  printf '%s' "$out"
}

# The one line out of git's stderr worth repeating.
#
# The first `fatal:` or `error:`, because what follows it is a hint: an unknown revision
# gets three lines, of which the last is a usage example and the first is the reason. A
# git that failed without either prefix falls back to its first line rather than to
# silence, since something was still wrong.
git_reason() { # git_reason <stderr-file> <status>
  local file=$1 status=$2 line
  line=$(grep -m 1 -E '^(fatal|error):' "$file" || true)
  [[ -n $line ]] || line=$(grep -m 1 -v '^[[:space:]]*$' "$file" || true)
  rm -f "$file"
  printf '%s' "${line:-git exited $status}"
}

# Print at most `max` lines of something, and say so when there were more.
#
# The count comes off the text already in hand rather than from a second pass, so the
# notice can name the total. Everything this bounds is a diff or a list that git produced
# in one call, so it is already in memory by the time this runs.
emit_bounded() { # emit_bounded <max> <unit> <text>
  local max=$1 unit=$2 text=$3 total
  [[ -n $text ]] || return 0
  total=$(printf '%s\n' "$text" | wc -l)

  if (( total > max )); then
    printf '%s\n' "$text" | head -n "$max"
    printf '[%s] truncated: showing %s of %s %s. Raise max_%s to see the rest.\n' \
      "$TOOL" "$max" "$total" "$unit" "$unit"
  else
    printf '%s\n' "$text"
  fi
}

# A path parameter, resolved and kept inside the workspace. Same rule and the same reason
# as the built-in read_file: a path from a model is untrusted, so canonicalise every
# component and compare against the root rather than pattern-matching the string.
#
# The path need not exist, because a commit may name one that was just deleted. So the
# parent is what gets resolved, and the leaf is appended to it.
check_path() { # check_path <path>, prints it relative to the root
  local given=$1 parent leaf abs
  [[ -n $given ]] || fail 2 'a path must not be empty'

  parent=$(dirname -- "$given")
  leaf=$(basename -- "$given")
  parent=$(cd -- "$root" && realpath -e -- "$parent" 2>/dev/null) \
    || fail 3 "no such directory: $(dirname -- "$given")"

  abs="$parent/$leaf"
  [[ $parent == "$root" ]] && abs="$root/$leaf"

  case "$abs" in
    "$root" | "$root"/*) ;;
    *) fail 4 "path resolves outside the workspace root: $given" ;;
  esac
  printf '%s' "${abs#"$root"/}"
}
