"""Write the release version into the source, before anything reads it.

The git tag is the version. A checkout cannot know it, so CI passes it in and this puts it
where both build paths look: `pip install` resolves pyproject.toml's dynamic version through
`cli.branding.__version__`, and packaging/atf.spec freezes the installed distribution rather
than the checkout.

Ordering is the whole contract. Run this before `pip install`, or it writes a source tree
nothing reads again: the build still succeeds and the binary still ships the placeholder.
Dockerfile.build's smoke test compares the binary against the tag rather than trusting the
order, and packaging/release.sh checks again before publishing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# The tag shapes .github/workflows/ci.yml starts a run for. Anything else is refused rather
# than stamped: no pipeline would have built the release such a tag names.
TAG = re.compile(r"v(\d+\.\d+\.\d+(?:-rc\.\d+)?)")

# The assignment, not the value. What a checkout carries is a placeholder and none of this
# script's business. Exactly one match is required, so reformatting that line fails the build
# here instead of leaving the placeholder in a release nobody looks at until it is installed.
ASSIGNMENT = re.compile(r'^__version__ = ".*"$', re.MULTILINE)

BRANDING = Path(__file__).resolve().parent.parent / "src" / "cli" / "branding.py"


def stamp(source: str, tag: str) -> str:
    """Return `source` with its `__version__` set to the version `tag` names."""
    named = TAG.fullmatch(tag)
    if named is None:
        raise ValueError(f"'{tag}' is not a release tag: expected vX.Y.Z or vX.Y.Z-rc.N")

    stamped, count = ASSIGNMENT.subn(f'__version__ = "{named.group(1)}"', source)
    if count != 1:
        raise ValueError(f"expected one __version__ assignment, found {count}")
    return stamped


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: stamp_version.py <tag>", file=sys.stderr)
        return 2

    try:
        BRANDING.write_text(stamp(BRANDING.read_text(), argv[0]))
    except ValueError as error:
        print(f"stamp_version.py: {error}", file=sys.stderr)
        return 1

    print(f"  stamped {argv[0]} onto {BRANDING.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
