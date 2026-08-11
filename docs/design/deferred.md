# Deferred

Written down so the decision is not re-litigated, and so the trigger is recognisable.

## A portable model vocabulary

An agent names `model: "sonnet"`, which is one provider's word. The obvious fix is a `tier`
enum on the agent (`fast`/`balanced`/`deep`) plus a `MODELS: dict[str, str]` on each adapter,
resolved inside `specs.adapter_parameters` so the existing lint probe is the check, with
`tier` and `model` together refused.

Not built, for two reasons.

The vocabulary is **unknowable with one adapter**. `model` and `effort` both move depth, so
whether `deep` means the bigger model or more thinking has no evidence behind it, and
`AGENT_SPEC_SCHEMA` is a contract with a release already tagged.

And it would not deliver what it looks like it delivers, because `adapter` is itself required
in every agent spec with no override anywhere. A tier changes what you edit per spec from two
fields to one. The larger coupling is `adapter`, and an ambient `$ATF_ADAPTER` would recreate
exactly the per-machine dependency that `isolate` and the required `model` exist to remove.

**Trigger: a second adapter.** It settles the vocabulary, and it is the rule
`.claude/rules/CONTRIBUTING.md` already names: the second adapter earns a change to the
adapter interface, not the first.

## Cancelling an agent turn

The run ceiling reaches every tool subprocess but cannot reach a turn already started, because
`adapter.run` is synchronous with no way in. The ceiling is a ceiling plus at most one turn.
See [execution](execution.md#the-run-ceiling).

**Trigger: putting cancellation into the adapter contract.** Same rule as above, since that
interface has one real implementation.

## Scoping a secret to one in-turn call

A granted tool gets no secrets, refused from both ends, because the adapter is handed the
step's grant and a tool the agent calls would inherit the lot. See [secrets](secrets.md).

**Trigger: a per-call scoping mechanism.** Until then, secrets belong to tool steps, and no
credentialled tool can be granted.

## A schema for flow YAML

`validate` checks keys imperatively, so an unknown key is ignored rather than refused. A
misspelled key is therefore silent, which is the one place the engine does not fail loudly.

**Trigger: someone losing time to it.** The cost is that the schema becomes a contract of its
own, and it has to stay in step with `validate` or the two disagree.

## `prompt_file` for the flow's output

`output.template` has the same readability problem a step's prompt had and would work the same
way. Nobody has asked.

## A published-checksum test for `install.sh`

It needs a release that exists, and the e2e job runs on the tag **before** the release job
publishes, so the asset it would fetch does not exist yet. `tests/e2e/test_install.py` covers
what `install.sh` produces (the archive, its checksum, the linked layout) and says in its
docstring which half it leaves.

**Trigger: a post-publish job**, which would run against a release rather than a tag.

## Platforms other than Linux x86-64

PyInstaller cannot cross-compile. macOS and Windows need runners on those platforms, which is
a CI change rather than a code change.
