# The github pack

Three tools for the pull requests of a GitHub repository. Also GitHub Enterprise: set
`$GITHUB_API_URL` to `https://<host>/api/v3`.

Ships with the engine and is **off** until `~/.arctic/config.yaml` says otherwise:

```yaml
packs:
  - github
```

## What is in it

| Tool | Does |
| --- | --- |
| `arctic/github/pr/open` | Opens a pull request from the current branch |
| `arctic/github/pr/status` | State, branches, review decisions and CI checks, as JSON |
| `arctic/github/pr/comment` | Leaves a comment on the conversation |

## The token

Every tool declares `secrets: ["GITHUB_TOKEN"]`, so a step that runs one declares it too:

```yaml
- id: report
  tool: arctic/github/pr/comment
  secrets: [GITHUB_TOKEN]
  input:
    body: "{{ steps.review.text }}"
```

```sh
atf vault set GITHUB_TOKEN
```

**No agent can be granted these.** The engine refuses to grant a tool that declares
`secrets`, because nothing scopes a credential to a single in-turn call, so a granted tool
would inherit the whole environment. That is not a limitation these tools work around: a
pull request being opened or commented on is a decision a flow should hold, not one a
model makes mid-turn. `unattended: true` does not change it, and neither does anything
else short of per-call secret scoping, which CLAUDE.md names as the outstanding follow-up.

The token never reaches `argv`. `curl` reads the `Authorization` header from a config file
written with mode 600 and removed afterwards, because `-H` would put the credential where
`ps` shows it to every user on the machine.

## What is deliberately not in it

**No approving or requesting changes.** Those are verdicts that count in a branch
protection rule, and a flow that could cast one could approve its own work. Commenting
says the same thing and signs nothing.

**No merging, closing or force-anything.** Merging is the one action a flow cannot take
back, and it is the one that most deserves a person in front of it.

**No pushing.** That belongs to git and the git pack refuses it too, so opening a pull
request for a branch the remote has never seen fails with GitHub's own message rather than
with a tool having pushed on your behalf.

**No `gh`.** A REST endpoint is a far more stable contract than a CLI's flags, and the
`claude_code` adapter's `VERIFIED_CLI_VERSION` is what depending on the other kind costs.

## The two rules it shares with the other packs

**The repository is the workspace.** When `repo` is not given it comes from the origin
remote, and reading that remote requires the git repository's root to *be* the workspace.
Otherwise a flow run in `myrepo/subproject` would open a pull request in `myrepo`. The
remote's host is checked too, so this pack will not derive a repository from a bitbucket
remote and build a plausible URL for the wrong service.

**Exit codes mean one thing across the pack.** `0` ok, `2` invalid input, `3` not found,
`4` not permitted, `5` unreachable, `6` the API refused, `7` no credential.

## Its answers are the bitbucket pack's answers

`state`, `source`, `target`, `number`, `reviews`, `checks`: same names, same meanings, so
a flow that moves between forges changes the tool name and nothing else. Where a forge
genuinely cannot answer, the field is `null` rather than invented. Each tool's `tool.md`
has the table of what differs.

## Changing one

`lib/api.sh` is shared by all three: the input helpers, the curl call, the error mapping
and the repository lookup. It sits outside `tools/` because the resolver walks that
directory for `spec.json` and anything else in there reads as an empty namespace.

It is **not** shared with the bitbucket pack, which carries its own copy of the same
shape. A pack is a unit you can read top to bottom, copy or delete, and roughly seventy
duplicated lines is what that costs. The git pack's `lib/git.sh` is shared for a different
reason: it holds a security check, where a second copy is a second thing to forget.
