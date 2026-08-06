"""Fail the build if the environment does not satisfy what pyproject.toml declares.

The build installs from a pinned lock file and pyproject.toml declares ranges. Two lists
that have to agree, which is the arrangement that drifts: add a dependency to pyproject,
forget the lock, and the binary ships without it. Nothing fails until someone runs the
command that needs it.

Checking the declared requirements against what is importable fails the build instead.

    python packaging/verify_deps.py
"""

from __future__ import annotations

import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.requirements import Requirement


def main() -> int:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"].get("dependencies", [])

    problems: list[str] = []
    for raw in declared:
        requirement = Requirement(raw)
        try:
            installed = version(requirement.name)
        except PackageNotFoundError:
            problems.append(f"{requirement.name}: declared in pyproject.toml, not installed")
            continue
        if requirement.specifier and installed not in requirement.specifier:
            problems.append(
                f"{requirement.name}: pyproject wants {requirement.specifier}, "
                f"the lock installed {installed}"
            )
        else:
            print(
                f"  ok  {requirement.name} {installed} satisfies {requirement.specifier or 'any'}"
            )

    if problems:
        print("\npyproject.toml and packaging/requirements-lock.txt disagree:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nRegenerate the lock:\n"
            "  docker run --rm atf-build pip freeze "
            "| grep -viE '^(pip|setuptools|wheel)==' > packaging/requirements-lock.txt",
            file=sys.stderr,
        )
        return 1

    print(f"\n{len(declared)} declared dependencies, all satisfied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
