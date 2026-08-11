# Contributing to Arctic Flow

```
   *  .  *
    \ | /       A R C T I C   F L O W
  .-- * --.     atf: push-based agentic workflows
    / | \
   *  .  *
```

This file is the process: how to run the checkout, what has to pass before you push, and how
a release is cut.

**The design lives in [docs/design/](docs/design/README.md)**: the idea, the execution model, the
layering and its four invariants, and what is deliberately missing. Anything you are about to
change was probably decided there.

Adding a tool, an agent, a pack or an adapter is documented for users rather than
contributors, so it sits in [docs/components.md](docs/components.md) with the rest of
[the documentation](docs/README.md).

## Running it

No install needed:

```sh
python3 src/main.py --help
python3 src/main.py --workspace examples/sign-release run sign_release --input path=release-notes.md
```

`src/main.py` puts `src/` on the import path and hands over to `cli/`. Installed
(`pip install .`) or frozen, the same CLI is reached as `atf`; the shim exists so a checkout
runs without either.

Start with the two examples. `examples/sign-release` is tool-only and demonstrates the vault:
no credentials, no network, deterministic. `examples/file-review` uses agents, so it costs
money and needs the `claude` CLI authenticated. [examples/README.md](examples/README.md) has
all five.

### A virtual environment

Only the tools need one. The engine runs on its three runtime dependencies, which a system
Python often already has. `pytest` and `ruff` are neither runtime dependencies nor usually
installed, so the gate has nowhere to get them.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test,lint]"
```

`-e` puts `atf` on your PATH pointing at the checkout, so every `atf …` in the documentation
works without the `python3 src/main.py` prefix.

On Debian and Ubuntu this fails until `sudo apt install python3-venv python3-pip`: the distro
ships the standard library without `ensurepip`. Add `--system-site-packages` to reuse what apt
already installed, which saves building a wheel from source on a new Python.

It is also how your versions come to match CI's. A distro `jsonschema` can be several minor
versions behind what `pip` resolves, and the engine's validation messages come out of that
library.

## Before you push

The pipeline runs these, so run them first:

```sh
ruff check src packaging tests
ruff format --check src packaging tests
shellcheck -x $(find . -name '*.sh' -not -path './dist/*' -not -path './build/*' -not -path './var/*')
pytest
python3 packaging/sync_docs.py --check

