"""Whether to use colour, and which colours to use.

One decision in one place, and the order it is made in is the point: someone who set a
variable has said what they want more clearly than a terminal check can. The terminal
checks run against a real pseudo-terminal, so "is this a tty" is answered by the kernel.
"""

from __future__ import annotations

import io
import sys

import pytest

from cli import colour
from support.terminal import Terminal


class TestEnabled:
    def test_a_terminal_gets_colour(self, terminal: Terminal) -> None:
        assert colour.enabled(terminal.stream) is True

    def test_a_pipe_does_not(self) -> None:
        assert colour.enabled(io.StringIO()) is False

    def test_no_color_wins_over_a_terminal(
        self, terminal: Terminal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        assert colour.enabled(terminal.stream) is False

    def test_no_color_wins_over_force_color(
        self, terminal: Terminal, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Checked first, so the way to be sure of plain output is one variable."""
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert colour.enabled(terminal.stream) is False

    def test_force_color_colours_a_pipe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert colour.enabled(io.StringIO()) is True

    @pytest.mark.parametrize("value", ["1", "false"])
    def test_the_variables_answer_to_being_set_not_to_their_value(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """https://no-color.org: presence is the signal."""
        monkeypatch.setenv("NO_COLOR", value)
        assert colour.enabled(io.StringIO()) is False

    def test_a_closed_stream_is_not_a_terminal(self) -> None:
        stream = io.StringIO()
        stream.close()
        assert colour.enabled(stream) is False

    def test_something_that_is_not_a_stream_is_not_a_terminal(self) -> None:
        assert colour.enabled(object()) is False  # type: ignore[arg-type]

    def test_the_default_stream_is_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        assert colour.enabled() is False


class TestPainter:
    def test_paints_when_it_is_on(self) -> None:
        assert colour.Painter(on=True)("hello", "red") == "\033[31mhello\033[0m"

    def test_returns_the_text_untouched_when_it_is_off(self) -> None:
        assert colour.Painter(on=False)("hello", "red") == "hello"

    def test_several_styles_share_one_escape_sequence(self) -> None:
        assert colour.Painter(on=True)("hi", "bold", "cyan") == "\033[1;36mhi\033[0m"

    def test_asking_for_no_style_paints_nothing(self) -> None:
        assert colour.Painter(on=True)("hello") == "hello"

    def test_empty_text_stays_empty(self) -> None:
        """Otherwise a reset sequence lands on a line with nothing on it."""
        assert colour.Painter(on=True)("", "red") == ""

    def test_a_style_that_does_not_exist_is_a_mistake_in_the_code(self) -> None:
        """Five entries, deliberately, so a sixth cannot be added by accident."""
        with pytest.raises(KeyError):
            colour.Painter(on=True)("hi", "magenta")


class TestPainterFactory:
    def test_it_follows_the_stream_by_default(self, terminal: Terminal) -> None:
        assert colour.painter(terminal.stream).on is True

    @pytest.mark.parametrize("decision", [True, False])
    def test_a_caller_that_has_already_decided_wins(
        self, terminal: Terminal, decision: bool
    ) -> None:
        """The output frame draws only when both streams are terminals, which is stricter."""
        assert colour.painter(terminal.stream, on=decision).on is decision


class TestStyles:
    def test_there_are_five(self) -> None:
        assert set(colour.STYLES) == {"bold", "dim", "red", "green", "cyan"}
