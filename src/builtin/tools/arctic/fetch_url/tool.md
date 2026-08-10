# fetch_url

Fetch an `http` or `https` URL and return the response body as text.

## Purpose

Read something that is not on disk: a page of documentation, an API response, a
changelog, a spec. Grounds an answer in what a source actually says now rather
than in what was true when the model was trained.

## When to use it

- The answer depends on a document you can name a URL for.
- You need the current state of something: a version, a status page, a release.
- An API returns the fact you need, and you have its URL.

## When not to use it

- You are guessing at a URL. A 404 costs a round trip and tells you nothing.
- The content is in the workspace. Use `read_file`.
- You want to *find* a page rather than read one. This fetches a URL you already
  have; it does not search.

## Parameters

`spec.json` → `input_schema` is the authoritative contract.

| Parameter         | Type    | Required | Default  | Notes                                                |
| ----------------- | ------- | -------- | -------- | ---------------------------------------------------- |
| `url`             | string  | yes      | none     | Must be `http://` or `https://`.                     |
| `max_bytes`       | integer | no       | `200000` | Bytes returned. Longer bodies are cut and say so.    |
| `timeout_seconds` | number  | no       | `30`     | Whole transfer. The tool's own budget is 45.         |
| `accept`          | string  | no       | none     | `Accept` header, e.g. `application/json`.            |

## Example

```sh
echo '{"url":"https://example.com/api/version","accept":"application/json"}' \
  | src/builtin/tools/arctic/fetch_url/run.sh
```

The body arrives verbatim and undecorated, so a JSON response is still JSON:

```json
{ "version": "2.1.224", "released": "2026-07-30" }
```

That is deliberate. Nothing is prefixed, no status line is added, and no summary
is written, because anything added would have to be stripped again before the
result could be parsed.

## Truncation is the one thing that adds a line

A body longer than `max_bytes` is cut there and followed by a notice:

```
[fetch_url] response truncated: showing 200000 of 1150400 bytes. ...
```

Which means a truncated JSON response no longer parses. If you were fetching
structured data and see that line, the result is incomplete: ask for a narrower
resource, or raise `max_bytes` only if the rest is genuinely needed.

## What it will not do

- **Only http and https.** Any other scheme is refused before curl sees it, so
  `file://`, `ftp://` and the rest are not a way to turn a fetch into a local
  file read.
- **Redirects stay on http(s).** Up to five are followed, and a redirect to
  another scheme is refused rather than followed.
- **No filesystem access.** `permissions.filesystem` is `none`; the response is
  never written into the workspace. Pipe it to `write_file` in a later step if
  it needs to land on disk.

What it *does* do is reach the network, and a URL is a way to carry data out as
well as in. Granting this to an agent gives that agent an egress path for
anything it can read. That is the point of the tool, but it is worth knowing
you granted it.

## Errors

Failures write one line to stderr and set an exit code from `spec.json`'s
`exit_codes`: `2` invalid input, `5` unreachable, `6` an HTTP error status.

```
$ echo '{"url":"https://example.com/nope"}' | src/builtin/tools/arctic/fetch_url/run.sh
fetch_url: https://example.com/nope returned HTTP 404. Not Found
$ echo $?
6
```

`5` is the network not answering at all, and carries curl's own words for why:
DNS failure, connection refused, a TLS problem, or the transfer running past
`timeout_seconds`. Those name the actual cause, so read them before retrying.

```
fetch_url: curl: (6) Could not resolve host: does-not-exist.invalid
```

A `4xx` or `5xx` is different: the server answered, and the first 200 bytes of
what it said are included in the message, because that is usually where the
reason is.