# the engine validates flows better than any generic linter. A bare `lint` checks every
# flow in the workspace and exits non-zero if any of them failed.
for project in examples/*/; do
  python3 src/main.py --workspace "$project" lint
done
```

`ruff` settings live in `pyproject.toml`. Line length is 100. The default 88 wanted 229 lines
of churn against code written to a wider measure.

`ruff`, `pytest` and `coverage` come from the two extras, installed together by the
`pip install -e` above. None is a runtime dependency, so none of them ships.

The pipeline runs the same commands, plus `coverage` and `--junitxml`. It puts the counts and
the coverage table on the run page, and uploads the JUnit XML and an HTML coverage report as
artifacts. Coverage never fails the build.

### Documentation

`docs/` is the single source of truth for user-facing prose, and **the user-facing pages are
flat**: one document per topic, and a section is a heading rather than a new file.
`docs/design/` is the one directory. It is written for a contributor rather than a user, so
it goes deeper: what the rule is, why, and what happens to someone who does the obvious
thing instead.

The `help` skill's references under `.claude-plugins/` are generated copies of the six
user-facing pages; `packaging/sync_docs.py` writes them, and a file in `references/` is
never edited directly.

`--check` is what CI runs. It fails on a stale reference, a relative link that does not
resolve, a page unreachable from `docs/README.md`, and a page over 280 lines. That last one
is a real limit rather than a suggestion: prose grows a line at a time and nobody notices.

Prose follows [.claude/rules/WRITINGSTYLE.md](.claude/rules/WRITINGSTYLE.md). No em dashes,
short sentences, plain words around exact technical terms.

A docs page states what the engine does and what it refuses, and guides the reader to the
command that answers their question. The reasoning goes in `docs/design/`, and no rule is
written on two pages.

## Tests

`tests/unit` and `tests/integration` are written and run in under half a minute. `tests/e2e`
drives the built binary and takes about as long again, but only once there is one to drive:
without a build it skips, so a plain `pytest` on a checkout runs the first two. How the suite
is built, and what belongs in which of the three, is in
[.claude/rules/TESTING.md](.claude/rules/TESTING.md).

**A unit test uses no doubles.** A tool test writes a real tool directory and the engine
spawns a real process; a vault test encrypts with real scrypt and AES-GCM; a test about
`isatty()` opens a real pseudo-terminal. The failures worth catching there are the ones a
substitute cannot have: a lost executable bit, a process that outlives its timeout, a secret
in an environment it was not granted.

**An integration test may use a fake, a stub or a mock, and should prefer them in that
order.** A fake is a working implementation, so it can still fail for a real reason. A stub
only answers. A mock asserts on how the code went about something rather than on what it
decided, so it is the last resort.

The one in the tree today is a fake: `tests/support/fake_claude.py` speaks the Claude Code
CLI's protocol, so the adapter spawns a real process without an account or a network.
`PATH=/usr/bin:/bin pytest` passes, which is how you can tell nothing reaches the real one.

**An end-to-end test is about the artefact, not this source.** It belongs there when it would
pass against `src/` and still ship something broken: a frozen process spawning `openssl`, the
binary re-invoking itself to serve an agent's tools, `atf` reached through the symlink
`install.sh` leaves, a password prompt that needs a controlling terminal. Its agent steps use
`adapters.echo`, which ships and answers from the request, because a registry frozen into a
binary cannot be added to from outside.

## Building

The build runs in a container, which is also what CI does. The binary embeds an interpreter
and pinned wheels, so the build environment is part of the artifact:

```sh
docker build -f packaging/Dockerfile.build -t atf-build .
docker create --name atf-out atf-build
docker cp atf-out:/out/atf ./dist/ && docker rm atf-out
```

`docker build` fails rather than producing a binary that cannot see its own built-in
components. That check has already caught a real regression.

Once `dist/atf/atf` exists, the end-to-end suite finds it:

```sh
pytest tests/e2e
```

The pipeline runs it on a release tag, between the build and the publish, so a binary that
fails it is never released.

If you change `pyproject.toml`'s dependencies, regenerate the lock:

```sh
docker run --rm atf-build pip freeze | grep -viE '^(pip|setuptools|wheel)==' > packaging/requirements-lock.txt
```

`packaging/verify_deps.py` fails the build when the lock and `pyproject.toml` disagree, so
forgetting this is loud rather than silent.

PyInstaller cannot cross-compile: this produces a Linux x86-64 binary. macOS and Windows need
runners on those platforms.

## Releasing

Tag it. `.github/workflows/ci.yml` runs lint, test and build on every pull request; a version
tag runs them again and adds the release job, which publishes a GitHub Release.

```sh
git tag v0.2.0 && git push origin v0.2.0            # what install.sh installs
git tag v0.2.0-rc.1 && git push origin v0.2.0-rc.1  # published, but not by default
```

The tag is the version, and nothing to edit beforehand. `packaging/stamp_version.py` writes
it into `src/cli/branding.py` inside the build, before `pip install` reads it through
pyproject.toml's dynamic version. A checkout carries `0.0.0.dev0`, which is what an untagged
build honestly reports.

The hyphen is what makes it a prerelease. GitHub leaves those out of `/releases/latest`, the
redirect `install.sh` follows, so a candidate is installed only when named:
`install.sh --version v0.2.0-rc.1`.

Those two shapes are the only tags the workflow answers to, and the only two the stamper
accepts. Anything else starts no run at all.

The wheel spells it differently: setuptools normalises to PEP 440, so `v0.2.0-rc.1` ships
`arctic_flow-0.2.0rc1-*.whl` beside the tarball's `atf-0.2.0-rc.1-linux-x86_64.tar.gz`.

`packaging/release.sh` builds the tarball, a `sha256` that verifies with `sha256sum -c`, and a
wheel. The tarball is reproducible (`--sort`, `--mtime`, fixed ownership), so the checksum
means something across rebuilds.

Uploading needs no credential of its own. `gh release create` runs with the workflow's
`GITHUB_TOKEN`, which the release job grants `contents: write`.

`packaging/install.sh` is the other end of that. It reads the tag from the redirect off
`/releases/latest`, checks the `sha256` before unpacking anything, and installs the whole
directory with a link to the binary. Users fetch it raw from `main`, so a fix to it reaches
them without a release, and it names the asset the way `release.sh` writes it. Renaming an
artefact means changing both.

The plugin under `.claude-plugins/` has its own version, in `plugin.json`, and the
marketplace reads `main`. So a skill fix reaches users without a release, and a fix that did
not bump that number is a fix nobody can reach.

## House style

The code is commented more heavily than most, and deliberately: comments explain **why**,
especially where the obvious approach is wrong. Several exist because the obvious approach
was tried and failed: `$(...)` stripping the trailing newline off a payload being signed,
`class a,b c` in Mermaid meaning two node ids rather than two classes, `import build` being
satisfied by an empty `build/` directory. If you fix something subtle, leave the reason
behind.

Prefer failing loudly over doing something plausible. The engine refuses a flow that reads
from a step it does not depend on, a switch value matching no case, an agent granted a tool
that writes without saying it is unattended, and a release whose tag disagrees with its
version. Each of those could have been a default, a guess, or a silent no-op instead.

## Licence

Arctic Flow is GPLv3 or later. By opening a pull request you agree that your contribution is
licensed under those terms, and that you have the right to submit it.

There is no CLA and nothing to sign. Copyright stays with whoever wrote the code.
