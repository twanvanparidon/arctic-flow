# The git pack

Eight tools for reading and changing the git repository a flow is running in.

Ships with the engine and is **off** until `~/.arctic/config.yaml` says otherwise:

```yaml
packs:
  - git
```

`atf list` shows every pack and whether it is on. A flow naming a tool from a pack that is
off fails with the line to add, at `lint` time as well as at `run` time.

## What is in it

| Tool | Permission | Does |
| --- | --- | --- |
| `arctic/git/status` | read | branch, staged, unstaged, untracked |
| `arctic/git/log` | read | recent commits, by ref or path |
| `arctic/git/diff` | read | worktree, index, or against a ref |
| `arctic/git/show` | read | one commit, its message and its diff |
| `arctic/git/branch` | read | which branches exist, newest first |
| `arctic/git/add` | write | stage named paths |
| `arctic/git/commit` | write | record what is staged |
| `arctic/git/checkout` | write | switch branches, or create one |

Each has a `tool.md` beside its `spec.json`, which is what a model reads and where the
edge cases are written down.

## What is deliberately not in it

**Nothing that leaves the machine.** No `push`, `pull`, `fetch`, `clone` or `remote`. So
`permissions.network` is `false` for every tool in the pack, which is a fact something can
check rather than a promise in a paragraph. A flow that needs to publish should run its
own tool, where the credentials and the decision are visible.

**Nothing that destroys history or uncommitted work.** No `reset`, `rebase`, `clean`,
`stash drop`, no `checkout -- <path>`, and no `--force` anywhere. These are the commands
with no way back, and a model calling one has not read the working tree the way a person
about to type it has.

**No `git add -A`.** Paths are named. That is the whole of how an unrelated file stays out
of a commit nobody reviewed.

**No `--no-verify`.** A repository's hooks are the checks its owner decided a commit must
pass. A tool that skipped them would let a flow write commits a person could not.

## Two rules the whole pack shares

**The repository is the workspace.** Every tool checks that `git rev-parse --show-toplevel`
**is** the workspace root, and refuses with exit `4` when the repository is above it.
Without that, a flow run in `myrepo/subproject` would log, diff and commit the whole of
`myrepo`, which is not what the workspace says the flow is about. To work on the outer
repository, point the engine at it: `atf --workspace myrepo run …`.

**Exit codes mean one thing across the pack.** `0` ok, `2` invalid input, `3` not found,
`4` not permitted, `5` no repository, `6` git refused, `7` no identity to commit under. A
tool's `spec.json` lists only the ones it can produce.

## Granting one to an agent

The three write tools declare `permissions.filesystem: write`, so an agent spec granting
one must also declare `unattended: true`. That is the engine's gate and it is not
specific to this pack: nothing approves a call an agent makes for itself.

Before granting `commit`, consider a gate instead. Letting the agent write the message and
letting a tool step do the commit keeps the decision in the flow, where it can be read.

## Changing one

`lib/git.sh` is shared by all eight and holds the containment check, the input helpers and
the git wrapper. It is the one file in this pack outside a tool directory, because eight
copies of a security check is eight places to fix it and seven to forget.

It sits outside `tools/` on purpose: the resolver walks that directory looking for
`spec.json`, so anything in there that is not a tool would read as an empty namespace.

Two traps it documents, both of which cost a bug already:

- `try_git`'s result must be **assigned**, never passed as an argument. `x=$(try_git …)`
  propagates a failure; `f "$(try_git …)"` runs it in a subshell whose exit reaches
  nothing, so the script carries on with an empty string and exit `0`.
- The sourcing line carries `# shellcheck source-path=SCRIPTDIR`, and the gate runs
  `shellcheck -x`. Without both, the pre-push gate fails on SC1091.
