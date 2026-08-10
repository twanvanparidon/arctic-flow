# git/branch

Which branches exist, and which one is checked out.

## Purpose

Find out what work is in flight, and whether a name is taken before trying to
create it.

## When to use it

- Before `git/checkout` with `create: true`, to avoid a name that exists.
- To confirm which branch a flow is on before it changes anything.
- To report what a repository is working on.

## When not to use it

- You want to switch or create one. That is `git/checkout`.
- You only need the current branch. `git/status` says it on its first line and
  tells you the state of the tree in the same call.

## This only lists

Creating and switching live in `git/checkout`, and the split is deliberate. A
spec declares `permissions.filesystem` once, so a tool that both listed and
switched could only ever be granted as one that **writes**. Keeping them apart is
what lets an agent be allowed to see the branches without being allowed to move
the working tree.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter      | Type    | Required | Default | Notes                              |
| -------------- | ------- | -------- | ------- | ---------------------------------- |
| `remote`       | boolean | no       | `false` | Include `origin/...` branches.     |
| `max_branches` | integer | no       | `100`   | Sorted newest first, so the cap drops the stalest. |

## Example

```sh
echo '{}' | src/builtin/packs/git/tools/arctic/git/branch/run.sh
```

```
* main                           2026-08-09  Merge pull request #49
  feature/45-tool-packs          2026-08-08  feat(packs): add the git pack
  feature/40-marketplace         2026-07-30  docs: describe the plugin
```

The marker is a column rather than a prefix on the name, so the branch name is
the second field of a plain split.

Sorted by last commit and not alphabetically: a repository with sixty branches is
asked this about the handful that are alive.

## Errors

| Exit | Means                                                            |
| ---- | ---------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, or a parameter is the wrong type.    |
| `4`  | the repository is above the workspace root.                       |
| `5`  | the workspace is not a git repository, or `git` is not installed. |

A repository with no commits has no branches yet. That is exit `0` and a
`[git/branch] no branches yet` notice.
