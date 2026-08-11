# word_limit

Answer whether a piece of text is inside a word budget.

## Purpose

Hold writing to a length a prompt asked for. "At most 60 words" is a request; this
is the answer to whether it happened.

## When to use it

- As the step a flow switches on, so an over-long answer goes back to the writer
  with the count instead of reaching the next step.
- Before publishing text into somewhere with a hard limit.

## When not to use it

- The limit is characters, lines, or tokens. This counts whitespace-separated
  words and nothing else.
- The text is fine at any length. A check that cannot reject costs a subprocess
  per pass and buys nothing.

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
echo '{"text":"Two words.","max_words":60}' | examples/checked-summary/tools/word_limit/run.sh
```

Output on stdout:

```json
{"verdict":"approved","words":2,"limit":60,"over":0,"reason":null}
```

A rejection is an answer, not a failure, so it comes out the same way and exits 0:

```sh
$ echo '{"text":"one two three","max_words":2}' | examples/checked-summary/tools/word_limit/run.sh
{"verdict":"rejected","words":3,"limit":2,"over":1,"reason":"3 words, 1 over the limit of 2. Cut it down."}
$ echo $?
0
```

That is what makes this switchable. The flow reads `{{ this.json.verdict }}` to pick
a branch and `{{ steps.check.json.reason }}` to tell the writer what to fix.

## Errors

An input this tool cannot read writes one line to stderr and exits `2`, the code in
`spec.json`'s `exit_codes`. That is a broken step rather than a verdict: there is no
answer to give.

```
$ echo '{"text":"one two three"}' | examples/checked-summary/tools/word_limit/run.sh
word_limit: parameter 'max_words' must be an integer >= 1
$ echo $?
2
```
