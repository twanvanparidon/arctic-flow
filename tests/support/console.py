"""A command on a terminal it controls, for the two places that prompt.

`terminal.Terminal` is a pty to write *into*, which is enough for code branching on
`isatty()`. It is not enough here. `getpass` does not read stdin: it opens `/dev/tty`, and
that only resolves for a process with a *controlling* terminal. A child that merely
inherits a pty file descriptor has none, so the open fails and the prompt is never reached.

Acquiring one is two steps and both are needed. `start_new_session` makes the child a
session leader, since a process that already belongs to a session cannot take another
terminal. Then `TIOCSCTTY` claims the pty, which has to happen in the child, after its file
descriptors are in place and before the exec. `preexec_fn` is the only hook between those
two points.

All three streams share the one terminal. That is not a simplification: the prompt goes to
`/dev/tty` whatever stdout and stderr are doing, so separating them would show less rather
than more.

Every wait has its own deadline and fails saying what it did see, so a command that stops
prompting fails the test instead of hanging the suite.
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import subprocess
import termios
import time
from pathlib import Path
from types import TracebackType

# Ctrl-D. In canonical mode this is what ends a read, which is how a person declines a
# prompt. getpass turns off echo and leaves the line discipline alone, so it still works.
END_OF_TRANSMISSION = "\x04"


def _take_the_terminal() -> None:
    """Claim the pty on fd 0 as this process's controlling terminal."""
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Console:
    """A running command, and everything it has written to its terminal so far."""

    def __init__(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._timeout = timeout
        self._seen = ""
        self._ended = False
        self._controller, follower = pty.openpty()
        self._process = subprocess.Popen(
            argv,
            stdin=follower,
            stdout=follower,
            stderr=follower,
            env=None if env is None else {**os.environ, **env},
            cwd=None if cwd is None else str(cwd),
            start_new_session=True,
            preexec_fn=_take_the_terminal,  # noqa: PLW1509 - see the module docstring
        )
        # Our copy goes, so a read returns end-of-file once the child's copies close too.
        os.close(follower)

    @property
    def output(self) -> str:
        """Everything read so far, with the line discipline's carriage returns undone."""
        return self._seen

    def expect(self, text: str, timeout: float | None = None) -> str:
        deadline = time.monotonic() + (self._timeout if timeout is None else timeout)
        while text not in self._seen:
            if self._ended:
                raise AssertionError(f"it ended without writing {text!r}. It wrote:\n{self._seen}")
            if time.monotonic() > deadline:
                raise AssertionError(f"waited for {text!r}. It wrote:\n{self._seen}")
            self._pump()
        return self._seen

    def send(self, line: str) -> None:
        """Type a line, as a person would: the newline is the answer."""
        os.write(self._controller, (line + "\n").encode())

    def decline(self) -> None:
        """Answer a prompt with Ctrl-D instead of a value."""
        os.write(self._controller, END_OF_TRANSMISSION.encode())

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + (self._timeout if timeout is None else timeout)
        while not self._ended and time.monotonic() < deadline:
            self._pump()
        return self._process.wait(timeout=max(1.0, deadline - time.monotonic()))

    def _pump(self) -> None:
        ready, _, _ = select.select([self._controller], [], [], 0.05)
        if not ready:
            return
        try:
            chunk = os.read(self._controller, 65536)
        except OSError:  # the far end closed, which on a pty is an error rather than b""
            chunk = b""
        if not chunk:
            self._ended = True
            return
        self._seen += chunk.decode("utf-8", "replace").replace("\r\n", "\n")

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
            self._process.wait(timeout=10)
        os.close(self._controller)

    def __enter__(self) -> Console:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
