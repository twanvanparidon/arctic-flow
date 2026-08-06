"""Framing a flow's output so it reads apart from the progress above it.

The whole design is about one command:

    atf run sign_release > release.sig

producing a signature and not a signature with a rule drawn around it. So the tests are
about which stream each byte went to, and about the frame being absent whenever there is
nobody watching both of them.

Real pseudo-terminals, because "both streams are terminals" is the condition under test.
"""

from __future__ import annotations

import io

import pytest

from cli.output import MAX_WIDTH, MIN_ARM, _labelled_rule, _width, flow_output
from support.terminal import Terminal


class TestPipedOutput:
    def test_the_flows_own_bytes_go_to_stdout(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        flow_output("the signature", stdout=out, stderr=err)
        assert out.getvalue() == "the signature\n"

    def test_nothing_at_all_goes_to_stderr(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        flow_output("the signature", label="sign_release", stdout=out, stderr=err)
        assert err.getvalue() == ""

    def test_output_that_already_ends_in_a_newline_does_not_get_a_second(self) -> None:
        """Exactly one, so a redirect produces a well-formed file and never a blank line."""
        out = io.StringIO()
        flow_output("the signature\n", stdout=out, stderr=io.StringIO())
        assert out.getvalue() == "the signature\n"

    def test_a_trailing_blank_line_inside_the_output_is_left_alone(self) -> None:
        out = io.StringIO()
        flow_output("two\nlines\n\n", stdout=out, stderr=io.StringIO())
        assert out.getvalue() == "two\nlines\n\n"

    def test_a_flow_that_resolved_to_nothing_writes_nothing(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        flow_output("", stdout=out, stderr=err)
        assert (out.getvalue(), err.getvalue()) == ("", "")


class TestTheFrame:
    def test_it_is_drawn_when_both_streams_are_terminals(
        self, two_terminals: tuple[Terminal, Terminal]
    ) -> None:
        out, err = two_terminals
        flow_output("the answer", label="demo", stdout=out.stream, stderr=err.stream)
        assert "output · demo" in err.read()

    def test_the_output_itself_still_goes_to_stdout_untouched(
        self, two_terminals: tuple[Terminal, Terminal]
    ) -> None:
        out, err = two_terminals
        flow_output("the answer", label="demo", stdout=out.stream, stderr=err.stream)
        assert out.read() == "the answer\n"

    def test_it_has_no_left_edge(self, two_terminals: tuple[Terminal, Terminal]) -> None:
        """A `|` on those lines would mean editing output that is not ours to edit."""
        out, err = two_terminals
        flow_output("the answer", stdout=out.stream, stderr=err.stream)
        assert not any(line.startswith("|") for line in err.read().splitlines())

    def test_it_closes_under_the_output(self, two_terminals: tuple[Terminal, Terminal]) -> None:
        out, err = two_terminals
        flow_output("the answer", stdout=out.stream, stderr=err.stream)
        assert len([line for line in err.read().splitlines() if "─" in line]) == 2

    def test_it_is_not_drawn_when_only_stdout_is_a_terminal(self, terminal: Terminal) -> None:
        """A frame around output that went somewhere else is worse than none."""
        err = io.StringIO()
        flow_output("the answer", stdout=terminal.stream, stderr=err)
        assert err.getvalue() == ""

    def test_it_is_not_drawn_when_only_stderr_is_a_terminal(self, terminal: Terminal) -> None:
        out = io.StringIO()
        flow_output("the answer", stdout=out, stderr=terminal.stream)
        assert terminal.read() == ""

    def test_quiet_turns_it_off_on_a_terminal(
        self, two_terminals: tuple[Terminal, Terminal]
    ) -> None:
        out, err = two_terminals
        flow_output("the answer", frame=False, stdout=out.stream, stderr=err.stream)
        assert err.read() == ""
        assert out.read() == "the answer\n"

    def test_output_that_is_only_whitespace_is_not_framed(
        self, two_terminals: tuple[Terminal, Terminal]
    ) -> None:
        out, err = two_terminals
        flow_output("   \n", stdout=out.stream, stderr=err.stream)
        assert "─" not in err.read()

    def test_a_flow_that_resolved_to_nothing_says_so_on_a_terminal(
        self, terminal: Terminal
    ) -> None:
        """Otherwise it looks identical to a flow that never ran."""
        flow_output("", stdout=io.StringIO(), stderr=terminal.stream)
        assert "(no output)" in terminal.read()

    def test_quiet_does_not_even_say_that(self, terminal: Terminal) -> None:
        flow_output("", frame=False, stdout=io.StringIO(), stderr=terminal.stream)
        assert terminal.read() == ""


class TestRuleWidth:
    def test_it_stops_short_of_a_very_wide_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 200-column terminal does not want a 200-column line drawn across it."""
        monkeypatch.setenv("COLUMNS", "200")
        assert _width() == MAX_WIDTH

    def test_it_follows_a_narrow_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLUMNS", "40")
        assert _width() == 39

    def test_the_label_sits_in_the_middle_of_the_rule(self) -> None:
        left, text, right = _labelled_rule("output")
        assert text == " output "
        assert len(left) + len(text) + len(right) == _width()

    def test_a_label_wider_than_the_terminal_keeps_stubs_of_rule(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COLUMNS", "20")
        left, _, right = _labelled_rule("a very long flow name indeed")
        assert len(left) == len(right) == MIN_ARM
