#!/usr/bin/env bash
#
# hmac_sign: HMAC-SHA256 a payload with the signing_key from the environment.
#
# The key arrives as an environment variable because the engine granted it to this
# step. It is never an input: an input would be templated into the flow, and from
# there into logs and diagrams.

set -euo pipefail

fail() { printf 'hmac_sign: %s\n' "$2" >&2; exit "$1"; }

input=$(cat)

jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" \
  || fail 2 'stdin must be a single JSON object matching spec.json'
jq -e 'has("payload") and (.payload | type == "string")' >/dev/null 2>&1 <<<"$input" \
  || fail 2 "missing required parameter 'payload'"

# Its own exit code, so a missing grant is distinguishable from a bad payload.
[[ -n ${signing_key:-} ]] \
  || fail 5 "signing_key is not in the environment: add it to this step's 'secrets'"

# Two things this must not do, both easy to get wrong in a shell:
#
#   Pass the key as an -hmac argument. It would be visible in the process list to any
#   other user on the machine, so it goes in as a hex macopt instead.
#
#   Round-trip the payload through $(...) or a variable. Command substitution strips
#   trailing newlines, so a body ending in one would be signed a byte short and the
#   signature would not verify against the real file. jq -j streams
#   the exact bytes straight into openssl.
jq -j '.payload' <<<"$input" \
  | openssl dgst -sha256 -mac HMAC -macopt "hexkey:$(printf '%s' "$signing_key" | xxd -p -c 256)" -r \
  | awk '{ printf "%s", $1 }'   # no trailing newline: a single value, so it can be
                                  # templated mid-line without breaking the line
