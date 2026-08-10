# github/pr/comment

Leave a comment on a pull request's conversation.

## Purpose

Put what a flow found where the people reviewing it will see it: a summary, a risk
list, a failing check explained.

## When to use it

- After `pr/status`, to report what it found.
- After an agent step reviewed a diff, to post the review as a remark.

## When not to use it

- You mean to approve or request changes. This deliberately cannot: see below.
- You have nothing to say. A whitespace-only body is refused rather than posted.
- You want to comment on a specific line. That is a review comment, with a path and
  a position, and is not this tool. Quote the line in prose instead.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type    | Required | Default | Notes                                     |
| --------- | ------- | -------- | ------- | ----------------------------------------- |
| `body`    | string  | yes      | none    | Markdown.                                 |
| `number`  | integer | no       | none    | Omit to use the current branch's open PR. |
| `repo`    | string  | no       | origin  | `owner/name`.                             |

## Example

```yaml
- id: report
  tool: arctic/github/pr/comment
  secrets: [GITHUB_TOKEN]
  input:
    body: |
      **Automated review**

      {{ steps.review.text }}
```

```json
{
  "repo": "acme/widget",
  "number": 42,
  "id": 2051,
  "url": "https://github.com/acme/widget/pull/42#issuecomment-2051",
  "author": "atf-bot"
}
```

`number` is the pull request and `id` is the comment. Two different numbers, named
the way the rest of the pack names them.

## It comments; it does not approve

Approving or requesting changes is a verdict that counts in a branch protection
rule. A flow that could cast one could approve its own work, which is the review
gone rather than the review automated. A comment says the same thing and signs
nothing.

## Errors

| Exit | Means                                                       |
| ---- | ----------------------------------------------------------- |
| `2`  | no body, a whitespace-only body, or the branch matched several PRs. |
| `3`  | no such pull request, or none open for the branch.           |
| `4`  | the git repository is above the workspace root.              |
| `5`  | unreachable: DNS, connection, TLS, or a timeout.             |
| `6`  | the API answered with a failure, including a refused token.  |
| `7`  | `GITHUB_TOKEN` is not in this step's environment.            |

## The token, and why no agent can be granted this

`secrets: [GITHUB_TOKEN]` on the step. Because the spec declares `secrets`, the
engine **refuses to grant this tool to an agent**. So a model cannot decide
mid-turn to post something under your name; a step in the flow decides, and the
body it posts is a template you can read.
