# github/pr/status

One pull request: its state, its branches, who reviewed it and whether the checks
pass. Returned as JSON.

## Purpose

Answer "is this ready" from the record rather than from a guess, and answer it in a
shape a flow can branch on.

## When to use it

- Before commenting, to know what to comment about.
- To gate a flow on checks passing or a review being in.
- After opening a pull request, to watch it.

## When not to use it

- You want the diff. Read that from the repository with the git pack, which costs
  no API call.
- You already called this and nothing has happened since. Checks take minutes;
  polling this in a tight loop is a way to be rate limited.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type    | Required | Default | Notes                                    |
| --------- | ------- | -------- | ------- | ---------------------------------------- |
| `number`  | integer | no       | none    | Omit to use the current branch's open PR. |
| `repo`    | string  | no       | origin  | `owner/name`.                            |

## Example

```sh
echo '{"number":42}' | .../pr/status/run.sh
```

```json
{
  "repo": "acme/widget",
  "number": 42,
  "state": "open",
  "title": "Add the git pack",
  "source": "feature/45-tool-packs",
  "target": "main",
  "author": "twanvanparidon",
  "url": "https://github.com/acme/widget/pull/42",
  "draft": false,
  "mergeable": true,
  "reviews": { "approved": 2, "changes_requested": 0 },
  "checks": { "success": 3, "failure": 1, "pending": 0, "failing": ["test"] }
}
```

JSON because the engine parses a tool's stdout, so a flow gets both:

```yaml
- id: pr
  tool: arctic/github/pr/status
  secrets: [GITHUB_TOKEN]
  switch: "{{ this.json.checks.failure }}"
  cases:
    "0": [comment_ready]
  default: [comment_failing]

- id: comment_failing
  tool: arctic/github/pr/comment
  secrets: [GITHUB_TOKEN]
  input:
    body: "These are red: {{ steps.pr.json.checks.failing }}"
```

## The field names are the bitbucket pack's

`state`, `source`, `target`, `number`, `reviews`, `checks` mean the same thing in
both packs, so a flow that moves between forges changes the tool name and nothing
else. Two fields cannot be the same:

| Field       | Here                                   | bitbucket                  |
| ----------- | -------------------------------------- | -------------------------- |
| `mergeable` | `true`, `false`, or `null` while GitHub computes it | always `null` |
| `state`     | `open`, `merged`, `closed`             | the same three; `DECLINED` and `SUPERSEDED` both map to `closed` |

## What the counts mean

**Reviews are the latest per reviewer.** One person who requested a change and
then approved counts once, as an approval. A plain comment is not a verdict and is
not counted.

**Checks are three answers out of GitHub's six.** Anything unfinished is
`pending`. Anything finished that did not object, including `neutral` and
`skipped`, is `success`. The rest is `failure`. `failing` names them, so a flow can
say which without a second call.

## Finding the pull request

With no `number` this looks up the open pull request whose source branch is the one
the workspace is on. Exactly one match is required: several means the guess would be
a guess, and none is exit `3`.

## Errors

| Exit | Means                                                       |
| ---- | ----------------------------------------------------------- |
| `2`  | a parameter is wrong, or the branch matched several PRs.     |
| `3`  | no such pull request, or none open for the branch.           |
| `4`  | the git repository is above the workspace root.              |
| `5`  | unreachable: DNS, connection, TLS, or a timeout.             |
| `6`  | the API answered with a failure, including a refused token.  |
| `7`  | `GITHUB_TOKEN` is not in this step's environment.            |

## The token

`secrets: [GITHUB_TOKEN]` on the step, and the token in the vault:

```sh
atf vault set GITHUB_TOKEN
```

Declaring `secrets` in this tool's spec has a second effect worth knowing: **an
agent cannot be granted it.** The engine refuses to grant any tool that expects a
credential, because an agent's tools are called without a step declaring anything.
So reading a pull request is always a step the flow decided on.
