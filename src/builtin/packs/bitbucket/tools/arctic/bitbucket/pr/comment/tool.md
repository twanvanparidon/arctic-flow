# bitbucket/pr/comment

Leave a comment on a Bitbucket Cloud pull request's conversation.

## Purpose

Put what a flow found where the people reviewing it will see it: a summary, a risk
list, a failing build explained.

## When to use it

- After `pr/status`, to report what it found.
- After an agent step reviewed a diff, to post the review as a remark.

## When not to use it

- You mean to approve. This deliberately cannot: see below.
- You have nothing to say. A whitespace-only body is refused rather than posted.
- You want to comment on a specific line. That is an inline comment, which needs a
  path and a line number, and is not this tool. Quote the line in prose instead.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type    | Required | Default | Notes                                     |
| --------- | ------- | -------- | ------- | ----------------------------------------- |
| `body`    | string  | yes      | none    | Markdown. Sent as `content.raw`.          |
| `number`  | integer | no       | none    | Omit to use the current branch's open PR. |
| `repo`    | string  | no       | origin  | `workspace/repository`.                   |

## Example

```yaml
- id: report
  tool: arctic/bitbucket/pr/comment
  secrets: [BITBUCKET_TOKEN]
  input:
    body: |
      **Automated review**

      {{ steps.review.text }}
```

```json
{
  "repo": "acme/widget",
  "number": 42,
  "id": 999,
  "url": "https://bitbucket.org/acme/widget/pull-requests/42#comment-999",
  "author": "atf-bot"
}
```

`number` is the pull request and `id` is the comment. Two different numbers.

## It comments; it does not approve

Approving is a verdict that counts in a merge check. A flow that could cast one
could approve its own work, which is the review gone rather than the review
automated. A comment says the same thing and signs nothing.

## Errors

| Exit | Means                                                       |
| ---- | ----------------------------------------------------------- |
| `2`  | no body, a whitespace-only body, or the branch matched several PRs. |
| `3`  | no such pull request, or none open for the branch.           |
| `4`  | the git repository is above the workspace root.              |
| `5`  | unreachable: DNS, connection, TLS, or a timeout.             |
| `6`  | the API answered with a failure, including a refused token.  |
| `7`  | `BITBUCKET_TOKEN` is not in this step's environment.         |

## The token, and why no agent can be granted this

`secrets: [BITBUCKET_TOKEN]` on the step. Because the spec declares `secrets`, the
engine **refuses to grant this tool to an agent**. So a model cannot decide mid-turn
to post something under your name; a step in the flow decides, and the body it posts
is a template you can read.
