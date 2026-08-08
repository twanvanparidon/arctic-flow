# shellcheck shell=bash
#
# Bash completion for atf. Install it with:
#
#   eval "$(atf completion bash)"
#
# Nothing about the interface is written down here. The candidates come from
# `atf __complete`, which reads them off the parser and the component lookup, so a new
# command, a new flag or a new flow completes without this file changing.

_atf_complete() {
  # The word under the cursor is sent even when it is empty, so `atf run <TAB>` asks a
  # different question from `atf run f<TAB>`. Reading the index rather than slicing up to
  # it is what keeps that empty word: a slice would drop it. The `-` supplies it as empty
  # rather than tripping a shell running with `set -u`.
  local words=("${COMP_WORDS[@]:1:COMP_CWORD-1}" "${COMP_WORDS[COMP_CWORD]-}")

  # "$1" rather than a literal atf: bash passes the command being completed as the first
  # argument, so the answers come from the same build being typed about. That is what lets a
  # binary at a path be registered below and complete against itself.
  #
  # Everything after `--`, so a flag being completed arrives as a word to answer about
  # instead of being parsed. stderr goes nowhere: a message here would be painted over the
  # command line still being typed.
  mapfile -t COMPREPLY < <("$1" __complete -- "${words[@]}" 2>/dev/null)
}

# -o default falls back to filename completion when there are no candidates, which is what
# makes a path to a flow file and a vault completable without naming them here.
#
# Another name, or a build somewhere else, is one more line of the same:
#
#   complete -F _atf_complete -o default -o bashdefault ./dist/atf/atf
complete -F _atf_complete -o default -o bashdefault atf
