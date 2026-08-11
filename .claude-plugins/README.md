# Arctic Flow skills for Claude Code

Two skills for people writing flows, in whatever project their flows live in. They drive
the `atf` the user already has installed. Nothing here is a second copy of the engine.

| Skill | For |
| --- | --- |
| `/atf:create` | scaffolding a flow, an agent or a tool, and filling it in |
| `/atf:help` | a flow that will not lint, will not run, or costs more than it should |

Both also trigger on their own. Ask for a flow, or paste a refusal, and the right one loads.

## Install

```
/plugin marketplace add twanvanparidon/claude
/plugin install atf@twanvanparidon
```

The plugin is `atf` and the skills inside it are `create` and `help`, so a skill is invoked
as `/atf:create`. Then `/plugin update atf` for a later version. The marketplace reads
`main`, so a fix to a skill reaches users without a release, the same way
`packaging/install.sh` does.

The skills expect `atf` on the `PATH`:

```sh
curl -fsSL https://raw.githubusercontent.com/twanvanparidon/arctic-flow/main/packaging/install.sh | bash
```

A checkout works too. Both fall back to `python3 src/main.py`, which is the same program.

## What is in them

`create` is a procedure. It finds the engine, runs `atf create` rather than writing a
`spec.json` by hand, fills in the scaffold, and does not report the work done until
`atf lint` passes. It carries the decisions a flow author actually makes: a tool step or an
agent step, `push` or `switch`, a check or a loop, and when granting an agent a tool is worth
what it costs.

`help` is a diagnosis. It runs `lint` before it reads anything, then `inspect` and
`list`, and only then reasons about the YAML. Beside it are four references:

| File | Covers |
| --- | --- |
| `skills/help/references/flow-yaml.md` | every flow and step key, and what each one refuses |
| `skills/help/references/templates.md` | the five namespaces, `.text` and `.json`, `(not run)` |
| `skills/help/references/components.md` | tool and agent `spec.json`, adapters, name resolution, the vault |
| `skills/help/references/errors.md` | each refusal, its cause and its fix |

The installed engine is the authority. Where a reference and `atf` disagree, `atf inspect`,
`atf lint` and `--help` are what the skills are told to believe. So a change to what the
engine refuses is a change to `errors.md`.

## Changing one

This directory is the plugin. `.claude-plugin/plugin.json` is what makes it one, and that
path is fixed by Claude Code. The name of this directory is not, but it is named as the
`git-subdir` path of a `marketplace.json` in
[twanvanparidon/claude](https://github.com/twanvanparidon/claude), so renaming it means
editing that repository in the same breath. The catalog lives there rather than here so that
one marketplace name can serve plugins for other projects too.

`version` in `plugin.json` is what tells an installed copy that there is something newer, so
a fix nobody can reach is a fix that did not bump it.

```sh
claude plugin validate .claude-plugins --strict   # from the repository root
```

It is not in the pre-push gate: it needs `claude` and does not need `atf`.

Two rules that are refused at load rather than reported:

- A skill's `name` is kebab-case, `^[a-z0-9-]+$`. An underscore is not a legal name.
- The `description` is one YAML scalar, so no unquoted `: ` and no angle brackets.

The `description` is also the whole of what decides when a skill fires, so it carries the
triggers rather than the body.
