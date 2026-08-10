# git/add

Stage named paths for the next commit.

## Purpose

Decide what goes into a commit, one path at a time, so that the decision is
visible in the flow rather than implied by whatever the working tree held.

## When to use it

- After a step wrote files, to stage the ones the commit is about.
- Before `git/commit`, which stages nothing itself.

## When not to use it

- You do not yet know what changed. Call `git/status` first.
- You want to stage everything. You cannot, and that is the point.

## There is no "stage everything"

`git add -A` is how an unrelated file, a build artefact, or a stray `.env` ends
up in a commit nobody reviewed. A person typing it has just read their working
tree; a flow calling a tool has not. So paths are named.

Naming a directory stages what is under it, which covers the honest case of "this
whole feature directory" without covering the whole repository by accident.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type            | Required | Default | Notes                                       |
| --------- | --------------- | -------- | ------- | ------------------------------------------- |
| `path`    | string or array | yes      | none    | Relative to the workspace. Must resolve inside it. |

## Example

```sh
echo '{"path":["src/app.py","src/db.py"]}' \
  | src/builtin/packs/git/tools/arctic/git/add/run.sh
```

```
src/app.py
src/db.py
```

## What comes back is the index, not the call

The output is every path staged **after** the call, not only what this call
added. That is what the next commit would record, which is the question worth
answering, and it means a flow can stage twice and check once.

## One bad path changes nothing

Every path is resolved and checked against the workspace root before anything is
staged, so a call naming one path outside the tree stages none of them. You never
get a partial index with an error about the rest, which would be easy to commit
by mistake.

Staging something that already matches the index is not a failure. It leaves the
index empty and returns a `[git/add] nothing staged` notice, so a flow that adds
then commits reaches the commit and is told there is nothing in it.

## Errors

| Exit | Means                                                            |
| ---- | ---------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, or `path` is missing or wrong.       |
| `3`  | a path's parent directory does not exist.                         |
| `4`  | the repository is above the workspace, or a path leaves it.       |
| `5`  | the workspace is not a git repository, or `git` is not installed. |
| `6`  | git would not stage a path: it is ignored, or it does not exist.  |

## Granting this to an agent

`permissions.filesystem` is `write`, so an agent spec granting it must also
declare `unattended: true`. Nothing approves a call an agent makes for itself,
and that flag is where saying so belongs.
