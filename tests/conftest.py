"""Fixtures every test may use.

Two of them exist to make the suite hermetic rather than convenient. `clean_environment`
removes the variables the engine reads from the ambient process, because a developer with
`NO_COLOR` or `ATF_PATH` set would otherwise get different results from CI. `paths` pins
both the workspace and the home directory into `tmp_path`, because `~/.arctic` is a real
search root and someone's own tools must not shadow a test's.

The engine's own built-in root stays in the search order. It ships with the code, so a test
that resolves `read_file` is resolving the same thing a user does.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from engine.executor import VARIABLE_PREFIX
from paths.resolver import Paths
from support.terminal import Terminal

# Everything the engine or the CLI reads straight out of the process environment.
AMBIENT = (
    "ATF_PATH",
    "ATF_VAULT_PASSWORD",
    "ATF_VAULT_PASSWORD_FILE",
    "NO_COLOR",
    "FORCE_COLOR",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    for name in AMBIENT:
        monkeypatch.delenv(name, raising=False)
    # ATF_VAR_* supplies a flow input, and its names are open-ended rather than a fixed
    # list. A developer with one exported would otherwise hand an input to a test that
    # meant to run without it, and the integration suite reads the real environment.
    for name in [name for name in os.environ if name.startswith(VARIABLE_PREFIX)]:
        monkeypatch.delenv(name)
    # shutil.get_terminal_size() consults COLUMNS before it asks the kernel, so pinning it
    # makes every width the CLI computes the same on a laptop and on a runner.
    monkeypatch.setenv("COLUMNS", "80")
    # `~/.arctic` is a search root and `Paths` defaults to `Path.home()`, so anything
    # constructing one without an explicit home would otherwise find the developer's own
    # components. This was caught by a test that passed everywhere except on a machine with
    # a flow of the same name installed at home.
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """The project root: the top search layer, and where components run."""
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """The temporary home directory, and what `$HOME` points at for every test."""
    path = tmp_path / "home"
    path.mkdir(exist_ok=True)
    return path


@pytest.fixture
def paths(workspace: Path, home: Path) -> Paths:
    # env={} rather than the real environment: ATF_PATH is the highest-precedence root and
    # a test must not inherit one.
    return Paths(workspace, env={}, home=home)


@pytest.fixture
def terminal() -> Iterator[Terminal]:
    with Terminal() as term:
        yield term


@pytest.fixture
def two_terminals() -> Iterator[tuple[Terminal, Terminal]]:
    """For the output frame, which draws only when stdout *and* stderr are terminals."""
    with Terminal() as out, Terminal() as err:
        yield out, err
