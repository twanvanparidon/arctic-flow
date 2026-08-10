# bitbucket/pr/open

Open a Bitbucket Cloud pull request.

## Purpose

Turn work a flow just finished into something a person can review, without a step in
between to work out which branch and which repository.

## When to use it

- At the end of a flow that committed to a branch and pushed it.

## When not to use it

- The branch is not pushed. Nothing in these packs pushes, and Bitbucket will refuse
  with exit `6`.
- A pull request is already open for the branch.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type   | Required | Default            | Notes                                |
| --------- | ------ | -------- | ------------------ | ------------------------------------ |
| `title`   | string | yes      | none               |                                      |
| `body`    | string | no       | none               | Sent as Bitbucket's `description`.   |
| `source`  | string | no       | current branch     | The branch to merge from.            |
| `target`  | string | no       | the repo's `mainbranch` | Read from the API, not assumed. |
| `repo`    | string | no       | origin remote      | `workspace/repository`.              |

`body`, not `description`, so a flow can swap this tool for the github one
unchanged. The mapping happens inside the tool.

## The one place the two packs differ

The github pack takes `draft`. This one does not, because opening a Bitbucket pull
request as a draft is not something this tool can do reliably through the create
endpoint. `draft` is still **reported** on the way out, so a flow reading the result
sees the same fields either way. A parameter that was accepted and silently ignored
would be worse than one that is absent.

## Example

```yaml
- id: propose
  tool: arctic/bitbucket/pr/open
  secrets: [BITBUCKET_TOKEN]
  input:
    title: "{{ steps.summarise.text }}"
    body: "Opened by the `nightly-tidy` flow."
  push: [announce]
```

```json
{
  "repo": "acme/widget",
  "number": 42,
  "state": "open",
  "title": "Tidy the imports",
  "source": "tidy/imports",
  "target": "main",
  "author": "atf-bot",
  "url": "https://bitbucket.org/acme/widget/pull-requests/42",
  "draft": false
}
```

## The target is asked for, not assumed

A repository whose main branch is `master` or `develop` is not unusual, and a pull
request opened against a branch nobody reviews is a quiet failure. Omitting `target`
costs one extra request and gets the right answer.

## Errors

| Exit | Means                                                        |
| ---- | ------------------------------------------------------------ |
| `2`  | no title, a bad parameter, or source and target are the same. |
| `3`  | no such repository.                                           |
| `4`  | the git repository is above the workspace root.               |
| `5`  | unreachable: DNS, connection, TLS, or a timeout.              |
| `6`  | Bitbucket refused: branch not pushed, PR already open, or the token was refused. |
| `7`  | `BITBUCKET_TOKEN` is not in this step's environment.          |

## The token, and why no agent can be granted this

`secrets: [BITBUCKET_TOKEN]` on the step. Because the spec declares `secrets`, the
engine **refuses to grant this tool to an agent**: nothing scopes a credential to a
single in-turn call. Opening a pull request is therefore always a step in the flow.
