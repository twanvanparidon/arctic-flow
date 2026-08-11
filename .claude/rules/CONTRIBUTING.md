# Contributing code

The design and the layout are in `docs/design/`, and how to add a component is in
`docs/components.md`. The repo root `CONTRIBUTING.md` is the process: running the checkout,
the gate, tests, the build and the release. Read those first. This file is about how you
write the code.

## Comments and docs

Follow [WRITINGSTYLE.md](WRITINGSTYLE.md). It covers comments, docstrings, commit
messages, and anything else you write here.

Comments explain why, not what. The code already says what it does.

**Nothing that rots.** A comment has to still be true once the branch is gone. Never tie
one to a branch, a PR, or a half-finished state: "new in this PR", "temporary until X
lands", "changed from the old version", "TODO before review". That belongs in the commit
message or the PR description, where it expires on its own.

**Nothing the contract already says.** If the name, the signature, or the schema tells you,
do not repeat it. `# returns the parsed result` above a function returning `Result` earns
nothing and goes stale on the first rename.

**Do write what the contract cannot show.** Ordering that matters. What happens on the path
that is not taken. Why a check sits where it does rather than one line later. A failure
mode that only appears in a frozen build, like the library path correction in
`child_environment`. Where the obvious approach is wrong, say what happens if someone tries
it. If you fix something subtle, leave the reason behind.

**Warnings are welcome.** If touching this breaks something over there, say so where
someone would touch it. `run_step` carries "execute() prefixes the step id onto step
failures, so don't repeat it", which is the kind that stops a duplicated error message
before it is written.

## Code has to pass the gate

Run these before you report the work as done:

```sh
ruff check src packaging tests
ruff format --check src packaging tests
shellcheck -x $(find . -name '*.sh' -not -path './dist/*' -not -path './build/*' -not -path './var/*')
pytest
```

Line length is 100. The rule set is E, F, W, I, UP, B, configured in `pyproject.toml`.
Never silence a finding with a bare `# noqa`. If you need one, give the reason on the same
line, the way `commands/flows.py` does for its lazy imports.

Be pedantic. Type hints on every signature. `from __future__ import annotations` at the
top of the module. Real types in and real types out, not `dict[str, Any]` where a dataclass
fits. No bare `except`, no mutable default arguments, no unused parameter kept "for later".

## Tests

New code needs tests. Follow [TESTING.md](TESTING.md).

The short version, and it differs by suite:

- **`tests/unit`: no doubles at all.** A tool test writes a real tool directory and lets the
  engine spawn a real process. A vault test encrypts with real scrypt. A test about
  `isatty()` opens a real pseudo-terminal.
- **`tests/integration`: fakes, stubs and mocks are all fine, in that order of preference.**
  A fake is a working implementation and can still fail for a real reason, so reach for it
  first. A stub only answers. A mock asserts on how the code went about something rather
  than on what it decided, so it comes last.
- **`tests/e2e`: the built binary, not this source.** A test belongs here when it would pass
  against `src/` and still ship something broken. It needs a build, and skips without one.

TESTING.md has the taxonomy, the two things a unit test may still do, and why neither is a
double.

## Minimal changes

Change what the task asks for and nothing else. No drive-by renames, no reformatting of
lines you did not have to touch, no reordering imports or functions for tidiness.

If you spot something worth refactoring, say so and leave it. Large changes need the
operator to ask for a large change. When they do, state the blast radius before you start.

## Simple code

YAGNI. Build what is needed now. No config flag, hook, or abstraction layer for a case
nobody has asked for. The second adapter is what earns a change to the adapter interface,
not the first.

SOLID is the direction, not a ritual. One reason to change per module. Prefer extending by
adding a file, so a new tool is a new directory and a new adapter is a new module plus one
line in `ADAPTERS`.

Keep it concrete:

- No class where a function works.
- No abstract base class with one implementation. Adapters are duck-typed modules on
  purpose. Leave them that way until a second runtime proves otherwise.
- A function should read without scrolling.
- Prefer a plain dict of static imports over dynamic lookup by name. A frozen build cannot
  follow the second kind.

## Contracts

Write the contract where the code enforces it. In this repo a contract is checked, not
described in a docstring: JSON Schema for payloads, dataclasses for results, exceptions for
failures.

These are contracts. Other people's flows, specs, and vaults are written against them:

- `INPUT_SCHEMA` on an adapter, and the envelope its `run()` returns.
- A tool's `spec.json`: `input_schema`, `output_schema`, `exit_codes`, and the JSON on
  stdin, text on stdout protocol.
- `TOOL_SPEC_SCHEMA`, `AGENT_SPEC_SCHEMA` and `FORWARDED` in `engine/specs.py`.
- `CONFIG_SCHEMA` in `paths/config.py`, which is what `~/.arctic/config.yaml` may hold.
  It refuses unknown keys, so adding one is safe and removing or renaming one breaks a
  file already on someone's disk.
- `ENGINE_NAMESPACE` in `paths/resolver.py`. Widening it takes names away from people who
  already use them, and narrowing it lets a flow's `arctic/read_file` be something else,
  which is the thing it exists to prevent. Renaming it means moving every shipped
  component and every reference to one.
- The flow YAML keys read by `engine.executor.validate`.
- The dataclasses in `commands/results.py`, and the names `commands/__init__.py` exports.
- `EXPECTED_ERRORS`, and the event dicts the engine passes to `on_event`.
- CLI flag names and exit codes.

Adding an optional field is usually safe. Renaming, removing, tightening a type, or
changing what a value means is not. **Ask the operator before you change an existing
contract.** Say what breaks, what the migration is, and whether `lint` catches it.

Once there is a release to be compatible with, an approved rename names the old spelling
and explains it rather than dropping it, so a file written against the old vocabulary is
told what to change instead of failing on a missing key. Before the first release there is
nothing to stay compatible with, and a shim for a spelling nobody ever shipped is rot.
