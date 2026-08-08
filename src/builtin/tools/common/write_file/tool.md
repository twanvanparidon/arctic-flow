# write_file

Write a text file into the workspace, creating it or replacing one already there.

## Purpose

Put a result on disk. An answer that only exists in the conversation is gone
when the turn ends, so anything the next step or the next person has to read
belongs in a file.

## When to use it

- A result has to outlive the turn: a report, a generated config, a patch note.
- A later step in the flow reads the file rather than the model's answer.
- You are replacing a file whose full contents you already have.

## When not to use it

- The value is small and the flow can template it straight into the next step.
  A step's output is already available as `{{ steps.<id>.text }}`.
- You have not read the file you are about to replace. This overwrites whole
  files; read it first so you are not discarding something you did not know was
  there.
- The parent directory does not exist yet. This tool will not create it, on
  purpose: see Errors.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter   | Type    | Required | Default | Notes                                                       |
| ----------- | ------- | -------- | ------- | ----------------------------------------------------------- |
| `path`      | string  | yes      | none    | Relative to the workspace root. Must resolve inside it.     |
| `content`   | string  | yes      | none    | Written verbatim. No newline is added.                      |
| `overwrite` | boolean | no       | `false` | Left false, an existing path is refused rather than clobbered. |

## Example

Input (one JSON object on stdin):

```json
{ "path": "out/summary.md", "content": "# Summary\n\nAll clear.\n" }
```

Run it directly, the same way the engine does:

```sh
echo '{"path":"out/summary.md","content":"# Summary\n"}' | src/builtin/tools/common/write_file/run.sh
```

Output on stdout, one line with no trailing newline:

```
wrote 11 bytes to out/summary.md
```

## Errors

Failures write one line to stderr and set an exit code from `spec.json`'s
`exit_codes`: `2` invalid input, `3` no such directory, `4` not permitted
(outside the workspace root, not a regular file, or unwritable), `5` the file
exists and `overwrite` was not set.

```
$ echo '{"path":"/etc/passwd","content":"x"}' | src/builtin/tools/common/write_file/run.sh
write_file: path resolves outside the workspace root: /etc/passwd
$ echo $?
4
```

A missing parent directory is reported rather than created. Creating it would
make a typo in a path leave a stray directory behind instead of being reported
as a typo, and the caller is better placed to know which of the two it meant.

## Why there is no append mode

An agent step can carry a gate, and a rejected answer re-runs the whole turn
from the original prompt. A tool that appended would append again on every
attempt, so a step that took three tries would write its output three times
over. Overwriting is idempotent under that retry; appending is not.

To add to a file, read it, then write the whole thing back.

## What the containment check covers

The path is fully canonicalised before it is checked against the workspace root,
so `..` segments and symlinks that leave the workspace are refused, not just
absolute paths. That includes a symlink whose target exists outside the tree and
one that dangles.

It is resolved once and then written, so a symlink created in between defeats
it, the same as `read_file`. The workspace boundary is a guard rail against a
wrong path, not a sandbox against a hostile one.
