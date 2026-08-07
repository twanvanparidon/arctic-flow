"""What a command did, and the shape of anything that runs one.

Shared rather than defined twice because the integration and end-to-end suites ask the same
question of two different things: the checkout in one case, the built binary in the other. A
test that moves between them should not have to change what it asserts on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    """What a command did: its exit status and each of its two streams."""

    code: int
    out: str
    err: str


# argv without the program name, in; an Outcome out.
Runner = Callable[..., Outcome]
