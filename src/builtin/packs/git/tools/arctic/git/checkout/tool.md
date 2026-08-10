# git/checkout

Switch to a branch, or create one and switch to it.

## Purpose

Put the working tree on the branch a flow is meant to be working on, so what it
writes lands where it belongs.

## When to use it

- At the start of a flow that will commit, to get off the default branch.
- To move to a branch another step named.

## When not to use it

- You want to know what branches exist. That is `git/branch`.
- You want to throw away uncommitted changes. This will not do that, and neither
  will anything else in this pack.

## Branches only

`git checkout` in a shell does two unrelated jobs: it switches branches, and it
restores files. The second throws away uncommitted work with no way back, so it
is not here and it is not coming. A tool an agent can call has no business being
the one command in git that silently destroys work.

Which is also why there is no `force`. git refuses to switch when the working
tree has changes the switch would overwrite, and that refusal is the only thing
between a flow and somebody's uncommitted work. It arrives as exit `6` carrying
git's own reason, so the flow can commit and try again.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter     | Type    | Required | Default | Notes                                     |
| ------------- | ------- | -------- | ------- | ----------------------------------------- |
| `branch`      | string  | yes      | none    | To switch to, or to create.               |
| `create`      | boolean | no       | `false` | Make it rather than expect it.            |
| `start_point` | string  | no       | `HEAD`  | Where a created branch starts. Needs `create`. |

## Example

```sh
echo '{"branch":"feature/45-tool-packs","create":true}' \
  | src/builtin/packs/git/tools/arctic/git/checkout/run.sh
```

```
on feature/45-tool-packs, was main
```

Branching from somewhere other than here:

```json
{ "branch": "hotfix/1", "create": true, "start_point": "v0.2.0" }
```

## Already there is a success

Switching to the branch that is already checked out returns `already on <name>`
and exits `0`, so a flow that ensures it is on a branch does not have to check
first.

`create: true` on a name that exists is **not** a success. It means the name was
not the one you thought, which is worth stopping for.

## Errors

| Exit | Means                                                            |
| ---- | ---------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, `branch` is missing, or `start_point` came without `create`. |
| `4`  | the repository is above the workspace root.                       |
| `5`  | the workspace is not a git repository, or `git` is not installed. |
| `6`  | no such branch, the name is taken, or the tree has changes the switch would overwrite. |

## Granting this to an agent

`permissions.filesystem` is `write`, so an agent spec granting it must also
declare `unattended: true`. Moving the working tree under a flow that is midway
through writing files is worth thinking about before you allow it.
