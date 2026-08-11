# Secrets

A step declares `secrets: [name]` and the engine passes that step **only** those, as
environment variables. Everything else here follows from keeping that sentence true.

## Why the environment and not the template

A tool reads a credential from its environment rather than from its `input`, because `input`
is data the flow wrote and the engine logs. `{{ secrets.NAME }}` exists for the case where a
tool genuinely takes the value as a parameter, and it is restricted to a tool step's `input`
for a name that step declared.

The declaration is on the **step**, not the tool, so what a step can read is visible where the
step is written. Reading the flow answers the question; you never have to open a spec to find
out what a run can touch.

## Four rules, all enforced by lint

**A secret in an agent prompt is refused outright.** It would be sent to the model and persist
in the session, and no scrubbing afterwards undoes that. Credentials reach an adapter through
the environment instead.

**`{{ secrets.NAME }}` works only for a name that step declared.** Otherwise the template
would render something the step was not given, and the failure would arrive at run time as an
unresolvable path rather than at lint time as a rule.

**Granting a tool that declares `secrets` is refused.**

**A step that declares `secrets` and runs a tool-granted agent is refused.**

The last two are the same rule from both ends. The adapter is handed the step's grant, and a
tool the agent calls would inherit the whole environment, which is wider than what `spawn`
promises a component. The engine keeps the secret out of the process tree entirely rather than
trying to strip it two processes down.

That pair is why no forge tool can ever be granted: every one declares `secrets`, so opening a
pull request or commenting is always a step the flow decided on, never a model's mid-turn
choice. It is the intended design rather than a limitation worked around, and per-call scoping
is what would change it. See [deferred](deferred.md).

## Results are not scrubbed

Values are scrubbed from errors and traces. They are **not** scrubbed from step results.

A result is data the flow asked for. Scrubbing it would corrupt the workflow rather than
protect it: a tool that legitimately returns a token-derived value would find it replaced by
`***` in the next step's input. So the rule is stated the other way round, at the flow author:
never template a secret into something you would not print.

`inspect flow -o md` prints a secrets column when any step declares one, so which step holds
what is answerable without running anything. That column exists so the claim above is
checkable rather than trusted.

## The vault file

```
$ARCTIC_FLOW_VAULT;1.0;AES256GCM;SCRYPT;n=16384,r=8,p=1
<base64 of salt(16) ‖ nonce(12) ‖ ciphertext>
```

Mode `0600`. The header is the GCM associated data, so editing it fails decryption rather than
downgrading anything: the parameters cannot be talked down to something cheaper.

A wrong password and a tampered file are **indistinguishable by design**. Both mean the
ciphertext did not authenticate, and saying which would tell an attacker whether the password
was the part that was wrong.

It is opened lazily, in `commands.prepare`, so a flow with no secrets never prompts. That is
what lets most flows ignore all of this.

Committing one is fine, and `examples/sign-release/secrets.vault` is committed on purpose so
the example runs with nothing to prepare.

## No secret in argv

There is deliberately no `--vault-password` flag, and `vault set` has no `--value`. `ps` shows
a command line to every user on the machine, for the length of the process.

The same rule reaches into the network packs: `lib/api.sh` hands curl a config file with mode
600 rather than `-H`, because `-H` would put the token in curl's own argv.

Passwords arrive from `--vault-password-file`, `$ATF_VAULT_PASSWORD_FILE`,
`$ATF_VAULT_PASSWORD`, or a prompt, in that order.

## Names are environment variable names

`^[A-Za-z_][A-Za-z0-9_]*$`, checked when a secret is set rather than when it is used, because
that is the point where someone can still choose a different name.
