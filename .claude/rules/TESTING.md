# Testing

```sh
pip install ".[test]"   # pytest; the runtime dependencies come with it
pytest                  # the whole suite, a few seconds
pytest tests/unit/engine -q
pytest -k skip_propagation
```

Configuration is `[tool.pytest.ini_options]` in `pyproject.toml`. It prepends `src` and
`tests` to the path, so a test imports `engine` by the same name it has once installed, and
`support` for the helpers. There is nothing to install first.

## No mocks

Do not patch a unit under test, and do not build an object that pretends to be one of its
collaborators. Use the real thing:

| Instead of | Use |
| ---------- | --- |
| a fake `subprocess.run` | `support.components.write_tool`, and let the engine spawn it |
| a fake filesystem | `tmp_path` |
| a stubbed AESGCM | real scrypt and real AES-GCM. A vault round trip costs milliseconds |
| an object with `isatty()` | `support.terminal.Terminal`, a real pseudo-terminal |
| a mock observer | a list, and `list.append` as the observer |

The reason is not purity. The failures worth catching in this engine are the ones a
substitute cannot have: a script that lost its executable bit, a process that outlives its
timeout, a secret in an environment it was not granted, a wrong password that has to be
indistinguishable from a tampered file, a terminal that gets a frame and a pipe that does
not. Every one of those is invisible to a test whose collaborator is a stand-in.

Where a test wants to observe something, have the real component print it. `echoes_env`
exists so a test can see which secrets a step was actually granted; the echo adapter puts
the payload it received in its envelope. Nothing has to watch a call happen.

### The three things that are allowed, and why

**Environment control.** `monkeypatch.setenv`, `delenv` and `setattr(sys, "frozen", True)`
set real values that real code reads. `conftest.clean_environment` uses this to remove
`ATF_PATH`, `NO_COLOR` and the rest, and to point `$HOME` at `tmp_path`. That is not
optional: `~/.arctic` is a search root, so without it the suite passes or fails depending on
what the developer has installed at home.

**A real adapter for tests.** `support/adapter_echo.py` implements the adapter contract and
answers with the prompt it was given. `agent_turn` takes its adapter as an argument, so
those tests pass the module in. `run_agent` looks one up by name, so those register it in
`ADAPTERS`, which is how the docs say an adapter is registered. The alternative is a network
call and an account, which is not a unit test of anything.

**Reading a private helper.** `_check_input`, `_parse_header` and `_duration` encode rules
worth a test of their own. Test them directly rather than reaching them through six layers.

## What goes where

**`tests/unit`** is one function's contract at a time. Real collaborators are fine and
expected; what makes it a unit test is that one function's behaviour is what fails. It must
stay fast (seconds) and need nothing installed beyond a POSIX shell and Python.

**`tests/integration`** is the castle rather than the blocks: whole commands, a flow from
YAML to output, the CLI's exit codes and streams, the shipped examples. It may use a fake
`claude` on `PATH` to exercise the adapter's `run()`, which the unit suite deliberately does
not.

**`tests/e2e`** is the built binary and anything needing a controlling terminal: the
password prompt, `vault set` reading from a tty.

A thing deferred out of the unit suite is named in the module docstring that defers it, so
the gap is written down where someone would look for it.

### What the unit suite deliberately does not reach

Everything else is covered. These are the six that need something a unit test should not
have, and they are the whole of what the empty suites owe:

| Not covered | Needs | Belongs in |
| ----------- | ----- | ---------- |
| `claude_code.run` and the success path of `cli_version` | the `claude` binary | integration |
| the handlers in `cli/dispatch.py` | a whole command, both streams | integration |
| `resolve_password`'s prompt | a controlling terminal, or getpass hangs on `/dev/tty` | e2e |
| `main`'s KeyboardInterrupt to exit 130 | a signal to a real process | e2e |
| `src/main.py` | running the file rather than importing it | e2e |
| one `continue` in `execute` | a step with no inbound edge, which `validate` refuses | nothing |

## Assert the decision, not the sentence

A test that fails when someone improves an error message is a test that punishes an
improvement. Assert what the code *decided*, which is almost always an identifier that came
out of the input:

```python
match="secrets.token"                          # the reference was refused
match="That sends the secret to the model"     # not this: it pins a sentence

assert "read" in line and "tool read_file" in line     # both facts are on the line
assert line == "→ read           tool read_file\n"     # not this: it pins a column width
```

Keep just enough of the wording to tell one refusal from another. `validate` has several
that could fire on the same flow, so `"unknown step 'ghost'"` earns its extra words where
`"max_attempts"` does not.

Three places where the format **is** the decision, and exact strings are right:

- **Bytes on a stream.** `atf run > file` producing the flow's output and nothing else.
- **Something a script parses.** `atf --version` printing `atf 0.1.0` on a pipe.
- **Where the formatting is the logic.** `count(1, "step")`, `_duration(60)`, `_money`.

Do not assert an escape sequence. Ask the painter for it, so the decision is checked and
the palette stays free to change:

```python
assert PAINT("--quiet", "green") in painted
```

Do not test the language. A frozen dataclass being frozen, a `default_factory` producing
`()`, `callable(module.run)`: all true by construction, and none of them is this repo.

## Writing one

Name the behaviour, not the function: `test_a_join_runs_once_one_of_its_branches_is_skipped`
beats `test_execute_3`. The docstring is for **why**, following
[WRITINGSTYLE.md](WRITINGSTYLE.md), and most tests do not need one. Where the code carries a
comment explaining why a check exists, the test for it should carry the same reason, so
deleting the check breaks a test that says what it was for.

Group with a class per unit (`class TestRender:`). Use `parametrize` for a rule with several
inputs and a separate test for a separate rule.

The suite is linted and formatted with everything else:

```sh
ruff check src packaging tests
ruff format --check src packaging tests
```

So `from __future__ import annotations` at the top, type hints on every signature including
`-> None`, and 100 columns.

## Fixtures

Defined in `tests/conftest.py`:

| Fixture | Is |
| ------- | -- |
| `workspace` | an empty project root under `tmp_path` |
| `home` | the temporary home directory, and what `$HOME` points at |
| `paths` | a `Paths` pinned to both, with `env={}` so no `ATF_PATH` leaks in |
| `echo_adapter` | the test adapter, registered in `ADAPTERS` for the test |
| `terminal` | a real pseudo-terminal: `.stream` to write to, `.read()` for what came out |
| `two_terminals` | a pair, for the output frame, which needs both streams to be terminals |
| `clean_environment` | autouse; the isolation described above |

`support/components.py` writes components: `write_tool`, `write_agent`, `write_flow`, and
the small scripts to give them (`prints`, `fails`, `echoes_env`, `rendezvous`, `sleeps`).
Prefer adding a script builder there over writing shell inline in a test.

## Determinism

Nothing in the suite may depend on the machine it runs on. Concretely:

- Never assert on wall-clock timing. Assert `ms >= 0`, not `ms < 50`.
- Never sleep to order two things. `rendezvous` makes two steps wait for each other, and
  fails loudly with its own deadline instead of hanging the suite.
- Give any tool that could hang a `timeout_seconds`, and keep it well under the engine's
  60s default.
- Assert on the shortened display path (`./tools/greet`), never on an absolute one.
