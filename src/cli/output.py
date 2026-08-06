"""Framing a flow's output so it reads apart from the progress above it.

The frame goes to **stderr**; only the flow's own bytes go to stdout. That is what keeps

    atf run sign_release > release.sig

producing a signature rather than a signature with a rule drawn around it, and it explains
the shape: rules above and below, but no left edge, because a `|` on those lines would mean
editing output that is not ours to edit.

Nothing is drawn unless **both** streams are terminals. Piped, there is nothing on screen
to frame, and a header captured into the file the user is building is worse than none.
"""

from __future__ import annotations

import shutil
import sys
from typing import TextIO

from cli import colour

RULE = "─"

# Wide enough for the label to read as a heading, narrow enough that a 200-column terminal
# does not get a 200-column line drawn across it.
MAX_WIDTH = 78
MIN_ARM = 3


def _width() -> int:
    return min(shutil.get_terminal_size((80, 24)).columns - 1, MAX_WIDTH)


def _rule() -> str:
    return RULE * _width()


def _labelled_rule(label: str) -> tuple[str, str, str]:
    """A rule with the label sitting on it, the way test runners mark their sections.

    Split into three so the caller can paint the arms without the label: the rule is
    scenery and should recede, the word on it is the part being read.
    """
    text = f" {label} "
    left = max(MIN_ARM, (_width() - len(text)) // 2)
    right = max(MIN_ARM, _width() - left - len(text))
    return RULE * left, text, RULE * right


def flow_output(
    text: str,
    label: str = "",
    frame: bool = True,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    """Write a flow's output, framed when there is a person watching both streams."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    # Both streams, because a frame drawn around output that went somewhere else is worse
    # than none. A stricter test than the colour module's, so the decision is passed in.
    drawing = frame and bool(text.strip()) and stdout.isatty() and stderr.isatty()
    paint = colour.painter(on=drawing and colour.enabled(stderr))

    def dim(part: str) -> str:
        return paint(part, "dim")

    if drawing:
        left, heading, right = _labelled_rule(f"output · {label}" if label else "output")
        # Flushed before stdout is touched. Two streams pointed at one terminal only appear
        # in the order they were written if each write is pushed out before the next begins.
        _write(stderr, "\n" + dim(left) + heading + dim(right) + "\n")

    if text:
        # Exactly one trailing newline: enough that a redirect produces a well-formed file,
        # and never a second one, which would show up as a blank line inside the frame.
        _write(stdout, text if text.endswith("\n") else text + "\n")

    if drawing:
        _write(stderr, dim(_rule()) + "\n")
    elif frame and not text.strip() and stderr.isatty():
        # A flow that resolved to nothing looks identical to a flow that never ran. Say so
        # rather than leaving a blank terminal to be read either way.
        _write(stderr, dim("  (no output)") + "\n")


def _write(stream: TextIO, text: str) -> None:
    stream.write(text)
    stream.flush()
