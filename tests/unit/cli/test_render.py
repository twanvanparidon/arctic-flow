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
    AdapterDetail,
    AgentDetail,
    ComponentEntry,
    FlowIssue,
    Inventory,
    KindListing,
    LintReport,
    LintResult,
    RunResult,
    SecretListing,
    SecretSet,
    ToolDetail,
    VaultContents,
    VaultCreated,
)

INVENTORY = Inventory(
    adapters=(
        ComponentEntry(
            name="claude_code",
            path=Path("a"),
            display="./src/adapters/claude_code.py",
        ),
    ),
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
        rendered = render.lint(result)
        assert "./flows/demo.yaml" in rendered
        assert "1 step" in rendered


class TestLintReport:
    PASSED = LintResult(flow="demo", path=Path("f"), display="./flows/demo.yaml", steps=[{}])
    BROKEN = FlowIssue(
        flow="bad", path=Path("b"), display="./flows/bad.yaml", error="unknown tool 'ghost'"
    )

    def test_a_clean_sweep_says_what_it_checked(self) -> None:
        text = render.lint_report(LintReport(checked=(self.PASSED,)))
        assert "./flows/demo.yaml" in text
        assert text.endswith("1 flow checked, no issues found")

    def test_a_failure_names_the_flow_and_what_stopped_it(self) -> None:
        text = render.lint_report(LintReport(checked=(self.PASSED,), issues=(self.BROKEN,)))
        assert "./flows/bad.yaml" in text
        assert "unknown tool 'ghost'" in text

    def test_failures_come_last(self) -> None:
        """Read in a pipeline log, where the end of the output is what is on screen."""
        text = render.lint_report(LintReport(checked=(self.PASSED,), issues=(self.BROKEN,)))
        assert text.index("demo.yaml") < text.index("bad.yaml")

    def test_the_count_covers_the_ones_that_failed_too(self) -> None:
        text = render.lint_report(LintReport(checked=(self.PASSED,), issues=(self.BROKEN,)))
        assert text.endswith("2 flows checked, 1 failed")

    def test_a_pass_reads_the_same_as_that_flow_checked_on_its_own(self) -> None:
        """One flow linted alone and the same flow inside a sweep say the same sentence."""
        assert render.lint(self.PASSED) in render.lint_report(LintReport(checked=(self.PASSED,)))

    def test_nothing_to_check_says_so_rather_than_counting_to_zero(self) -> None:
        assert render.lint_report(LintReport()) == "no flows found"


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
    def test_adapters_are_listed_apart_from_the_kinds_and_ahead_of_them(self) -> None:
        """They are registered in code, so no root found them and nothing can shadow one."""
        text = render.inventory(INVENTORY)
        assert "adapters:\n  claude_code" in text
        assert text.index("adapters:") < text.index("flows:")

    def test_a_kind_with_nothing_installed_says_so_on_one_line(self) -> None:
        """Rather than a heading over nothing."""
        assert "agents: none" in render.inventory(INVENTORY)

    def test_an_entry_shows_where_its_definition_is(self) -> None:
        line = next(line for line in render.inventory(INVENTORY).splitlines() if "demo" in line)
        assert "./flows/demo.yaml" in line

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


AGENT = AgentDetail(
    name="summarizer",
    path=Path("a"),
    display="./agents/summarizer",
    spec={
        "name": "summarizer",
        "description": "Explains what a file does.",
        "adapter": "claude_code",
        "model": "sonnet",
        "tools": [],
        "output_schema": {"type": "object"},
    },
    prompt="You summarise source files.",
)

TOOL = ToolDetail(
    name="common/read_file",
    path=Path("t"),
    display="./tools/common/read_file",
    spec={
        "name": "read_file",
        "description": "Read a file.",
        "run": {"command": ["./run.sh"], "timeout_seconds": 10},
        "input_schema": {"type": "object"},
        "permissions": {"filesystem": "read", "network": False},
        "secrets": ["token"],
        "exit_codes": {"0": "success", "3": "not found"},
    },
    doc="# read_file",
)


ADAPTER = AdapterDetail(
    name="echo",
    path=Path("e"),
    display="$ATF_ROOT/adapters/echo.py",
    description="Answer from the request rather than a model.",
    input_schema={"type": "object", "properties": {"prompt": {"type": "string"}}},
)


class TestAdapterDetail:
    def test_it_names_the_module_it_is(self) -> None:
        assert render.adapter_detail(ADAPTER).startswith("echo  $ATF_ROOT/adapters/echo.py")

    def test_the_settings_schema_is_the_body(self) -> None:
        """It is the answer to "what may an agent spec naming this ask for", and the same
        schema `engine.specs` checks that spec against."""
        text = render.adapter_detail(ADAPTER)
        assert "settings:" in text
        assert '"prompt"' in text


class TestAgentDetail:
    def test_it_names_the_definition_that_won(self) -> None:
        assert render.agent_detail(AGENT).startswith("summarizer  ./agents/summarizer")

    def test_it_shows_the_settings_a_flow_author_cannot_see_from_the_flow(self) -> None:
        text = render.agent_detail(AGENT)
        assert "adapter" in text and "claude_code" in text
        assert "model" in text and "sonnet" in text

    def test_the_prompt_is_reproduced_rather_than_summarised(self) -> None:
        """It *is* the agent, so a view that abbreviated it would answer a different
        question from the one being asked."""
        assert render.agent_detail(AGENT).endswith("You summarise source files.")

    def test_a_field_the_spec_leaves_out_is_not_printed_empty(self) -> None:
        """A spec with no `effort` is not an agent with no effort. Its adapter decides."""
        assert "effort" not in render.agent_detail(AGENT)

    def test_an_empty_list_is_named_rather_than_left_blank(self) -> None:
        assert "(none)" in render.agent_detail(AGENT)

    def test_a_declared_output_schema_is_shown(self) -> None:
        assert "output schema:" in render.agent_detail(AGENT)


class TestToolDetail:
    def test_it_names_the_tool_as_it_was_looked_up(self) -> None:
        """Not `spec["name"]`, which for a namespaced tool carries only the leaf."""
        assert render.tool_detail(TOOL).startswith("common/read_file  ./tools/common/")

    def test_the_two_fields_that_decide_a_grant_are_shown(self) -> None:
        """`filesystem` gates whether granting it needs `unattended`, and a tool declaring
        `secrets` cannot be granted at all."""
        text = render.tool_detail(TOOL)
        assert "filesystem" in text and "read" in text
        assert "secrets" in text and "token" in text

    def test_the_nested_run_object_is_lifted_out(self) -> None:
        text = render.tool_detail(TOOL)
        assert "command" in text and "./run.sh" in text

    def test_the_exit_codes_are_listed_with_what_they_mean(self) -> None:
        text = render.tool_detail(TOOL)
        assert "3" in text and "not found" in text

    def test_the_schemas_are_shown_as_json(self) -> None:
        assert '"type": "object"' in render.tool_detail(TOOL)

    def test_the_doc_comes_last(self) -> None:
        assert render.tool_detail(TOOL).endswith("# read_file")

    def test_a_tool_with_no_doc_ends_cleanly(self) -> None:
        """The caller prints, so a trailing blank line here shows up as a stray one."""
        text = render.tool_detail(ToolDetail(**{**vars(TOOL), "doc": ""}))
        assert not text.endswith("\n")


class TestVaultWording:
    def test_a_created_vault_counts_its_secrets(self) -> None:
        result = VaultCreated(path=Path("v"), display="./v", count=1)
        assert "./v" in render.vault_created(result)
        assert "1 secret" in render.vault_created(result)

    def test_an_addition_and_a_replacement_read_differently(self) -> None:
        """The caller cannot tell afterwards, and it is the one thing worth reporting."""
        added = SecretSet(path=Path("v"), display="./v", name="token", replaced=False)
        replaced = SecretSet(path=Path("v"), display="./v", name="token", replaced=True)
        assert render.secret_set(added).startswith("added ")
        assert render.secret_set(replaced).startswith("replaced ")

    def test_a_listing_is_a_count_and_then_the_names(self) -> None:
        result = SecretListing(path=Path("v"), display="./v", names=("a", "b"))
        lines = render.secret_names(result).splitlines()
        assert "2 secrets" in lines[0]
        assert [line.strip() for line in lines[1:]] == ["a", "b"]

    def test_an_empty_vault_lists_nothing_under_its_count(self) -> None:
        result = SecretListing(path=Path("v"), display="./v", names=())
        assert render.secret_names(result).splitlines() == [
            render.secret_names(result)
        ]  # one line, no names under it
        assert "0 secrets" in render.secret_names(result)


class TestVaultContents:
    RESULT = VaultContents(path=Path("v"), display="./v", values={"b": "2", "a": "1"})

    def test_it_reads_back_in_as_the_yaml_that_created_it(self) -> None:
        assert yaml.safe_load(render.vault_contents(self.RESULT)) == {"a": "1", "b": "2"}

    def test_it_is_sorted_so_two_dumps_are_safe_to_diff(self) -> None:
        lines = render.vault_contents(self.RESULT).splitlines()
        assert [line.split(":")[0] for line in lines] == ["a", "b"]

    def test_it_does_not_end_with_a_newline(self) -> None:
        assert not render.vault_contents(self.RESULT).endswith("\n")


class TestTheHouseRules:
    @pytest.mark.parametrize(
        "text",
        [
            render.lint(LintResult(flow="d", path=Path("f"), display="./f", steps=[])),
            render.adapter_detail(ADAPTER),
            render.agent_detail(AGENT),
            render.tool_detail(TOOL),
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
        ],
    )
    def test_nothing_is_coloured(self, text: str) -> None:
        """Colour belongs where the stream is known, which is not here."""
        assert "\033[" not in text
