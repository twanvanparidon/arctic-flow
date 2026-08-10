# git/status

What the working tree looks like right now: the branch, what is staged, what is
changed, and what is untracked.

## Purpose

Find out whether there is anything to commit, and what it would be, before doing
anything about it. This is the tool that turns "make a commit" into a decision
about specific paths rather than a guess.

## When to use it

- Before `git/add`, to learn what there is to stage.
- Before `git/commit`, to check the index holds what you meant.
- To branch a flow on whether the tree is clean.
- After a step that writes files, to confirm it wrote what it said.

## When not to use it

- You want the contents of the change. That is `git/diff`.
- You want the history. That is `git/log`.
- You already called this and nothing has run since.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter      | Type    | Required | Default | Notes                                          |
| -------------- | ------- | -------- | ------- | ---------------------------------------------- |
| `no_untracked` | boolean | no       | `false` | Leave untracked files out.                     |
| `max_files`    | integer | no       | `200`   | Across all three groups together.               |

## Example

```sh
echo '{}' | src/builtin/packs/git/tools/arctic/git/status/run.sh
```

```
branch main
upstream origin/main, ahead 2, behind 0

staged:
  added       src/new.py

unstaged:
  modified    README.md

untracked:
  notes.md
```

A clean tree is two lines and still exits 0:

```
branch main
clean
```

So a flow can switch on it:

```yaml
- id: check
  tool: arctic/git/status
  switch: "{{ this.text }}"
  cases:
    "*clean*": [nothing_to_do]
    "*": [write_commit]
```

## What the words mean

The two-letter porcelain code is translated, so nothing downstream has to know
what `MM` means. `modified`, `added`, `deleted`, `renamed`, `copied`,
`typechange` and `conflicted` are the whole vocabulary.

A path appears in both `staged` and `unstaged` when it has changes in each. That
is not a duplicate: committing then would record one and leave the other.

`conflicted` appears under `staged` on its own. A commit with one in the index is
what git refuses, and this is where a flow finds out before it tries.

## Errors

| Exit | Means                                                            |
| ---- | ---------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, or a parameter is the wrong type.    |
| `4`  | the repository is above the workspace root.                       |
| `5`  | the workspace is not a git repository, or `git` is not installed. |

Exit `4` is the containment rule the whole pack shares. Every tool acts on the
repository whose root **is** the workspace, never one above it, so a flow run in
`myrepo/subproject` will not quietly report on the whole of `myrepo`. To work on
the outer repository, point the engine at it:

```sh
atf --workspace myrepo run my_flow
```
