"""Name, tagline and banner.

Two things shape the banner, both about it not breaking somewhere:

  ASCII only. A `❄` is the obvious glyph and the wrong choice: terminals giving it emoji
  presentation render it double-width, skewing everything aligned after it, and a
  non-UTF-8 stdout turns a decoration into a UnicodeEncodeError.

  Suppressed when stdout is not a terminal. `atf --version` gets parsed by scripts and
  read by CI logs, and ASCII art belongs in neither.
"""

from __future__ import annotations

import sys
from typing import TextIO

from cli import colour

# The git tag is the version. This is the placeholder a checkout carries, and what an
# unstamped build reports: packaging/stamp_version.py overwrites this line before the build
# installs the project, and pyproject.toml reads the result through tool.setuptools.dynamic.
#
# Deliberately not a plausible release number. A missed stamp is otherwise invisible until
# someone installs 0.2.0 and finds it reports something older.
__version__ = "0.0.0.dev0"

NAME = "Arctic Flow"
COMMAND = "atf"
TAGLINE = "push-based agentic workflows"

WORDMARK = "A R C T I C   F L O W"

# Column 0 is the flake, column 1 is the text beside it. Kept as pairs so the two stay
# aligned when either changes.
_FLAKE = (
    r"   *  .  *   ",
    r"    \ | /    ",
    r"  .-- * --.  ",
    r"    / | \    ",
    r"   *  .  *   ",
)


def banner(version: str, stream: TextIO | None = None) -> str:
    """The banner, or an empty string for anything that is not a terminal."""
    stream = stream or sys.stdout
    if not stream.isatty():
        return ""

    paint = colour.painter(stream)

    beside = (
        "",
        paint(WORDMARK, "bold"),
        paint(f"{COMMAND} {version}", "dim"),
        paint(TAGLINE, "dim"),
        "",
    )
    lines = [paint(flake, "cyan") + text for flake, text in zip(_FLAKE, beside, strict=True)]
    return "\n".join(lines).rstrip() + "\n\n"


def version_line(version: str, stream: TextIO | None = None) -> str:
    """`atf 0.1.0` on a pipe; the banner when someone is watching."""
    stream = stream or sys.stdout
    art = banner(version, stream)
    return art if art else f"{COMMAND} {version}\n"
