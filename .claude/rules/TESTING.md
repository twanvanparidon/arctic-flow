# Testing

```sh
pip install -e ".[test]"    # pytest; the runtime dependencies come with it
pytest                      # everything, about twenty seconds
pytest tests/unit -q        # the fast half, about five
pytest tests/unit/engine -q
pytest -k skip_propagation
pytest -x --lf              # stop at the first failure, then rerun only what failed
```

Coverage comes with the same extra, and reads its settings from `pyproject.toml`:

```sh
coverage run -m pytest && coverage report
```

The pipeline runs that in two steps, unit then integration with `--append`, so one file
covers both. It never fails the build on a number: coverage is there to be looked at, and a
floor would mostly measure how much of the code the integration suite reaches in a
subprocess. Which is the caveat to know before reading the table. `coverage` only measures
the process it started, and `tests/integration` spawns `python3 src/main.py` for the stream
and vault tests, so `cli/dispatch.py` and `src/main.py` read far lower than they are.

The extra is there for `pytest` itself. Nothing needs installing for the imports to resolve:
`[tool.pytest.ini_options]` in `pyproject.toml` prepends `src` and `tests` to the path, so a
test imports `engine` by the same name it has once installed, and `support` for the helpers.
CONTRIBUTING.md has the virtual environment to put it all in.

## Test doubles

Three words, used here in their usual senses, and the difference between them decides which
one to reach for:

| | Is | Fails when |
| --- | --- | --- |
| **fake** | a working implementation, simplified | the contract it implements changes |
| **stub** | canned answers, no working parts | never; it answers whatever it was told to |
| **mock** | a recorder, asserted on afterwards | the code calls things in a different order |

**In `tests/unit`, none of the three.** Use the real collaborator.

**In `tests/integration`, all three are allowed, in this order: fake, then stub, then
mock.** Prefer the fake, because it is the only one that can still fail for a real reason:
`fake_claude.py` really parses argv and really writes JSON, so an adapter that builds the
wrong command line is caught by it. Drop to a stub when writing a working implementation
would cost more than the test is worth. Reach for a mock last, and only when the assertion
genuinely is about a call happening, since a mock pins *how* the code went about something
rather than what it decided, which is the same trap as pinning a sentence.

Whichever you use, name it for what it is, and put it in `tests/support/` beside the two
that are there.

### In tests/unit, use the real thing

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

### The three things a unit test may still do

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
YAML to output, which stream each byte left on, the vault from `create` to a step that
reads it, and the shipped examples run the way the docs say to run them. A test here fails
when two parts stop agreeing, so it goes through the CLI rather than calling a function.

**`tests/e2e`** is the built binary, the installed `atf`, and anything needing a
controlling terminal: the password prompt, `vault set` reading from a tty. Still empty.

A thing deferred out of a suite is named in the module docstring that defers it, so the gap
is written down where someone would look for it.

### Running the integration suite

Two runners, and the choice between them is the whole design of the file you are writing:

- **`atf`** calls `cli.app.main` in this process and captures both streams. Everything from
  argv to the flow's output is real. Fast, so it is the default.
- **`atf_process`** spawns `python3 src/main.py`. For claims only a process can make: what
  `> file` contains, what the exit status was, what a piped stdin does. `test_streams.py`
  also attaches pty pairs, because the frame draws only when both streams are terminals.

**`fake_claude` is autouse.** The machine this was written on has the real `claude` on
`PATH`, so without it one stray agent step would reach a real model and cost real money.
`tests/support/fake_claude.py` speaks the protocol of `--print --output-format json` and is
steered by the prompt: `!fail`, `!crash`, `!garbage`, `!contradiction` and `!invocation`
reach the four failure branches and the argv report, and anything else is answered with the
prompt itself. That last part is what makes a gate loop observable: the engine appends the
gate's feedback to the next prompt, so the second turn really does differ from the first.

A fake rather than a stub, which is the order to work down: it is a working program on the
other side of a real pipe, so an adapter that built the wrong command line fails against it.
A stub returning a canned envelope would have answered anyway. Prove it is not the real CLI
the way the suite does: `PATH=/usr/bin:/bin pytest` passes, so nothing here needs one
installed.

The shipped examples need what their specs declare: `jq`, `openssl`, `xxd`, `awk`,
`realpath`. `conftest.requires()` skips with the missing name rather than failing, since a
machine without `jq` is an environment and not a defect. `-ra` prints every skip, so it is
never silent.

### What no suite reaches yet

| Not covered | Needs | Belongs in |
| ----------- | ----- | ---------- |
| `resolve_password`'s prompt | a controlling terminal, or getpass hangs on `/dev/tty` | e2e |
| `vault set` prompting for a value | the same | e2e |
| the PyInstaller binary, and `atf` as an installed script | a build | e2e |
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
- **Something a script parses.** `atf --version` printing one line on a pipe, `atf ` then
  the version. The number is stamped in from the tag at build time, so assert that shape
  around `branding.__version__` rather than around a literal.
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
