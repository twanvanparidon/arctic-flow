# Testing

```sh
pip install -e ".[test]"    # pytest; the runtime dependencies come with it
pytest                      # everything, about twenty seconds
pytest tests/unit -q        # the fast half, about five
pytest tests/unit/engine -q
pytest -k skip_propagation
pytest -x --lf              # stop at the first failure, then rerun only what failed
```

`tests/e2e` needs a built binary and skips without one, so a plain `pytest` stays green on a
checkout. Building one is in CONTRIBUTING.md and takes a few minutes.

Coverage comes with the same extra, and reads its settings from `pyproject.toml`:

```sh
coverage run -m pytest && coverage report
```

The pipeline runs that in two steps, unit then integration with `--append`, so one file
covers both. `tests/e2e` is not in it and cannot be: it drives a binary with its own
interpreter, so there is no source tree for `coverage` to watch. It never fails the build on
a number: coverage is there to be looked at, and a floor would mostly measure how much of
the code the integration suite reaches in a subprocess. Which is the caveat to know before reading the table. `coverage` only measures
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
| a stubbed model runtime | `adapters.echo`, which ships and answers from the request |

The reason is not purity. The failures worth catching in this engine are the ones a
substitute cannot have: a script that lost its executable bit, a process that outlives its
timeout, a secret in an environment it was not granted, a wrong password that has to be
indistinguishable from a tampered file, a terminal that gets a frame and a pipe that does
not. Every one of those is invisible to a test whose collaborator is a stand-in.

Where a test wants to observe something, have the real component print it. `echoes_env`
exists so a test can see which secrets a step was actually granted; an `!invocation` prompt
makes the echo adapter answer with the request it was handed. Nothing has to watch a call
happen.

### The two things a unit test may still do

**Environment control.** `monkeypatch.setenv`, `delenv` and `setattr(sys, "frozen", True)`
set real values that real code reads. `conftest.clean_environment` uses this to remove
`ATF_PATH`, `NO_COLOR` and the rest, and to point `$HOME` at `tmp_path`. That is not
optional: `~/.arctic` is a search root, so without it the suite passes or fails depending on
what the developer has installed at home.

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
controlling terminal: the password prompt, `vault set` reading from a tty. It asks the
questions the other two cannot, because their subject is a checkout and its subject is an
artefact. A test belongs here when it would pass against `src/` and still ship something
broken.

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

### Running the end-to-end suite

It drives a binary, so first there has to be one. `dist/atf/atf` is found on its own;
anywhere else, name it:

```sh
pytest tests/e2e -v                      # after the docker build in CONTRIBUTING.md
ATF_BINARY=/some/where/atf pytest tests/e2e
ATF_EXPECTED_VERSION=v0.2.0 pytest tests/e2e   # what the pipeline adds on a tag
```

Without a binary every test skips, naming how to get one. That is deliberate: `testpaths`
collects this directory on a plain `pytest`, and a developer who has not spent minutes on a
Docker build should not be shown a failure for it.

**Never invoke a bare `atf`.** The pipeline installs the project into the same job, so there
is one on `PATH` and it is the checkout. Go through the `atf` fixture.

Three things only this suite can ask, and each is worth knowing before adding to it:

- **A frozen process spawning a system binary.** PyInstaller points `LD_LIBRARY_PATH` at the
  bundle, and a child inheriting it loads the bundle's OpenSSL instead of the system's.
  `child_environment()` undoes that, and `sign_release` is the test which says so.
- **`atf` reached through a symlink.** `install.sh` links `<prefix>/bin/atf` at a bundle in
  `<prefix>/lib`, and PyInstaller has to resolve one from the other.
- **A prompt.** `getpass` opens `/dev/tty`, so it needs a *controlling* terminal, which
  `support/console.py` acquires and `support/terminal.py` cannot.

Agent steps here use `adapters.echo`, the shipped adapter that answers from the request.
`ADAPTERS` is frozen into the binary, so a test cannot register one from outside; an adapter
that answers without a runtime is the only way in. `fake_claude` is autouse here too, for the
two shipped examples that name `claude_code`.

### What no suite reaches yet

| Not covered | Needs | Belongs in |
| ----------- | ----- | ---------- |
| `install.sh`'s download and published checksum | a release that exists | nothing yet |
| one `continue` in `execute` | a step with no inbound edge, which `validate` refuses | nothing |

The first is not an oversight. The e2e job runs on the tag *before* the release job
publishes, so the asset it would fetch does not exist. `tests/e2e/test_install.py` covers
what `install.sh` produces (the archive, its checksum, the linked layout) and says in its
docstring which half it leaves.

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
| `terminal` | a real pseudo-terminal: `.stream` to write to, `.read()` for what came out |
| `two_terminals` | a pair, for the output frame, which needs both streams to be terminals |
| `clean_environment` | autouse; the isolation described above |

There is no adapter fixture. `adapters.echo` ships and is in `ADAPTERS`, so a test that
needs an agent step names it the way a user would, and `components.agent_spec` already
defaults to it.

And in `tests/e2e/conftest.py`:

| Fixture | Is |
| ------- | -- |
| `binary` | the built binary, or the skip that says how to get one |
| `atf` | a runner spawning it, same arguments as `atf_process` |
| `console` | the same, on a terminal it controls, for the prompts |
| `expected_version` | what `$ATF_EXPECTED_VERSION` promises, so the stamp can be checked |

`support/components.py` writes components: `write_tool`, `write_agent`, `write_flow`, and
the small scripts to give them (`prints`, `fails`, `echoes_env`, `rendezvous`, `sleeps`).
Prefer adding a script builder there over writing shell inline in a test.
`support/outcome.py` holds `Outcome` and `Runner`, shared so a test reads the same whether
it drives the checkout or the artefact.

## Determinism

Nothing in the suite may depend on the machine it runs on. Concretely:

- Never assert on wall-clock timing. Assert `ms >= 0`, not `ms < 50`.
- Never sleep to order two things. `rendezvous` makes two steps wait for each other, and
  fails loudly with its own deadline instead of hanging the suite.
- Give any tool that could hang a `timeout_seconds`, and keep it well under the engine's
  60s default.
- Assert on the shortened display path (`./tools/greet`), never on an absolute one.
