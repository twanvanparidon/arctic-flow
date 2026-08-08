# __NAME__

Returns the text it was given.

This file is what a model is handed when the tool is offered to a turn, appended
to `description` from `spec.json`. Write it for that reader: it decides whether
to call the tool from what is here.

## Purpose

One paragraph on what the tool is for, in terms of the job rather than the
implementation. What question does calling it answer?

## When to use it

- The case this tool exists for.
- Another one, if there is a second.

## When not to use it

- The case that looks like this tool's job and is not. This section is the one
  that saves a wasted call, so it is worth more than the one above it.
- Something a cheaper tool already answers.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter | Type   | Required | Default | Notes                    |
| --------- | ------ | -------- | ------- | ------------------------ |
| `text`    | string | yes      | none    | The text to return.      |

## Example

Input (one JSON object on stdin):

```json
{ "text": "hello" }
```

Run it directly, the same way the engine does:

```sh
echo '{"text":"hello"}' | ./tools/__NAME__/run.sh
```

Output on stdout:

```
hello
```

## Errors

A failure writes one line to stderr and exits with a code from `spec.json`'s
`exit_codes`. The engine turns the code back into the sentence written there, so
add a code rather than inventing an error shape.

```
$ echo '{}' | ./tools/__NAME__/run.sh
__NAME__: parameter 'text' must be a non-empty string
$ echo $?
2
```
