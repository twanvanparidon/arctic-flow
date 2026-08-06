"""Live progress for a running flow.

The engine emits events and this decides how they look, so every test here hands it an
event dict and reads what came out of the stream. Most use an ordinary in-memory stream:
that is the not-a-terminal mode, where there is no repainting and no colour, and a CI log
reads as text. The live mode gets a real pseudo-terminal, because the repaint thread starts
only when the stream says it is one.

Everything goes to stderr in real use. Here it goes to whatever stream is passed, which is
the same thing said in a way a test can read.
"""

from __future__ import annotations

import io
import time
from typing import Any

import pytest

from cli.progress import Progress
from support.terminal import Terminal


def started(step: str = "a", component: str = "tool t") -> dict[str, Any]:
    return {"kind": "started", "step": step, "component": component}


def finished(step: str = "a", **extra: Any) -> dict[str, Any]:
    return {"kind": "finished", "step": step, "ms": 120, **extra}


class TestDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "0ms"),
            (0.12, "120ms"),
            (0.9994, "999ms"),
            (1.0, "1.0s"),
            (59.94, "59.9s"),
            (60.0, "1m00s"),
            (125.0, "2m05s"),
            (3601.0, "60m01s"),
        ],
    )
    def test_the_unit_follows_the_size(self, seconds: float, expected: str) -> None:
        """An agent step can take ninety seconds, and "working" against "hung" is all you
        want to know."""
        assert Progress._duration(seconds) == expected


class TestMoney:
    def test_a_flow_that_cost_nothing_gets_no_cost_line(self) -> None:
        assert Progress._money(0.0) == ""

    def test_four_decimals_rather_than_two(self) -> None:
        """These runs land in fractions of a cent often enough that $0.00 would be usual."""
        assert Progress._money(0.00012) == " · $0.0001"

    def test_a_real_amount_reads_as_one(self) -> None:
        assert Progress._money(1.5) == " · $1.5000"


class TestStepDetail:
    def test_a_finished_step_reports_how_long_it_took(self) -> None:
        assert Progress(io.StringIO())._finished_detail(finished()) == "120ms"

    def test_a_switch_says_where_it_sent_its_result(self) -> None:
        detail = Progress(io.StringIO())._finished_detail(
            finished(is_switch=True, pushed_to=["b", "c"])
        )
        assert "120ms" in detail
        assert "→ b, c" in detail

    def test_a_linear_step_does_not(self) -> None:
        """The next step is already obvious from the flow."""
        detail = Progress(io.StringIO())._finished_detail(finished(pushed_to=["b"]))
        assert detail == "120ms"

    def test_a_rejected_attempt_says_whether_there_is_another(self) -> None:
        event = {"tool": "word_limit", "attempt": 1, "of": 3}
        detail = Progress._gate_detail(event)
        assert "word_limit" in detail
        assert "1/3" in detail

    def test_the_last_attempt_says_it_was_the_last(self) -> None:
        """Whether the step is converging or about to run out of turns."""
        last = Progress._gate_detail({"tool": "word_limit", "attempt": 3, "of": 3})
        earlier = Progress._gate_detail({"tool": "word_limit", "attempt": 2, "of": 3})
        assert "3/3" in last
        # Something beyond the number differs, which is the part being tested.
        assert last != earlier.replace("2/3", "3/3")


class TestEventLines:
    def test_a_starting_step_names_what_it_runs(self) -> None:
        stream = io.StringIO()
        Progress(stream)(started("read", "tool read_file"))
        assert stream.getvalue().startswith("→ ")
        assert "read" in stream.getvalue()
        assert "tool read_file" in stream.getvalue()

    def test_a_finished_step_is_ticked(self) -> None:
        stream = io.StringIO()
        Progress(stream)(finished("read"))
        assert stream.getvalue().startswith("✓ ")
        assert "read" in stream.getvalue()
        assert "120ms" in stream.getvalue()

    def test_a_skipped_step_says_why(self) -> None:
        stream = io.StringIO()
        Progress(stream)({"kind": "skipped", "step": "scan"})
        assert stream.getvalue().startswith("⤼ ")
        assert "skipped" in stream.getvalue()

    def test_a_failed_step_carries_the_error(self) -> None:
        stream = io.StringIO()
        Progress(stream)({"kind": "failed", "step": "sign", "error": "no signing key"})
        assert stream.getvalue().startswith("✗ ")
        assert "no signing key" in stream.getvalue()

    def test_a_rejected_gate_earns_a_line(self) -> None:
        stream = io.StringIO()
        Progress(stream)({"kind": "gated", "step": "draft", "tool": "wc", "attempt": 1, "of": 3})
        assert "⟲ draft" in stream.getvalue()

    def test_a_gate_that_passed_does_not(self) -> None:
        """It is followed straight away by the step's own tick."""
        stream = io.StringIO()
        Progress(stream)({"kind": "gated", "step": "draft", "ok": True, "attempt": 1, "of": 3})
        assert stream.getvalue() == ""

    def test_an_unrecognised_event_is_ignored(self) -> None:
        """A new event kind should not crash a front end that predates it."""
        stream = io.StringIO()
        Progress(stream)({"kind": "invented", "step": "a"})
        assert stream.getvalue() == ""

    def test_nothing_is_written_at_all_when_it_is_switched_off(self) -> None:
        stream = io.StringIO()
        progress = Progress(stream, enabled=False)
        progress(started())
        progress(finished())
        progress.summary()
        assert stream.getvalue() == ""

    def test_a_pipe_gets_no_colour(self) -> None:
        stream = io.StringIO()
        Progress(stream)(finished("read"))
        assert "\033[" not in stream.getvalue()


