# sign-release

HMAC-signs a file with a key from an encrypted vault. Two steps, one secret, no setup.

## Run it

The demo vault's password is `demo`, and the key in it is fake.

```sh
ATF_VAULT_PASSWORD=demo atf \
    --workspace examples/sign-release \
    run sign_release --input path=release-notes.md
```

```
b3b7f6ca731a0de0d59f59aaf5c3ed4f6c0b7eb76cf513ae81cf5ed7a025005b  release-notes.md
```

Same shape as `sha256sum`, so it redirects into a signature file:

```sh
ATF_VAULT_PASSWORD=demo atf --workspace examples/sign-release \
    run sign_release --input path=release-notes.md > release-notes.md.sig
```

The signature is real. Anyone holding the key verifies it without this engine:

```sh
ATF_VAULT_PASSWORD=demo atf vault view examples/sign-release/secrets.vault
openssl dgst -sha256 -hmac '<signing_key>' -r < examples/sign-release/release-notes.md
```

## What the vault is doing

`signing_key` appears nowhere in the flow, reaches no prompt, and is absent from the
output. The engine hands it to the `sign` step as an environment variable and to nothing
else. `read_artifact`, one step earlier, cannot read it.

That is a claim you can check without running anything:

```sh
atf --workspace examples/sign-release inspect flow sign_release -o md
```

| step | secrets |
| ---- | ------- |
| `read_artifact` | _none_ |
| `sign` | `signing_key` |

A grant is written where the step is, so what a flow can touch is reviewable from the
flow file, and the diagram surfaces it for a reviewer who is not reading YAML.

## Managing the vault

`secrets.vault` is committed. That is the point of the format: encrypted at rest,
reviewable as a diff, no side channel for distributing it. The password is what must
never be committed.

```sh
V=examples/sign-release/secrets.vault
export ATF_VAULT_PASSWORD=demo

atf vault list $V                        # names only, safe to run in front of people
atf vault view $V                        # decrypts to stdout
printf 'rotated-key' | atf vault set $V signing_key
```

`vault set` reads the value from stdin, or prompts. There is no `--value` flag and no
`--vault-password` flag, because either would put the secret into shell history and the
process list.

## Two ways a secret can arrive

This example uses one of them:

- **Environment.** What `signing_key` does here, and the right default for a credential.
  It stays out of the flow, out of rendered inputs, and out of the diagram.
- **Templated.** `{{ secrets.NAME }}` in a step's `input`, for configuration you are
  willing to read in the output. A template renders into step input, which flows to the
  next step and into the flow's result, and the engine scrubs secrets from errors and
  traces but *not* from results. Never template something you would not print.

An agent step cannot template a secret at all. `lint` refuses it, because that sends the
value to the model and leaves it in the session:

```yaml
- id: nope
  agent: summarizer
  secrets: [signing_key]
  prompt: "the key is {{ secrets.signing_key }}"
```

## Layout

```
flows/sign_release.yaml    the graph and the grant
tools/hmac_sign/            signs with signing_key from the environment
secrets.vault               encrypted, committed, password "demo"
release-notes.md            the artifact being signed
```

`common/read_file` is not here. It is inherited from the engine's built-ins. This project
defines only what is specific to it.
