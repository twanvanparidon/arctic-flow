"""A real terminal to write into.

Three modules branch on `isatty()`: colour, the output frame and the progress display. An
object with an `isatty` method returning True would prove only that the branch exists. A
pseudo-terminal is an actual terminal as far as the kernel and the interpreter are
concerned, so the branch is taken for the reason it is taken in a real session.

The line discipline turns "\\n" into "\\r\\n" on the way through. `read()` undoes that, so a
test compares against the string the code wrote.
"""

from __future__ import annotations

import os
from types import TracebackType
from typing import TextIO


class Terminal:
    """A pty pair: `stream` is the terminal, `read()` is what came out of it."""

    def __init__(self) -> None:
        self._controller, follower = os.openpty()
        # Non-blocking, so draining an empty terminal returns rather than waiting for
        # output that is never coming.
        os.set_blocking(self._controller, False)
        self.stream: TextIO = os.fdopen(follower, "w", buffering=1, encoding="utf-8")
        self._closed = False

    def read(self) -> str:
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(self._controller, 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stream.close()
        os.close(self._controller)

    def __enter__(self) -> Terminal:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
