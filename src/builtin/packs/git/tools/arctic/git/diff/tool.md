# git/diff

What changed, as a unified diff.

## Purpose

Read the actual change rather than a list of the files it touched. Writing a
commit message, reviewing work, and deciding whether a change is what was asked
for all need the lines, not the paths.

## When to use it

- Before `git/commit`, with `staged: true`, to see what would be recorded.
- To review what a previous step wrote.
- To compare a branch against `main`, with `ref`.

## When not to use it

- You only need to know *whether* anything changed. That is `git/status`, which
  is cheaper and answers in one word.
- The change might be large and you do not yet know. Ask with `summary: true`
  first, then diff the paths that matter.
- You want one commit's change. That is `git/show`.

## Three questions, not one

| Input                              | Answers                                    |
| ---------------------------------- | ------------------------------------------ |
| `{}`                               | what is changed but not staged             |
| `{"staged": true}`                 | what the next commit would record          |
| `{"ref": "main"}`                  | what this tree has that `main` does not    |
| `{"staged": true, "ref": "main"}`  | what the next commit would look like against `main` |

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter   | Type    | Required | Default | Notes                                    |
| ----------- | ------- | -------- | ------- | ---------------------------------------- |
| `staged`    | boolean | no       | `false` | Diff the index instead of the worktree.  |
| `ref`       | string  | no       | none    | Diff against this branch, tag or commit. |
| `path`      | string  | no       | none    | Limit to one path.                       |
| `summary`   | boolean | no       | `false` | Counts per file instead of the diff.     |
| `max_lines` | integer | no       | `400`   | Says so when it truncates.               |

## Example

```sh
echo '{"summary":true}' | src/builtin/packs/git/tools/arctic/git/diff/run.sh
```

```
 src/app.py   | 12 ++++++++----
 src/db.py    |  3 +--
 2 files changed, 13 insertions(+), 6 deletions(-)
```

Then the one that matters:

```json
{ "path": "src/app.py" }
```

## There is no way to ask for all of it

`max_lines` has a default and no "unlimited". A diff is the one output in this
pack with no natural bound, and it usually goes straight into a prompt, where
every line is paid for. Narrow with `path`, or ask for `summary` first. Raising
`max_lines` is the last resort rather than the first move.

## Errors

| Exit | Means                                                            |
| ---- | ---------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, or a parameter is the wrong type.    |
| `3`  | the ref does not resolve, or the path does not exist.             |
| `4`  | the repository is above the workspace, or the path leaves it.     |
| `5`  | the workspace is not a git repository, or `git` is not installed. |

Nothing to diff is exit `0` and the line `[git/diff] no changes`, so a flow that
diffs before deciding whether to commit can read it as an answer.
