# Contracts

A contract here is **checked, not described**: JSON Schema for payloads, dataclasses for
results, exceptions for failures. If it is only written in a docstring, it is not a contract.

These are the ones other people's flows, specs and vaults are written against. Adding an
optional field to any of them is usually safe. Renaming, removing, tightening a type, or
changing what a value means is not.

**Ask before changing an existing one.** Say what breaks, what the migration is, and whether
`lint` catches it.

| Contract | Where | Breaking it means |
| --- | --- | --- |
| `INPUT_SCHEMA` on an adapter, and the envelope `run()` returns | `src/adapters/*.py` | agent specs stop validating, or the progress line loses its cost |
| A tool's `spec.json`: `input_schema`, `output_schema`, `exit_codes`, and JSON-on-stdin / text-on-stdout | every tool directory | every third-party tool |
| `TOOL_SPEC_SCHEMA`, `AGENT_SPEC_SCHEMA`, `FORWARDED` | `engine/specs.py` | every component on disk |
| `CONFIG_SCHEMA` | `paths/config.py` | a file already in someone's home directory |
| `ENGINE_NAMESPACE` | `paths/resolver.py` | see below |
| The flow YAML keys read by `validate` | `engine/executor.py` | every flow ever written |
| The dataclasses in `results.py`, and what `commands/__init__.py` exports | `src/commands/` | a second front end |
| `EXPECTED_ERRORS`, and the event dicts passed to `on_event` | `commands/`, `engine/` | exit codes, and any observer |
| CLI flag names and exit codes | `cli/app.py` | every script and pipeline |

## The ones with a sharper edge

**`CONFIG_SCHEMA` refuses unknown keys.** That makes adding a key safe: an older engine
reading a newer config fails loudly rather than ignoring the setting someone relied on. It
also makes removing or renaming one break a file already on disk, with no way to warn first.

**`ENGINE_NAMESPACE`.** Widening it takes names away from people who already use them.
Narrowing it lets a flow's `arctic/read_file` be something else, which is the exact thing it
exists to prevent. Renaming it means moving every shipped component and every reference to
one. See [resolution](resolution.md#arctic-is-refused-not-preferred).

**The flow YAML keys are checked imperatively, not against a closed schema.** So an unknown
key is ignored rather than refused. That is a real gap: a misspelled key is silent. Closing it
means a schema for flows, which would then itself be a contract.

**Exit codes.** 0, 1 for an expected failure, 2 for bad arguments, 130 for interrupted.
`EXPECTED_ERRORS` is what turns an exception into a 1 with a one-line message instead of a
traceback; anything not in that tuple keeps its traceback on purpose, because it is a bug.

## Compatibility, before and after a release

Before the first release there is nothing to stay compatible with, and a shim for a spelling
nobody ever shipped is rot.

Once there is a release, an approved rename **names the old spelling and explains it** rather
than dropping it, so a file written against the old vocabulary is told what to change instead
of failing on a missing key.

## Where a contract gets checked

The rule is that the check lives where the code depends on it, not in a validation layer of
its own. `engine/specs.py` holds the spec schemas because `run` needs them before its first
step. `paths/config.py` holds `CONFIG_SCHEMA` because `Paths` cannot be built without it.
`check_name` sits in the resolver because `run`, a grant and `mcp-serve` all go through it.

That is also why `atf lint` is not a separate validator: it calls exactly what `run` calls.
Two validators drift, and the one people do not run is the one that matters.
