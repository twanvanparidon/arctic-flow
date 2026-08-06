"""The seam between argparse and `commands/`.

Three things live in this layer rather than in the command layer, and each is a policy of
the terminal: which stream a byte goes to, where a value comes from, and when to ask. The
first two are what these tests are about. The third is covered by the vault command tests,
which check that nothing prompts before it has to.

`--value` is deliberately not a flag anywhere here, so a secret is read from stdin or from
a prompt. The prompt needs a controlling terminal and belongs to the end-to-end suite.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pytest

from cli import dispatch
from engine.executor import FlowError


class TestParseInputPairs:
    def test_a_pair_becomes_a_mapping(self) -> None:
        assert dispatch.parse_input_pairs(["path=notes.md"]) == {"path": "notes.md"}

    def test_several_pairs_accumulate(self) -> None:
        pairs = ["path=notes.md", "depth=2"]
        assert dispatch.parse_input_pairs(pairs) == {"path": "notes.md", "depth": "2"}

    def test_nothing_supplied_is_an_empty_mapping(self) -> None:
        assert dispatch.parse_input_pairs([]) == {}

    def test_a_repeated_key_takes_its_last_value(self) -> None:
        assert dispatch.parse_input_pairs(["a=1", "a=2"]) == {"a": "2"}

    def test_only_the_first_equals_separates(self) -> None:
        """So a value may contain one, which query strings and URLs regularly do."""
        assert dispatch.parse_input_pairs(["q=a=b"]) == {"q": "a=b"}

    def test_an_empty_value_is_allowed(self) -> None:
        assert dispatch.parse_input_pairs(["note="]) == {"note": ""}

    def test_something_with_no_equals_is_refused(self) -> None:
        with pytest.raises(FlowError, match="--input expects KEY=VALUE"):
            dispatch.parse_input_pairs(["path"])

    def test_one_bad_pair_refuses_the_lot(self) -> None:
        """Rather than silently dropping the one that was typed wrong."""
        with pytest.raises(FlowError, match="--input expects KEY=VALUE"):
            dispatch.parse_input_pairs(["a=1", "b"])


class TestPasswordProvider:
    def test_it_does_not_resolve_anything_until_it_is_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So a command that turns out not to need a password never asks for one."""
        args = argparse.Namespace(vault_password_file=None)
        provider = dispatch.password_provider(args)
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "resolved late")
        assert provider() == "resolved late"

    def test_it_reads_the_file_the_flag_named(self, tmp_path: Path) -> None:
        path = tmp_path / "pw"
        path.write_text("from the file")
        args = argparse.Namespace(vault_password_file=path)
        assert dispatch.password_provider(args)() == "from the file"

    def test_a_command_with_no_such_flag_still_gets_a_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "from the environment")
        assert dispatch.password_provider(argparse.Namespace())() == "from the environment"


class TestReadingStdin:
    def test_everything_piped_in_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("a: 1\nb: 2\n"))
        assert dispatch.stdin_text() == "a: 1\nb: 2\n"

    def test_a_secret_keeps_everything_but_its_trailing_newlines(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`echo key | …` and `printf key | …` have to store the same secret."""
        monkeypatch.setattr(sys, "stdin", io.StringIO("s3cret\n"))
        assert dispatch.secret_value("token") == "s3cret"

    def test_a_secret_may_contain_newlines_of_its_own(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A PEM key is one credential, not several."""
        monkeypatch.setattr(sys, "stdin", io.StringIO("line one\nline two\n"))
        assert dispatch.secret_value("key") == "line one\nline two"

    def test_leading_whitespace_is_part_of_the_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("  padded\n"))
        assert dispatch.secret_value("token") == "  padded"
