"""Whether to use colour, and which colours to use.

One decision in one place. Python 3.14's argparse colours its own help and the compiled
binary embeds 3.13, so the CLI arrived coloured from a checkout and monochrome from a
release. How you installed something should not change how it looks.

Five entries, deliberately, so a sixth cannot be added by accident:

  bold    structure: headings, the wordmark
  dim     secondary: timings, rules, metavars, anything you read second
  cyan    the brand: the flake, the program's own name
  green   good: a finished step, an available option
  red     bad: a failed step

Checked in order, because someone who set a variable has said what they want more clearly
than a terminal check can:

  NO_COLOR      set to anything → never colour (https://no-color.org)
  FORCE_COLOR   set to anything → always colour, even into a pipe
  otherwise     colour only when the stream is a terminal
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

RESET = "\033[0m"

STYLES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "cyan": "36",
}


def enabled(stream: TextIO | None = None) -> bool:
    """Whether this stream should be coloured."""
    stream = stream if stream is not None else sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        # A closed or exotic stream is not a terminal for our purposes.
        return False


class Painter:
    """Paints text, or does not, having decided once.

    Decided once rather than per string: a banner re-checking the terminal for every line
    could disagree with itself halfway through. `.on` lets a caller skip building output
    nobody will see.
    """

    __slots__ = ("on",)

    def __init__(self, on: bool) -> None:
        self.on = on

    def __call__(self, text: str, *styles: str) -> str:
        if not self.on or not styles or not text:
            return text
        codes = ";".join(STYLES[style] for style in styles)
        return f"\033[{codes}m{text}{RESET}"


def painter(stream: TextIO | None = None, on: bool | None = None) -> Painter:
    """A Painter for this stream, or one forced on or off.

    `on` is for callers that have already decided. The output frame only draws when both
    stdout and stderr are terminals, which is a stricter test than this module's.
    """
    return Painter(enabled(stream) if on is None else on)
