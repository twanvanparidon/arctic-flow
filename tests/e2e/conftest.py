"""Fixtures for the end-to-end suite, which drives the built binary.

Everything below the CLI has already been tested twice over. What is left is the artefact:
a PyInstaller bundle carrying its own interpreter, its own OpenSSL and the built-in tools as
real files beside it. Two failure classes live only there and no other suite can see them.
`child_environment` undoes PyInstaller's `LD_LIBRARY_PATH` rewrite, without which a spawned
`openssl` loads the bundle's copy and fails; `paths.builtin_root()` resolves against its own
module, which is a different place inside a bundle. Both are commented in the source as
things that were tried and went wrong.

So the runner here spawns `dist/atf/atf` rather than `python3 src/main.py`. It takes the
same arguments as the integration suite's `atf_process` and returns the same `Outcome`, so a
test reads the same way at both levels.

**Never invoke a bare `atf`.** The pipeline installs the project into the job that runs
this, so there is an `atf` on `PATH`, and it is the checkout rather than the artefact. Every
command goes through the `atf` fixture, which knows the difference.

There may be no binary at all: a developer running `pytest` has usually not spent five
minutes on a Docker build, and `testpaths` collects this directory regardless. That is a
skip and not a failure. The skip names how to get one, and `-ra` prints it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import support
from support.console import Console
from support.outcome import Outcome, Runner

REPOSITORY = Path(__file__).resolve().parents[2]

# The one-directory build, as `docker cp` leaves it and as release.sh expects to find it.
DEFAULT_BINARY = REPOSITORY / "dist" / "atf" / "atf"

# Read at import time rather than through a fixture. `clean_environment` is autouse and
# scrubs the environment the engine reads; leaving nothing here that depends on the order
# those two run in is cheaper than remembering why it mattered.
NAMED_BINARY = os.environ.get("ATF_BINARY")

# The tag CI is releasing, so the version the binary reports can be checked against the
# thing that stamped it. Absent when the suite is run against a local build.
EXPECTED_VERSION = os.environ.get("ATF_EXPECTED_VERSION")


def binary_path() -> Path | None:
    candidate = Path(NAMED_BINARY) if NAMED_BINARY else DEFAULT_BINARY
    return candidate.resolve() if os.access(candidate, os.X_OK) else None


@pytest.fixture(scope="session")
def binary() -> Path:
    found = binary_path()
    if found is None:
        pytest.skip(
            "no built binary. Build one with `docker build -f packaging/Dockerfile.build "
            "-t atf-build .` and extract it to dist/, or point $ATF_BINARY at it"
        )
    return found


@pytest.fixture(scope="session")
def expected_version() -> str | None:
    """The version the tag being released promises, without its leading `v`."""
    return EXPECTED_VERSION.lstrip("v") if EXPECTED_VERSION else None


@pytest.fixture(autouse=True)
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a `claude` on PATH that speaks the CLI's protocol without calling a model.

    Autouse for the reason it is autouse in the integration suite: the machine this was
    written on has the real one installed, and two of the shipped examples declare
    `adapter: claude_code`. Here it earns its place twice over, because a frozen process
    spawning a subprocess is one of the things under test.
    """
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    installed = binaries / "claude"
    shutil.copy(Path(support.__file__).parent / "fake_claude.py", installed)
    installed.chmod(0o755)
    # Prepended rather than replacing PATH: the tools a flow runs still need sh, jq and awk.
    monkeypatch.setenv("PATH", f"{binaries}{os.pathsep}{os.environ['PATH']}")
    return installed


@pytest.fixture
def atf(binary: Path) -> Runner:
    """Run the built binary as a real process."""

    def run(
        *argv: str,
        stdin: str = "",
        env: dict[str, str] | None = None,
        stdout: int | object = subprocess.PIPE,
        timeout: float = 120,
    ) -> Outcome:
        completed = subprocess.run(
            [str(binary), *argv],
            input=stdin,
            stdout=stdout,  # type: ignore[arg-type]
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **(env or {})},
            timeout=timeout,
        )
        return Outcome(
            code=completed.returncode,
            out=completed.stdout or "",
            err=completed.stderr or "",
        )

    return run


@pytest.fixture
def console(binary: Path) -> Callable[..., Console]:
    """Run the built binary on a terminal it controls, for the prompts."""

    def start(*argv: str, env: dict[str, str] | None = None, timeout: float = 60) -> Console:
        return Console([str(binary), *argv], env=env, timeout=timeout)

    return start


@pytest.fixture
def examples() -> Path:
    """The shipped sample projects, exactly as a user gets them."""
    return REPOSITORY / "examples"


def missing(*binaries: str) -> list[str]:
    return [name for name in binaries if shutil.which(name) is None]


def requires(*binaries: str) -> None:
    """Skip when a component's declared requirement is not installed.

    A frozen Python does not freeze `jq`, `openssl` or `awk`. The bundle carries an
    interpreter and nothing else, which is exactly what the packaging notes say, so a
    machine without them is an environment and not a defect.
    """
    absent = missing(*binaries)
    if absent:
        pytest.skip(f"needs {', '.join(absent)} on PATH")
