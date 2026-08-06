"""The banner, and the two things that shape it.

ASCII only, because a snowflake given emoji presentation renders double-width and skews
everything aligned after it, and a non-UTF-8 stdout turns a decoration into a
UnicodeEncodeError. Suppressed off a terminal, because `atf --version` gets parsed by
scripts and read in CI logs.
"""

from __future__ import annotations

import io

from cli import branding
from support.terminal import Terminal


class TestBanner:
    def test_nothing_is_drawn_into_a_pipe(self) -> None:
        assert branding.banner("1.2.3", io.StringIO()) == ""

    def test_a_terminal_gets_the_wordmark(self, terminal: Terminal) -> None:
        assert branding.WORDMARK in branding.banner("1.2.3", terminal.stream)

    def test_it_names_the_command_and_the_version(self, terminal: Terminal) -> None:
        assert "atf 1.2.3" in branding.banner("1.2.3", terminal.stream)

    def test_it_carries_the_tagline(self, terminal: Terminal) -> None:
        assert branding.TAGLINE in branding.banner("1.2.3", terminal.stream)

    def test_it_is_ascii_only(self, terminal: Terminal) -> None:
        """A `❄` is the obvious glyph and the wrong choice."""
        assert branding.banner("1.2.3", terminal.stream).isascii()

    def test_it_ends_with_one_blank_line(self, terminal: Terminal) -> None:
        assert branding.banner("1.2.3", terminal.stream).endswith("\n\n")

    def test_the_flake_and_the_text_beside_it_stay_the_same_height(self) -> None:
        """Kept as pairs, and zipped strictly, so neither can grow without the other."""
        assert len(branding._FLAKE) == 5


class TestVersionLine:
    def test_a_pipe_gets_one_parseable_line(self) -> None:
        assert branding.version_line("1.2.3", io.StringIO()) == "atf 1.2.3\n"

    def test_a_terminal_gets_the_banner(self, terminal: Terminal) -> None:
        assert branding.version_line("1.2.3", terminal.stream) == branding.banner(
            "1.2.3", terminal.stream
        )


class TestVersion:
    def test_it_is_a_dotted_release_number(self) -> None:
        """pyproject.toml reads this attribute, and release.sh refuses a tag that disagrees."""
        assert branding.__version__.count(".") == 2
        assert all(part.isdigit() for part in branding.__version__.split("."))
