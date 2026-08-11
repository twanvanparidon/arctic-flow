# edit_file

Replace an exact string in a text file in the workspace, leaving the rest of it
untouched.

## Purpose

Change part of a file without handling all of it. `write_file` needs the whole
contents, so a one line change to a thousand line file means reading a thousand
lines and writing them back. This takes the old text and the new text, and the
file's size stops mattering.

## When to use it

- A small change to a file already on disk: a version string, a flag, one
  function.
- Deleting a block, by passing an empty `new_string`.
- A file too large to read in full, where the part to change is known.

## When not to use it

- The file does not exist yet, or you are replacing all of it. That is
  `write_file`.
- You have not read the text you are replacing. `old_string` is matched byte for
  byte, so a remembered approximation of a line will not match it.
- The change is scattered across a file and each part needs different text.
  Several edits are several calls, and at some point reading the file and writing
  it back is the shorter route.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter     | Type    | Required | Default | Notes                                                             |
| ------------- | ------- | -------- | ------- | ----------------------------------------------------------------- |
| `path`        | string  | yes      | none    | Relative to the workspace root. Must resolve inside it, and exist. |
| `old_string`  | string  | yes      | none    | Matched literally, never as a pattern. Must appear once, unless `replace_all`. |
| `new_string`  | string  | yes      | none    | Inserted verbatim. Empty deletes the match. Must differ from `old_string`. |
| `replace_all` | boolean | no       | `false` | Left false, a string appearing more than once is refused.          |

## Example

Input (one JSON object on stdin):

```json
{
  "path": "src/app.py",
  "old_string": "TIMEOUT = 30",
  "new_string": "TIMEOUT = 120"
}
```

Run it directly, the same way the engine does:

```sh
echo '{"path":"src/app.py","old_string":"TIMEOUT = 30","new_string":"TIMEOUT = 120"}' \
  | src/builtin/tools/arctic/edit_file/run.sh
```

Output on stdout, one line with no trailing newline:

```
replaced 1 occurrence in src/app.py
```

The count is worth reading back when `replace_all` is set, because it is the
number of places that changed and nothing else reports it.

## Errors

Failures write one line to stderr and set an exit code from `spec.json`'s
`exit_codes`: `2` invalid input, `3` no such file, `4` not permitted (outside the
workspace root, not a regular file, or not readable and writable), `5` not a text
file, `6` no match, `7` several matches.

```
$ echo '{"path":"src/app.py","old_string":"TIMEOUT = 5","new_string":"TIMEOUT = 9"}' \
    | src/builtin/tools/arctic/edit_file/run.sh
edit_file: old_string does not appear in src/app.py. Read the file and copy the text to replace exactly
$ echo $?
6
```

Nothing is written on any of them. The file is only touched once the match has
been found and counted.

## Why the match has to be unique

A string appearing three times gives three possible edits, and picking one is a
guess about which the caller meant. So the default refuses, and says how many
were found, which is the fact needed to fix the call: add the line above and the
line below, or say `replace_all` and mean all three.

There is no line number parameter for the same reason in reverse. A line number
is right only for the version of the file that was read, and a stale one still
matches something. It would edit the wrong place quietly, where a string that has
moved fails loudly.

## Why a second run does not edit twice

`write_file` is idempotent, and says why that matters: a step in a loop runs again
on every pass, so a tool that appended would append again each time.

This tool is not idempotent, and it does not need to be. Once the edit has
landed, `old_string` is gone, so a re-run finds no match and exits `6` without
writing. The second pass fails rather than corrupting the file.

So in a loop, a step that edits the same text on a second pass fails. Have the
flow read the file again between passes rather than assume the first edit is
still to be made.

## Why a file it cannot read as text is refused

The replacement is done in `jq`, which works on decoded text rather than bytes.
What it does with a byte it cannot decode depends on the version installed: 1.8
passes it through, older ones substitute U+FFFD. On those, editing one line of a
Latin-1 file would rewrite every other line as replacement characters and report
success.

So the file is read back through `jq` and compared against itself before anything
is written, and refused when the bytes differ. That asks about this file and this
`jq`, rather than about an encoding the file claims. What survives can be edited,
and what does not is refused the same way on every version.

## What the containment check covers

The path is fully canonicalised before it is checked against the workspace root,
so `..` segments and symlinks that leave the workspace are refused, not just
absolute paths. That includes a symlink whose target exists outside the tree and
one that dangles.

Containment is decided before existence, so a path leaving the workspace is
reported as leaving it whether or not anything is there. The alternative answers
"no such file" for `../absent.txt`, which buries the useful half of the message
and reports on a path outside the workspace.

It is resolved once and then written, so a symlink created in between defeats it,
the same as `read_file`. The workspace boundary is a guard rail against a wrong
path, not a sandbox against a hostile one.

## What a failed write leaves behind

The new contents are built in a temporary file first, so a failure while
computing the edit leaves the original exactly as it was. Copying it over the
target is the one step that can leave a partial file: it writes in place rather
than renaming, which keeps the file's mode instead of replacing the inode.
`write_file` makes the same trade for the same reason.
