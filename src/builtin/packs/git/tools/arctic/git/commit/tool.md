# git/commit

Record what is staged.

## Purpose

Write a commit whose contents were chosen on purpose and whose message says why.

## When to use it

- After `git/add`, once `git/diff staged=true` shows what you meant to record.
- At the end of a flow that produced a change worth keeping.

## When not to use it

- Nothing is staged yet. Stage first; this refuses an empty index rather than
  recording an empty commit.
- You have not looked at what is staged. Do that first: this is the step that
  cannot be taken back without rewriting history.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter   | Type    | Required | Default | Notes                                       |
| ----------- | ------- | -------- | ------- | ------------------------------------------- |
| `message`   | string  | yes      | none    | Subject, blank line, body.                  |
| `author`    | string  | no       | none    | `Name <email>`. Authorship only.            |
| `max_files` | integer | no       | `100`   | How many committed paths to list back.      |

## Example

```sh
printf '%s' '{"message":"fix(engine): stop a join stalling\n\n- a join now runs once every inbound edge is delivered or skipped"}' \
  | src/builtin/packs/git/tools/arctic/git/commit/run.sh
```

```
a1b2c3d fix(engine): stop a join stalling

src/engine/executor.py
```

The sha is first, on its own line, because that is what a later step templates.

## Nothing is staged for you

There is no `all` parameter. What goes into a commit is a decision made by a
`git/add` call that can be read back in the flow, rather than whatever happened
to be in the working tree when this ran. An empty index is exit `6`, not a silent
no-op.

## The identity is never invented

A commit carries an author, and that name goes into history. Guessing one
attributes work to somebody who did not do it.

So this uses git's configured identity, or the one `author` names, and exits `7`
when there is neither, saying what to configure. It will not make one up. On a
build machine that means setting it in the repository or in the environment:

```sh
git config user.name "CI"
git config user.email "ci@example.com"
# or
export GIT_COMMITTER_NAME=CI GIT_COMMITTER_EMAIL=ci@example.com
```

`author` sets who *wrote* the change and never who *recorded* it, so a committer
identity is needed either way.

## The message is written verbatim

git's default cleanup deletes every line starting with `#`, which exists for a
message typed into an editor over a commented template. There is no editor here,
so `--cleanup=whitespace` is used instead and a `#45` written against an issue
survives.

Hooks are **not** skipped. A repository's hooks are the checks its owner decided
a commit must pass, and a tool that passed `--no-verify` would let a flow write
commits a person could not. A hook that rejects the commit is exit `6` carrying
what the hook said.

## Errors

| Exit | Means                                                            |
| ---- | ---------------------------------------------------------------- |
| `2`  | stdin was not a JSON object, `message` is missing, or `author` is malformed. |
| `4`  | the repository is above the workspace root.                       |
| `5`  | the workspace is not a git repository, or `git` is not installed. |
| `6`  | nothing is staged, or a commit hook rejected it.                  |
| `7`  | git has no `user.name` and `user.email` to record the commit under. |

## Granting this to an agent

`permissions.filesystem` is `write`, so an agent spec granting it must also
declare `unattended: true`.

Think about it before you do. A gate is usually the better shape: let the agent
produce the message, and let a tool step do the commit, so the flow decides
whether it happens rather than the model deciding mid-turn.
