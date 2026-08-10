# bitbucket/pr/status

One Bitbucket Cloud pull request: its state, its branches, who approved it and
whether the builds pass. Returned as JSON.

## Purpose

Answer "is this ready" from the record rather than from a guess, and answer it in a
shape a flow can branch on.

## When to use it

- Before commenting, to know what to comment about.
- To gate a flow on builds passing or an approval being in.
- After opening a pull request, to watch it.

## When not to use it

- You want the diff. Read that from the repository with the git pack, which costs
  no API call.
- You already called this and nothing has happened since.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type    | Required | Default | Notes                                     |
| --------- | ------- | -------- | ------- | ----------------------------------------- |
| `number`  | integer | no       | none    | Omit to use the current branch's open PR. |
| `repo`    | string  | no       | origin  | `workspace/repository`.                   |

**`number`, not `id`.** Bitbucket's own API calls it an id. The github pack calls it
a number, and one vocabulary across both packs is worth more than matching each
vendor's word.

**`workspace` in `repo` is Bitbucket's word, not the engine's.** The engine's
workspace is the project root a flow runs in. Bitbucket's is the account a
repository belongs to. They are unrelated and it is unfortunate.

## Example

```json
{
  "repo": "acme/widget",
  "number": 42,
  "state": "open",
  "title": "Add the git pack",
  "source": "feature/45-tool-packs",
  "target": "main",
  "author": "twan",
  "url": "https://bitbucket.org/acme/widget/pull-requests/42",
  "draft": false,
  "mergeable": null,
  "reviews": { "approved": 1, "changes_requested": 1 },
  "checks": { "success": 1, "failure": 2, "pending": 1, "failing": ["test", "e2e"] }
}
```

```yaml
- id: pr
  tool: arctic/bitbucket/pr/status
  secrets: [BITBUCKET_TOKEN]
  switch: "{{ this.json.checks.failure }}"
  cases:
    "0": [ready]
  default: [report_failing]
```

## The field names are the github pack's

Swapping `arctic/github/pr/status` for this one changes the tool name and nothing
else. Two fields cannot be identical:

| Field       | Here                                       | github                              |
| ----------- | ------------------------------------------ | ----------------------------------- |
| `mergeable` | always `null`                              | `true`, `false`, or `null`          |
| `state`     | `open`, `merged`, `closed`                 | the same three                      |

**`mergeable` is always null**, and that is deliberate rather than missing.
Bitbucket does not report one without a dry-run merge, which is a third request and
a write-shaped call from a tool that reads. So this says "not known" instead of
guessing. Gate on `checks` and `reviews`, which are the real question anyway.

`DECLINED` and `SUPERSEDED` both map to `closed`, because both mean "closed without
merging", which is what GitHub calls closed. Collapsing them is what lets one flow
read either forge.

## What the counts mean

**Approvals come off the participants**, of which Bitbucket keeps one per person, so
there is no superseded review to discard the way there is on GitHub.
`changes_requested` is the separate `state` field, not the absence of an approval.

**Builds are three answers out of four.** `SUCCESSFUL` is success, `INPROGRESS` is
pending, and `FAILED` and `STOPPED` are both failure. A cancelled build is not one
still running, and calling it pending would make a flow wait for something that
already stopped.

## Errors

| Exit | Means                                                       |
| ---- | ----------------------------------------------------------- |
| `2`  | a parameter is wrong, or the branch matched several PRs.     |
| `3`  | no such pull request, or none open for the branch.           |
| `4`  | the git repository is above the workspace root.              |
| `5`  | unreachable: DNS, connection, TLS, or a timeout.             |
| `6`  | the API answered with a failure, including a refused token.  |
| `7`  | `BITBUCKET_TOKEN` is not in this step's environment.         |

## The token

`secrets: [BITBUCKET_TOKEN]` on the step, and the token in the vault. A workspace,
project or repository access token: this sends `Authorization: Bearer`, not an app
password over Basic.

Because the spec declares `secrets`, the engine **refuses to grant this tool to an
agent**. Reading a pull request is always a step the flow decided on.
