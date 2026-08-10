# The bitbucket pack

Three tools for the pull requests of a Bitbucket **Cloud** repository.

Ships with the engine and is **off** until `~/.arctic/config.yaml` says otherwise:

```yaml
packs:
  - bitbucket
```

## Cloud only

Server and Data Center speak a different API entirely, under `/rest/api/1.0/`, with
different paths, different response shapes and a different auth scheme. Supporting both
would be two packs wearing one name. `$BITBUCKET_API_URL` exists for a proxy and for the
test suite's double, not as a way to point these at Server: the paths would not match.

## What is in it

| Tool | Does |
| --- | --- |
| `arctic/bitbucket/pr/open` | Opens a pull request from the current branch |
| `arctic/bitbucket/pr/status` | State, branches, approvals and build statuses, as JSON |
| `arctic/bitbucket/pr/comment` | Leaves a comment on the conversation |

## Two words that collide

**`workspace`.** Bitbucket's workspace is the account a repository belongs to, and is the
first segment of `repo: "workspace/repository"`. The engine's workspace is the project
root a flow runs in, which is what `--workspace` sets. They are unrelated. Everything in
these tools means the engine's workspace when it says workspace, except inside `repo`.

**`number`.** Bitbucket's API calls it an `id`. These tools call it `number`, because the
github pack does, and one vocabulary across both packs is worth more than matching each
vendor's word.

## The token

Every tool declares `secrets: ["BITBUCKET_TOKEN"]`, so a step that runs one declares it
too. A workspace, project or repository access token: this sends `Authorization: Bearer`,
not an app password over Basic, which needs a username to go with it and is a credential
Atlassian is moving away from.

```sh
atf vault set BITBUCKET_TOKEN
```

**No agent can be granted these**, for the reason set out in the github pack's README: the
engine refuses to grant a tool that declares `secrets`. The token never reaches `argv`
either.

## What is deliberately not in it

**No approving.** It is a verdict that counts in a merge check, and a flow that could cast
one could approve its own work.

**No merging, declining or force-anything.** **No pushing**, which belongs to git.

## Its answers are the github pack's answers

`state`, `source`, `target`, `number`, `reviews`, `checks`: same names, same meanings.
Where this forge genuinely cannot answer, the field is `null` rather than invented, and
there are two such places:

- **`mergeable` is always `null`.** Bitbucket does not report one without a dry-run merge,
  which is a third request and a write-shaped call from a tool that reads. Gate on
  `checks` and `reviews`, which are the real question.
- **`pr/open` takes no `draft`.** The github pack does. `draft` is still reported on the
  way out, so a flow reading either result sees the same fields. A parameter accepted and
  silently ignored would be worse than one that is absent.

`DECLINED` and `SUPERSEDED` both map to `closed`, since both mean closed without merging.

## Changing one

`lib/api.sh` is shared by all three and deliberately not with the github pack. See that
pack's README for why, and for the two rules (containment, exit codes) both share.
