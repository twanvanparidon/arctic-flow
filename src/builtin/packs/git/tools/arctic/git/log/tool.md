# git/log

Recent commits, newest first.

## Purpose

Answer "what has happened here" from the record rather than from the working
tree. Release notes, a changelog, a summary of the week, and finding the commit
that introduced something all start here.

## When to use it

- Building release notes or a changelog from a range.
- Finding out when a file last changed, with `path`.
- Establishing what a branch contains that another does not, with a range ref.
- Getting a sha to hand to `git/show`.

## When not to use it

- You want what a commit did, not that it exists. That is `git/show`.
- You want uncommitted work. That is `git/status` or `git/diff`.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter     | Type    | Required | Default | Notes                                       |
| ------------- | ------- | -------- | ------- | ------------------------------------------- |
| `ref`         | string  | no       | `HEAD`  | A branch, tag, commit, or a `a..b` range.   |
| `path`        | string  | no       | none    | Only commits touching this path.            |
| `body`        | boolean | no       | `false` | Include each message body.                  |
| `max_commits` | integer | no       | `20`    | Says so when it truncates.                  |

## Example

```sh
echo '{"max_commits":3}' | src/builtin/packs/git/tools/arctic/git/log/run.sh
```

```
c6752b7 2026-08-09 Twan van Paridon  Merge pull request #49
5c313e6 2026-08-09 Twan van Paridon  refactor(paths): move the engine's namespace to arctic
185af31 2026-08-08 Twan van Paridon  Merge pull request #47
```

With `body`, the subject and body are indented under a header line instead:

```
5c313e6 2026-08-09 Twan van Paridon
    refactor(paths): move the engine's namespace to arctic
    Renames the reserved namespace and moves every shipped component.
```

Everything since a tag, which is what a release note is built from:

```json
{ "ref": "v0.2.0..HEAD", "body": true, "max_commits": 100 }
```

## The date and the name

`YYYY-MM-DD`, always, and the author's name rather than the email. A log that is
read by a model and compared across calls needs one shape, and git's default
date format is the machine's locale.

## Empty is not an error

A fresh `git init` has no commits, and a `path` may have been touched by none.
Both exit `0` with a `[git/log]` notice. A flow asking about history has to
survive the answer being "none", or it cannot run on a repository it just made.

## Errors

| Exit | Means                                                            |
| ---- | ---------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, or a parameter is the wrong type.    |
| `3`  | the ref does not resolve, or the path does not exist.             |
| `4`  | the repository is above the workspace, or the path leaves it.     |
| `5`  | the workspace is not a git repository, or `git` is not installed. |
