"""Live progress for a running flow.

Everything here writes to **stderr**, so `run sign_release > release.sig` produces a
signature file and not one with a progress log in it.

Two modes, chosen by whether stderr is a terminal:

  terminal        permanent lines as steps start and finish, plus a status line that
                  repaints with a live clock. The clock is the point: an agent step can
                  take ninety seconds, and "working" versus "hung" is all you want to know.
  not a terminal  the same lines, no repainting and no colour, so a CI log reads as text.

The engine does not know any of this exists. It emits events; this decides how they look.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
from typing import Any, TextIO

from cli import colour

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
REPAINT_SECONDS = 0.1


class Progress:
    """Renders flow events. Use as a context manager so the status line is cleared."""

    def __init__(self, stream: TextIO | None = None, enabled: bool = True) -> None:
        self.stream = stream or sys.stderr
        self.enabled = enabled
        # Two separate decisions. Repainting needs a terminal to repaint; colour also
        # answers to NO_COLOR and FORCE_COLOR. Tying them together, as an earlier version
        # did, meant NO_COLOR silently took the live clock away with the colour.
        self.live = enabled and self.stream.isatty()
        self.paint = colour.painter(self.stream)

        self._lock = threading.Lock()
        self._running: dict[str, float] = {}
        self._finished = 0
        self._skipped = 0
        self._cost = 0.0
        self._started = time.monotonic()
        self._frame = 0
        self._painted = 0  # characters currently on the status line
        self._stop = threading.Event()
        self._painter: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------- #

    def __enter__(self) -> Progress:
        if self.live:
            self._painter = threading.Thread(target=self._repaint_loop, daemon=True)
            self._painter.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._painter:
            self._painter.join(timeout=1)
        with self._lock:
            self._clear()

    # -- the engine's entry point ------------------------------------------- #

    def __call__(self, event: dict[str, Any]) -> None:
        """Handle one event. Safe to call from the worker threads."""
        if not self.enabled:
            return

        kind = event.get("kind")
        step = str(event.get("step", "?"))

        if kind == "started":
            with self._lock:
                self._running[step] = time.monotonic()
                self._line("→", step, str(event.get("component", "")), dim=True)
        elif kind == "finished":
            with self._lock:
                self._running.pop(step, None)
                self._finished += 1
                # Accumulated here rather than handed in at the end, so a run that dies
                # halfway can still report what it already spent.
                self._cost += event.get("cost_usd") or 0.0
                self._line("✓", step, self._finished_detail(event), style="green")
        elif kind == "gated":
            # Only a rejection earns a line. A gate that passed is followed straight away
            # by the step's own ✓.
            if not event.get("ok"):
                with self._lock:
                    self._line("⟲", step, self._gate_detail(event), dim=True)
        elif kind == "tool_call":
            # A call the model made inside a turn, not a step. Indented under the step it
            # belongs to, because the step's own line is still to come.
            with self._lock:
                self._line("  ·", step, self._call_detail(event), dim=True)
        elif kind == "skipped":
            with self._lock:
                self._running.pop(step, None)
                self._skipped += 1
                self._line("⤼", step, "skipped, its branch was not taken", dim=True)
        elif kind == "failed":
            with self._lock:
                self._running.pop(step, None)
                self._line("✗", step, str(event.get("error", "")), style="red")

    def summary(self, ok: bool = True) -> None:
        """The closing tagline: what ran, how long it took, what it cost."""
        if not self.enabled:
            return

        elapsed = self._duration(time.monotonic() - self._started)
        if not ok:
            line = f"  failed after {elapsed}{self._money(self._cost)}"
        else:
            steps = f"{self._finished} step{'' if self._finished == 1 else 's'}"
            if self._skipped:
                steps += f" ({self._skipped} skipped)"
            line = f"  {steps} · {elapsed}{self._money(self._cost)}"

        with self._lock:
            self._clear()
            # Blank line first, so the tagline closes the block of steps instead of reading
            # as one more of them. Newline outside the colour span, so the reset does not
            # land at the start of the next line.
            self._write("\n" + self.paint(line, "dim") + "\n")

    @staticmethod
    def _money(cost: float) -> str:
        """Nothing at all when nothing was spent: a tool-only flow has no cost line.

        Four decimals rather than two. These runs land in fractions of a cent often
        enough that $0.00 would be the usual answer, which is worse than no display.
        """
        return f" · ${cost:.4f}" if cost else ""

    # -- rendering ---------------------------------------------------------- #

    def _finished_detail(self, event: dict[str, Any]) -> str:
        """What a completed step says about itself.

        Deliberately not cost. Money per step turns every line into a line item when the
        number that matters is the total, which the closing tagline carries. The per-step
        breakdown is in --trace for when you want it.
        """
        parts = []
        if event.get("ms") is not None:
            parts.append(self._duration(event["ms"] / 1000))
        # Only worth saying where a choice was actually made; on a linear step the
        # next step is already obvious from the flow.
        if event.get("is_switch") and event.get("pushed_to"):
            parts.append("→ " + ", ".join(event["pushed_to"]))
        return "  ".join(parts)

    @staticmethod
    def _gate_detail(event: dict[str, Any]) -> str:
        """A rejected attempt. The attempt number is the part worth watching: it says
        whether the step is converging or about to run out of turns."""
        attempt, allowed = event.get("attempt"), event.get("of")
        last = attempt == allowed
        return (
            f"{event.get('tool')} rejected attempt {attempt}/{allowed}"
            f"{', no attempts left' if last else ', trying again'}"
        )

    @staticmethod
    def _call_detail(event: dict[str, Any]) -> str:
        """One in-turn tool call. Whether it failed is the part worth seeing: the model is
        told and carries on, so a run that looks slow is often one retrying a bad call."""
        outcome = "ok" if event.get("ok") else "failed"
        return f"{event.get('tool')} {outcome} ({event.get('ms', 0)}ms)"

    @staticmethod
    def _duration(seconds: float) -> str:
        if seconds < 1:
            return f"{seconds * 1000:.0f}ms"
        if seconds < 60:
            return f"{seconds:.1f}s"
        return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    def _clear(self) -> None:
        """Erase the status line so a permanent line can be written over it."""
        if self._painted:
            self._write("\r" + " " * self._painted + "\r")
            self._painted = 0

    def _line(
        self, marker: str, step: str, detail: str, style: str = "", dim: bool = False
    ) -> None:
        """A permanent line. Caller holds the lock."""
        self._clear()
        mark = self.paint(marker, style) if style else self.paint(marker, "dim") if dim else marker
        body = f" {step:<14} {detail}".rstrip()
        self._write(mark + (self.paint(body, "dim") if dim else body) + "\n")
        self._status()

    def _status(self) -> None:
        """Repaint the bottom line. Caller holds the lock."""
        if not self.live or not self._running:
            return
        now = time.monotonic()
        spin = SPINNER[self._frame % len(SPINNER)]
        running = ", ".join(
            f"{step} {self._duration(now - since)}"
            for step, since in sorted(self._running.items(), key=lambda kv: kv[1])
        )
        text = f"{spin} {running}"
        width = shutil.get_terminal_size((80, 24)).columns - 1
        if len(text) > width:
            text = text[: max(0, width - 1)] + "…"
        self._write("\r" + " " * self._painted + "\r" + self.paint(text, "dim"))
        self._painted = len(text)

    def _repaint_loop(self) -> None:
        while not self._stop.wait(REPAINT_SECONDS):
            with self._lock:
                self._frame += 1
                self._status()
