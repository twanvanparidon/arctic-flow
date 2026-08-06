"""Command results as terminal text.

Pure functions, so these tests are the cheapest in the suite and cover the most wording.
Two module-wide rules get a test each: every function returns text with no trailing newline
because the caller prints it, and nothing here is coloured because colour belongs where the
stream is known.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cli import render
from commands.results import (
    ComponentEntry,
    Inventory,
    KindListing,
    LintResult,
    PathsReport,
    RootReport,
    RunResult,
    SecretListing,
    SecretSet,
    VaultContents,
    VaultCreated,
)

INVENTORY = Inventory(
    adapters={"claude_code": "Run one LLM turn."},
    kinds=(
        KindListing(
            kind="flow",
            entries=(ComponentEntry(name="demo", path=Path("f"), display="./flows/demo.yaml"),),
        ),
        KindListing(kind="agent", entries=()),
    ),
)


class TestCount:
    @pytest.mark.parametrize(("n", "expected"), [(0, "0 steps"), (1, "1 step"), (2, "2 steps")])
    def test_it_agrees_with_itself(self, n: int, expected: str) -> None:
        """Three commands were printing "1 steps"."""
        assert render.count(n, "step") == expected


class TestLint:
    def test_says_what_was_checked(self) -> None:
        result = LintResult(
            flow="demo", path=Path("f"), display="./flows/demo.yaml", steps=[{"id": "a"}]
        )
        assert render.lint(result) == "./flows/demo.yaml: ok, 1 step, no issues found"


class TestTrace:
    def test_it_is_json_for_something_else_to_read(self) -> None:
        result = RunResult(
            flow="demo",
            path=Path("f"),
            display="./f",
            output="out",
            trace=[{"step": "a", "cost_usd": 0.011111119}],
        )
        parsed = json.loads(render.trace(result))
        assert parsed["flow"] == "demo"
        assert parsed["steps"] == [{"step": "a", "cost_usd": 0.011111119}]

    def test_the_total_is_rounded_to_something_readable(self) -> None:
        result = RunResult(
            flow="demo",
            path=Path("f"),
            display="./f",
            output="",
            trace=[{"cost_usd": 0.0111111119}],
        )
        assert json.loads(render.trace(result))["cost_usd"] == 0.011111


class TestInventory:
    def test_adapters_come_first(self) -> None:
        assert render.inventory(INVENTORY).startswith("adapters:\n  claude_code")

    def test_a_kind_with_nothing_installed_says_so_on_one_line(self) -> None:
        """Rather than a heading over nothing."""
        assert "agents: none" in render.inventory(INVENTORY)

    def test_an_entry_shows_where_its_definition_is(self) -> None:
        assert "  demo               ./flows/demo.yaml" in render.inventory(INVENTORY)

    def test_shadowing_is_noted_beside_the_winner(self) -> None:
        listing = Inventory(
            kinds=(
                KindListing(
                    kind="tool",
                    entries=(
                        ComponentEntry(
                            name="read_file",
                            path=Path("t"),
                            display="./tools/read_file",
                            shadows=("~/.arctic/tools/read_file", "/opt/atf/tools/read_file"),
                        ),
                    ),
                ),
            )
        )
        text = render.inventory(listing)
        assert "(shadows ~/.arctic/tools/read_file, /opt/atf/tools/read_file)" in text


class TestSearchPaths:
    REPORT = PathsReport(
        roots=(
            RootReport(path=Path("/p"), display=".", subdirs=("flows", "tools")),
            RootReport(path=Path("/h"), display="~/.arctic", subdirs=()),
        ),
        workspace=Path("/p"),
    )

    def test_the_roots_are_numbered_in_precedence_order(self) -> None:
        text = render.search_paths(self.REPORT)
        assert "  1. ." in text
        assert "  2. ~/.arctic" in text

    def test_a_root_with_nothing_in_it_reads_as_answered(self) -> None:
        """Rather than left blank, which reads as "I did not look"."""
        assert "     (nothing)" in render.search_paths(self.REPORT)

    def test_it_says_where_components_will_run(self) -> None:
        assert "working directory: /p" in render.search_paths(self.REPORT)


class TestVaultWording:
    def test_a_created_vault_counts_its_secrets(self) -> None:
        result = VaultCreated(path=Path("v"), display="./v", count=1)
        assert render.vault_created(result) == "wrote ./v (1 secret)"

    def test_an_addition_and_a_replacement_read_differently(self) -> None:
        """The caller cannot tell afterwards, and it is the one thing worth reporting."""
        added = SecretSet(path=Path("v"), display="./v", name="token", replaced=False)
        replaced = SecretSet(path=Path("v"), display="./v", name="token", replaced=True)
        assert render.secret_set(added) == "added token in ./v"
        assert render.secret_set(replaced) == "replaced token in ./v"

    def test_a_listing_is_a_count_and_then_the_names(self) -> None:
        result = SecretListing(path=Path("v"), display="./v", names=("a", "b"))
        assert render.secret_names(result) == "./v: 2 secrets\n  a\n  b"

    def test_an_empty_vault_lists_nothing_under_its_count(self) -> None:
        result = SecretListing(path=Path("v"), display="./v", names=())
        assert render.secret_names(result) == "./v: 0 secrets"


class TestVaultContents:
    RESULT = VaultContents(path=Path("v"), display="./v", values={"b": "2", "a": "1"})

    def test_it_reads_back_in_as_the_yaml_that_created_it(self) -> None:
        assert yaml.safe_load(render.vault_contents(self.RESULT)) == {"a": "1", "b": "2"}

    def test_it_is_sorted_so_two_dumps_are_safe_to_diff(self) -> None:
        assert render.vault_contents(self.RESULT).splitlines() == ["a: '1'", "b: '2'"]

    def test_it_does_not_end_with_a_newline(self) -> None:
        assert not render.vault_contents(self.RESULT).endswith("\n")


class TestTheHouseRules:
    @pytest.mark.parametrize(
        "text",
        [
            render.lint(LintResult(flow="d", path=Path("f"), display="./f", steps=[])),
            render.search_paths(TestSearchPaths.REPORT),
            render.vault_created(VaultCreated(path=Path("v"), display="./v", count=2)),
            render.secret_names(SecretListing(path=Path("v"), display="./v", names=("a",))),
            render.vault_contents(TestVaultContents.RESULT),
        ],
    )
    def test_no_message_terminates_its_own_last_line(self, text: str) -> None:
        """The caller prints, so a trailing newline here shows up as a stray blank line."""
        assert not text.endswith("\n")

    def test_a_listing_ends_with_one_blank_line_and_not_two(self) -> None:
        """The blank line between kinds is part of the listing's shape, so it is an empty
        final element. The newline that terminates it is still the printer's job."""
        text = render.inventory(INVENTORY)
        assert text.endswith("agents: none\n")
        assert not text.endswith("\n\n")

    @pytest.mark.parametrize(
        "text",
        [
            render.inventory(INVENTORY),
            render.search_paths(TestSearchPaths.REPORT),
        ],
    )
    def test_nothing_is_coloured(self, text: str) -> None:
        """Colour belongs where the stream is known, which is not here."""
        assert "\033[" not in text
