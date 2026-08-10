"""A real git repository to point a flow at.

Real, not a stand-in, for the reason `tests/unit` uses real subprocesses everywhere else:
the failures the git pack can have are the ones only git has. A repository whose root is
above the workspace, an index that is empty, a branch name already taken, a commit with no
configured identity. None of those is reachable through a fake.

`environment()` is the other half. A developer's own `~/.gitconfig` can carry
`commit.gpgsign`, a `core.hooksPath`, or an `init.templatedir` that seeds hooks into every
repository made here, and any of those turns a green suite red on one machine. Every
helper takes the environment this builds, and so does the runner driving `atf`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

NAME = "Test Person"
EMAIL = "test@example.com"


def environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """An environment where git reads no configuration but the repository's own.

    `/dev/null` rather than a written empty file: git accepts it as "there is no such
    config", and there is nothing to clean up afterwards.
    """
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        **(extra or {}),
    }


def git(repository: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """One git command, failing loudly. Returns stdout with the trailing newline gone."""
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        env=env or environment(),
        check=True,
    )
    return completed.stdout.rstrip("\n")


def initialise(path: Path, *, identity: bool = True) -> Path:
    """A repository at `path`, on `main`, with no commits.

    `-b main` because the default branch name is a per-machine setting, and a test that
    asserts on a branch would otherwise pass or fail by whose laptop it ran on.

    `identity` false leaves git with no name and email, which is the state a build machine
    is in and the one `git/commit` exits 7 for.
    """
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    if identity:
        git(path, "config", "user.name", NAME)
        git(path, "config", "user.email", EMAIL)
    return path


def commit(repository: Path, message: str, **files: str) -> str:
    """Write files, stage them, commit. Returns the short sha.

    Named files rather than "everything", so a test's repository holds what the test said
    it holds and nothing a previous step left behind.
    """
    for name, content in files.items():
        target = repository / name.replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        git(repository, "add", "--", str(target.relative_to(repository)))
    git(repository, "commit", "-q", "-m", message)
    return git(repository, "rev-parse", "--short", "HEAD")
