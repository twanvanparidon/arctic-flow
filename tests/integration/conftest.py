"""Fixtures for the integration suite.

Two of these decide what the suite is. `atf` calls the CLI's own `main` in process, which
crosses every layer from argv to the flow's output and is fast enough to use everywhere.
`atf_process` spawns the real entry point, and is for the claims that only a real process
can make: which file descriptor a byte came out of, what `> file` contains, and what the
exit status was.

`fake_claude` is autouse and deliberately so. The developer machine this was written on has
the real `claude` on `PATH`, so without it a stray agent step would reach a real model and
cost real money. Every integration test runs with the fake in front of it; a test that wants
the runtime to be missing points `PATH` somewhere else.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import support
from cli.app import main
from engine.executor import SKIPPED_RESULT
from support import components
from support.outcome import Outcome, Runner

REPOSITORY = Path(__file__).resolve().parents[2]
ENTRY_POINT = REPOSITORY / "src" / "main.py"


@pytest.fixture(autouse=True)
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a `claude` on PATH that speaks the CLI's protocol without calling a model."""
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    installed = binaries / "claude"
    shutil.copy(Path(support.__file__).parent / "fake_claude.py", installed)
    installed.chmod(0o755)
    # Prepended rather than replacing PATH: the tools a flow runs still need sh, jq and awk.
    monkeypatch.setenv("PATH", f"{binaries}{os.pathsep}{os.environ['PATH']}")
    return installed


@pytest.fixture
def atf(capsys: pytest.CaptureFixture[str]) -> Runner:
    """Run a command through the CLI's own entry point, in this process.

    Everything from argument parsing to the flow's output happens for real. Only the
    process boundary is missing, which is what `atf_process` is for.
    """

    def run(*argv: str) -> Outcome:
        capsys.readouterr()
        try:
            code = main(list(argv))
        except SystemExit as exit_request:  # --version and argparse's own failures
            code = int(exit_request.code or 0)
        captured = capsys.readouterr()
        return Outcome(code=code, out=captured.out, err=captured.err)

    return run


@pytest.fixture
def atf_process() -> Runner:
    """Run the real entry point as a real process.

    `python3 src/main.py` rather than the installed `atf`, so the suite tests the checkout
    it was run from. The two are the same CLI; `main.py` only puts `src/` on the path.
    """

    def run(
        *argv: str,
        stdin: str = "",
        env: dict[str, str] | None = None,
        stdout: int | object = subprocess.PIPE,
        timeout: float = 60,
    ) -> Outcome:
        completed = subprocess.run(
            [sys.executable, str(ENTRY_POINT), *argv],
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
def examples() -> Path:
    """The shipped sample projects, exactly as a user gets them."""
    return REPOSITORY / "examples"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A workspace holding a few real components, for flows a test writes itself.

    Deliberately a small toolbox rather than one tool per test: a flow reads better when
    its steps are named after what they do, and these are the verbs the shapes need.
    """
    root = tmp_path / "project"
    root.mkdir()

    components.write_tool(root, "echo_input", script=components.ECHO_STDIN)
    components.write_tool(
        root,
        "shout",
        script=components.python("sys.stdout.write(payload['text'].upper())\n"),
    )
    components.write_tool(
        root,
        "fail_with",
        script=components.python(
            "sys.stderr.write(payload.get('message', 'no reason given'))\n"
            "sys.exit(payload['code'])\n"
        ),
        exit_codes={"3": "not found", "4": "not permitted"},
    )
    components.write_tool(
        root,
        "word_limit",
        script=components.python(
            "words = len(payload['text'].split())\n"
            "if words > payload['max_words']:\n"
            "    sys.stderr.write(f\"{words} words, {payload['max_words']} allowed\")\n"
            "    sys.exit(1)\n"
            "sys.stdout.write(f'{words} words')\n"
        ),
        exit_codes={"1": "over the limit"},
    )
    components.write_tool(
        root,
        "reveal",
        script=components.python("sys.stdout.write(os.environ.get(payload['name'], ''))\n"),
    )
    # The two verbs a loop needs: something whose result differs each pass, and something
    # that hands it on so the switch has a value to leave on.
    components.write_tool(root, "grow", script=components.grows("a", SKIPPED_RESULT["text"]))
    components.write_tool(root, "say", script=components.echoes_input("text"))

    components.write_agent(root, "writer", adapter="claude_code", model="sonnet")
    components.write_agent(
        root,
        "classifier",
        adapter="claude_code",
        model="sonnet",
        effort="low",
        output_schema={
            "type": "object",
            "properties": {"verdict": {"enum": ["risky", "clean"]}},
            "required": ["verdict"],
        },
    )
    return root


def missing(*binaries: str) -> list[str]:
    return [name for name in binaries if shutil.which(name) is None]


def requires(*binaries: str) -> None:
    """Skip when a component's declared requirement is not installed.

    The shipped tools name what they need in their own spec.json. A machine without `jq` is
    an environment that cannot run them, which is not a defect in this repository. The skip
    names the binary, and `-ra` prints it, so it is never silent.
    """
    absent = missing(*binaries)
    if absent:
        pytest.skip(f"needs {', '.join(absent)} on PATH")


@pytest.fixture
def unset_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """A PATH with nothing on it, for the case where the runtime is not installed."""
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))
    yield
