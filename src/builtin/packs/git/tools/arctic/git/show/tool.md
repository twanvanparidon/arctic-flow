# git/show

One commit: who wrote it, when, why, and what it changed.

## Purpose

Turn a sha into the thing it stands for. `git/log` says a commit exists; this
says what it did.

## When to use it

- `git/log` gave you a sha and you need the change behind it.
- Writing a release note that quotes a commit's reasoning, which is in the body.
- Checking what a commit a flow just made actually recorded.

## When not to use it

- You want a range. That is `git/log`, with `body: true` if you need the messages.
- You want the difference between two branches. That is `git/diff` with `ref`.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter   | Type    | Required | Default | Notes                                |
| ----------- | ------- | -------- | ------- | ------------------------------------ |
| `ref`       | string  | no       | `HEAD`  | A sha, branch, tag, or `HEAD~2`.     |
| `path`      | string  | no       | none    | Only what it did to this path.       |
| `summary`   | boolean | no       | `false` | Counts per file instead of the diff. |
| `max_lines` | integer | no       | `400`   | Says so when it truncates.           |

## Example

```sh
echo '{"ref":"5c313e6"}' | src/builtin/packs/git/tools/arctic/git/show/run.sh
```

```
commit 5c313e6a94a1f0b6d2e8...
author Twan van Paridon <twan@example.com>
date   2026-08-09

refactor(paths): move the engine's namespace to arctic

The first segment says who a component came from.

diff --git a/src/paths/resolver.py b/src/paths/resolver.py
...
```

The header is this tool's own format rather than git's, so it lines up with what
`git/log` prints and can be split on a space.

## Merge commits carry no diff

git cannot choose which parent to compare a merge against, so it shows none. The
header and the message are still returned, and the call still succeeds. If you
need what a merge brought in, ask `git/log` for the range instead.

## Errors

| Exit | Means                                                            |
| ---- | ---------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, or a parameter is the wrong type.    |
| `3`  | the ref does not resolve to a commit, or the path does not exist. |
| `4`  | the repository is above the workspace, or the path leaves it.     |
| `5`  | the workspace is not a git repository, or `git` is not installed. |