class TestSummary:
    def test_it_counts_the_steps_that_finished(self) -> None:
        stream = io.StringIO()
        progress = Progress(stream)
        progress(finished("a"))
        progress(finished("b"))
        progress.summary()
        assert "2 steps · " in stream.getvalue()

    def test_one_step_is_not_one_steps(self) -> None:
        stream = io.StringIO()
        progress = Progress(stream)
        progress(finished("a"))
        progress.summary()
        assert "1 step · " in stream.getvalue()

    def test_skipped_steps_are_counted_apart(self) -> None:
        stream = io.StringIO()
        progress = Progress(stream)
        progress(finished("a"))
        progress({"kind": "skipped", "step": "b"})
        progress.summary()
        assert "1 step (1 skipped)" in stream.getvalue()

    def test_the_total_cost_is_accumulated_as_the_run_goes(self) -> None:
        """So a run that dies halfway can still report what it already spent."""
        stream = io.StringIO()
        progress = Progress(stream)
        progress(finished("a", cost_usd=0.01))
        progress(finished("b", cost_usd=0.02))
        progress.summary(ok=False)
        assert "failed after" in stream.getvalue()
        assert "$0.0300" in stream.getvalue()

    def test_a_step_that_reported_no_cost_adds_nothing(self) -> None:
        stream = io.StringIO()
        progress = Progress(stream)
        progress(finished("a", cost_usd=None))
        progress.summary()
        assert "$" not in stream.getvalue()

    def test_it_opens_with_a_blank_line(self) -> None:
        """So the tagline closes the block of steps instead of reading as one more of them."""
        stream = io.StringIO()
        progress = Progress(stream)
        progress(finished("a"))
        progress.summary()
        assert "\n\n" in stream.getvalue()
        assert stream.getvalue().rstrip().endswith("1 step · 0ms")


class TestLiveMode:
    def test_a_terminal_gets_a_repainting_status_line(self, terminal: Terminal) -> None:
        with Progress(terminal.stream) as progress:
            assert progress.live is True
            progress(started("read"))
        assert "read" in terminal.read()

    def test_a_pipe_does_not(self) -> None:
        with Progress(io.StringIO()) as progress:
            assert progress.live is False

    def test_no_color_takes_the_colour_and_not_the_clock(
        self, terminal: Terminal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tying the two together meant NO_COLOR silently removed the live clock."""
        monkeypatch.setenv("NO_COLOR", "1")
        progress = Progress(terminal.stream)
        assert progress.live is True
        assert progress.paint.on is False

    def test_being_switched_off_takes_both(self, terminal: Terminal) -> None:
        assert Progress(terminal.stream, enabled=False).live is False

    def test_leaving_the_block_clears_the_status_line(self, terminal: Terminal) -> None:
        with Progress(terminal.stream) as progress:
            progress(started("read"))
            assert progress._painted > 0
        # Blanked and returned to column zero, so the next thing written lands on a clean
        # line rather than on top of a half-erased clock.
        assert terminal.read().endswith("\r")
        assert progress._painted == 0

    def test_a_finished_step_removes_itself_from_the_status_line(self, terminal: Terminal) -> None:
        with Progress(terminal.stream) as progress:
            progress(started("read"))
            progress(finished("read"))
            assert progress._running == {}

    def test_the_clock_keeps_running_while_a_step_does(self, terminal: Terminal) -> None:
        """The point of the live mode: an agent step can take ninety seconds, and a frozen
        display and a hung engine look the same. Polled rather than slept on, with its own
        deadline, so a machine under load reads as slow instead of as broken."""
        with Progress(terminal.stream) as progress:
            progress(started("read"))
            deadline = time.monotonic() + 5
            while progress._frame == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert progress._frame > 0

    def test_the_status_line_is_cut_to_the_width_of_the_terminal(
        self, terminal: Terminal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It is repainted in place, so a line that wrapped would leave its own tail behind."""
        monkeypatch.setenv("COLUMNS", "24")
        with Progress(terminal.stream) as progress:
            progress(started("a_step_with_a_long_name"))
            progress(started("another_long_step_name"))
            assert progress._painted <= 23
        assert "…" in terminal.read()
