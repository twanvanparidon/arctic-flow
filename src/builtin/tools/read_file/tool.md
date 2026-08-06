# read_file

Return the contents of a text file from the workspace.

## Purpose

Ground answers and edits in what a file actually contains, rather than in what
the conversation so far implies it contains. Read before you edit; read before
you claim a file says something.

## When to use it

- You are about to edit a file and need its current contents.
- A question is about specific code, config, or data on disk.
- Something in the conversation contradicts what you'd expect the file to hold.

## When not to use it

- You already read the file this turn and nothing has changed it since.
- You don't know the path yet. Locate it first (`glob`/`grep`), then read.
- The target is a directory, a binary, or a file you only need to check the
  existence of.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter   | Type    | Required | Default | Notes                                                    |
| ----------- | ------- | -------- | ------- | -------------------------------------------------------- |
| `path`      | string  | yes      | none    | Relative to the workspace root. Must resolve inside it.  |
| `max_lines` | integer | no       | `500`   | Truncates at this many lines and says so in the output.  |

## Example

Input (one JSON object on stdin):

```json
{ "path": "src/app.py", "max_lines": 50 }
```

Run it directly, the same way the engine does:

```sh
echo '{"path":"src/app.py","max_lines":50}' | src/builtin/tools/read_file/run.sh
```

Output on stdout, the file verbatim:

```php
<?php

namespace App\Engine;

final class Loop
{
    // ...
}
```

If the file was longer than `max_lines`, a notice follows the content:

```
[read_file] output truncated: showing 50 of 812 lines. Raise max_lines to read the rest.
```

## Errors

Failures write one line to stderr and set an exit code from `spec.json`'s
`exit_codes`: `2` invalid input, `3` file not found, `4` not permitted (outside
the workspace root, not a regular file, or unreadable).

```
$ echo '{"path":"/etc/passwd"}' | src/builtin/tools/read_file/run.sh
read_file: path resolves outside the workspace root: /etc/passwd
$ echo $?
4
```

The path is fully canonicalised before that check, so `..` segments and symlinks
that leave the workspace are rejected too, not just absolute paths.

The engine is expected to surface stderr to the model as the tool result and
mark the call as failed, so the model can correct the path and retry rather than
inventing file contents.

## Adding another tool

Copy this directory. Each tool is a directory under `tools/` holding exactly
three files:

- `spec.json` is the machine-readable contract: parameter schema, how to invoke
  the script, exit codes, permissions. `input_schema` is a plain JSON Schema, so
  it can be handed to the model's tool-definition field unchanged.
- `tool.md` is the prose the model reads: what the tool is for, when to reach for
  it, when not to, and a worked example.
- `run.sh` is the implementation. Reads one JSON object on stdin, writes the
  result to stdout, writes errors to stderr, exits with a code from `spec.json`.

Nothing about the tool lives outside its directory, so a tool can be added,
removed, or swapped without touching the engine.
