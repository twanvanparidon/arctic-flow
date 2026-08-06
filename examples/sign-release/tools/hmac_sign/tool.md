# hmac_sign

HMAC-SHA256 a payload, using the `signing_key` the engine granted to the step.

## Purpose

Prove that a file came from you and has not been altered since. The signature covers the
exact bytes given, so anyone holding the artifact and the key can recompute it and compare.

## The key is not an input

`signing_key` arrives as an environment variable, because the step declared it:

```yaml
- id: sign
  tool: hmac_sign
  secrets: [signing_key]
  input:
    payload: "{{ steps.read_payload.text }}"
```

Passing it as an input instead would be a mistake worth being explicit about: inputs are
templated into the flow, and from there into rendered prompts, traces and diagrams. A
credential belongs in the environment, where it reaches the process and nothing else.

Missing grant exits **5**, separately from a bad payload (**2**), so a flow that forgot
`secrets:` says so instead of failing as if the input were wrong.

## Parameters

| Parameter | Type   | Required | Notes                                     |
| --------- | ------ | -------- | ----------------------------------------- |
| `payload` | string | yes      | Signed verbatim: no trimming, no newline handling |

## Example

```sh
signing_key=hunter2 echo '{"payload":"hello"}' | tools/hmac_sign/run.sh
# 3b2a...  (lowercase hex, HMAC-SHA256)
```

Equivalent to `openssl dgst -sha256 -hmac hunter2`, which is worth knowing because it is
how someone verifies your signature without this engine.

## Verbatim means verbatim

The payload is streamed from `jq -j` straight into `openssl`, never through a shell
variable. `$(...)` strips trailing newlines, so a body ending in one would be signed a
byte short and would not verify against the real file. That bug only shows up on files
that happen to end in a newline, which is most of them.
