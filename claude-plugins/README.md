# Arctic Flow skills for Claude Code

Two skills for people writing flows, in whatever project their flows live in. They drive
the `atf` the user already has installed. Nothing here is a second copy of the engine.

| Skill | For |
| --- | --- |
| `/atf-create` | scaffolding a flow, an agent or a tool, and filling it in |
| `/atf-help` | a flow that will not lint, will not run, or costs more than it should |

Both also trigger on their own. Ask for a flow, or paste a refusal, and the right one loads.

## Install

```
/plugin marketplace add twanvanparidon/arctic-flow
/plugin install arctic-flow@arctic-flow
```

Then `/plugin update arctic-flow` for a later version. The marketplace reads `main`, so a
fix to a skill reaches users without a release, the same way `packaging/install.sh` does.

The skills expect `atf` on the `PATH`:

```sh
curl -fsSL https://raw.githubusercontent.com/twanvanparidon/arctic-flow/main/packaging/install.sh | bash
```

A checkout works too. Both fall back to `python3 src/main.py`, which is the same program.

## What is in them

`atf-create` is a procedure. It finds the engine, runs `atf create` rather than writing a
`spec.json` by hand, fills in the scaffold, and does not report the work done until
`atf lint` passes. It carries the decisions a flow author actually makes: a tool step or an
agent step, `push` or `switch`, a gate or a loop, and when granting an agent a tool is worth
what it costs.

`atf-help` is a diagnosis. It runs `lint` before it reads anything, then `inspect` and
`list`, and only then reasons about the YAML. Beside it are four references:

| File | Covers |
| --- | --- |
| `skills/atf-help/references/flow-yaml.md` | every flow and step key, and what each one refuses |
| `skills/atf-help/references/templates.md` | the five namespaces, `.text` and `.json`, `(not run)` |
| `skills/atf-help/references/components.md` | tool and agent `spec.json`, adapters, name resolution, the vault |
| `skills/atf-help/references/errors.md` | each refusal, its cause and its fix |

The installed engine is the authority. Where a reference and `atf` disagree, `atf inspect`,
`atf lint` and `--help` are what the skills are told to believe. So a change to what the
engine refuses is a change to `errors.md`.

## Changing one

This directory is the plugin. `.claude-plugin/plugin.json` is what makes it one, and
`.claude-plugin/marketplace.json` at the repository root points here. Both of those paths
are fixed by Claude Code and cannot move; the name of this directory is free.

```sh
claude plugin validate . --strict                # the marketplace manifest
claude plugin validate claude-plugins --strict   # the plugin manifest
```

Neither is in the pre-push gate: both need `claude` and neither needs `atf`.

Two rules that are refused at load rather than reported:

- A skill's `name` is kebab-case, `^[a-z0-9-]+$`. An underscore is not a legal name.
- The `description` is one YAML scalar, so no unquoted `: ` and no angle brackets.

The `description` is also the whole of what decides when a skill fires, so it carries the
triggers rather than the body.
