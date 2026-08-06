# word_limit

Check that a piece of text is inside a word budget.

## Purpose

Hold writing to a length a prompt asked for. "At most 60 words" is a request; this
is the check, and it either passes or it does not.

## When to use it

- As a step's `gate`, so an over-long answer comes back with the count instead of
  reaching the next step.
- Before publishing text into somewhere with a hard limit.

## When not to use it

- The limit is characters, lines, or tokens. This counts whitespace-separated
  words and nothing else.
- The text is fine at any length. A gate that cannot fail costs a subprocess per
  attempt and buys nothing.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter   | Type    | Required | Notes                                                       |
| ----------- | ------- | -------- | ----------------------------------------------------------- |
| `text`      | string  | yes      | Measured verbatim, with no trimming or normalising.         |
| `max_words` | integer | yes      | Write it in the flow. A rendered template is a string.      |

## Example

Input (one JSON object on stdin):

```json
{ "text": "Two words.", "max_words": 60 }
```

Run it directly, the same way the engine does:

```sh
echo '{"text":"Two words.","max_words":60}' | examples/gated-summary/tools/word_limit/run.sh
```

Output on stdout:

```
2 words, inside the limit of 60
```

## Errors

Failures write one line to stderr and set an exit code from `spec.json`'s
`exit_codes`: `1` over the limit, `2` invalid input.

```
$ echo '{"text":"one two three","max_words":2}' | examples/gated-summary/tools/word_limit/run.sh
word_limit: 3 words, 1 over the limit of 2. Cut it down.
$ echo $?
1
```

Exit `1` is what makes this a gate. The engine does not treat it as a broken run:
it hands that line back to the step as `{{ gate.text }}` and lets it try again.
