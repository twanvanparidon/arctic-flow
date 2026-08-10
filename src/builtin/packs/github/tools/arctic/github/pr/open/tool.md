# github/pr/open

Open a pull request.

## Purpose

Turn work a flow just finished into something a person can review, without a step in
between to work out which branch and which repository.

## When to use it

- At the end of a flow that committed to a branch and pushed it.
- To open a draft, when a flow produced the change and nobody has read it yet.

## When not to use it

- The branch is not pushed. Nothing in these packs pushes, and GitHub will refuse
  with exit `6`. Push it in a tool step of your own first.
- A pull request is already open for the branch. That is also exit `6`, and GitHub's
  message names the one that exists.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type    | Required | Default        | Notes                              |
| --------- | ------- | -------- | -------------- | ---------------------------------- |
| `title`   | string  | yes      | none           |                                    |
| `body`    | string  | no       | none           | Markdown. Omitted, not sent empty. |
| `source`  | string  | no       | current branch | The branch to merge from.          |
| `target`  | string  | no       | the repo's default | Read from the API, not assumed. |
| `draft`   | boolean | no       | `false`        |                                    |
| `repo`    | string  | no       | origin remote  | `owner/name`.                      |

## Example

```yaml
- id: propose
  tool: arctic/github/pr/open
  secrets: [GITHUB_TOKEN]
  input:
    title: "{{ steps.summarise.text }}"
    body: |
      Opened by the `nightly-tidy` flow.

      {{ steps.summarise.text }}
    draft: true
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
  "url": "https://github.com/acme/widget/pull/42",
  "draft": true
}
```

`number` and `url` are what the next step wants: `{{ steps.propose.json.url }}`.

## The target is asked for, not assumed

A repository whose default branch is `master`, `trunk` or `develop` is not unusual,
and a pull request opened against a branch nobody reviews is a quiet failure. So
omitting `target` costs one extra request and gets the right answer.

## What comes back is what was recorded

Not what was asked for. GitHub can normalise a title or refuse a draft on a
repository that does not allow them, and the answer says what actually happened.

## Errors

| Exit | Means                                                        |
| ---- | ------------------------------------------------------------ |
| `2`  | no title, a bad parameter, or source and target are the same. |
| `3`  | no such repository.                                           |
| `4`  | the git repository is above the workspace root.               |
| `5`  | unreachable: DNS, connection, TLS, or a timeout.              |
| `6`  | GitHub refused: branch not pushed, PR already open, or the token was refused. |
| `7`  | `GITHUB_TOKEN` is not in this step's environment.             |

## The token, and why no agent can be granted this

`secrets: [GITHUB_TOKEN]` on the step. Because the spec declares `secrets`, the
engine **refuses to grant this tool to an agent**: nothing scopes a credential to a
single in-turn call, so a granted tool would inherit the whole environment. Opening
a pull request is therefore always a step in the flow, which is where a decision
that other people will see belongs.
