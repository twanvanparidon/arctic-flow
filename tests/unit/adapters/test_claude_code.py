"""The Claude Code adapter's pure parts: the command line it builds and how it reads a
failure.

`run()` is not here. It spawns the `claude` binary and needs an authenticated CLI, so it
belongs to the integration suite where a real turn can be paid for. What is testable
without one is the part that has actually gone wrong before: an explicit `isolate: false`
being read as true, and a failed turn reported as "success: 529: API Error".

The flags were verified against CLI 2.1.222 and move between releases. These tests pin what
the adapter sends, so changing a flag is a decision rather than a diff nobody reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters import claude_code
from adapters.errors import AdapterUnavailable


class TestBuildArgs:
    def test_the_default_invocation(self) -> None:
        assert claude_code.build_args({"prompt": "hello"}) == [
            "--print",
            "--output-format",
            "json",
            # Empty rather than absent: "" is the CLI's disable-all, so the default is a
            # plain completion and the engine's loop stays the only loop.
            "--tools",
            "",
            "--safe-mode",
        ]

    def test_the_prompt_is_not_an_argument(self) -> None:
        """It goes on stdin, clear of ARG_MAX and of the variadic --tools."""
        assert "hello" not in claude_code.build_args({"prompt": "hello"})

    def test_requested_tools_are_passed_as_one_comma_separated_value(self) -> None:
        args = claude_code.build_args({"prompt": "p", "tools": ["Read", "Grep"]})
        assert args[args.index("--tools") + 1] == "Read,Grep"

    def test_isolation_is_on_unless_it_is_turned_off(self) -> None:
        """A plain dict lookup, so an explicit False stays False. jq's `//` did not."""
        assert "--safe-mode" not in claude_code.build_args({"prompt": "p", "isolate": False})

    def test_asking_for_isolation_explicitly_is_the_same_as_the_default(self) -> None:
        assert claude_code.build_args({"prompt": "p", "isolate": True}) == claude_code.build_args(
            {"prompt": "p"}
        )

    @pytest.mark.parametrize(
        ("field", "flag", "value"),
        [
            ("system", "--system-prompt", "be terse"),
            ("append_system", "--append-system-prompt", "and quick"),
            ("model", "--model", "sonnet"),
            ("effort", "--effort", "high"),
            ("resume", "--resume", "abc123"),
        ],
    )
    def test_each_pass_through_option_becomes_its_flag(
        self, field: str, flag: str, value: str
    ) -> None:
        args = claude_code.build_args({"prompt": "p", field: value})
        assert args[args.index(flag) + 1] == value

    def test_a_numeric_option_is_rendered_as_text(self) -> None:
        args = claude_code.build_args({"prompt": "p", "max_budget_usd": 0.25})
        assert args[args.index("--max-budget-usd") + 1] == "0.25"

    def test_an_option_left_out_contributes_no_flag(self) -> None:
        assert "--model" not in claude_code.build_args({"prompt": "p"})

    def test_an_option_set_to_none_contributes_no_flag(self) -> None:
        assert "--model" not in claude_code.build_args({"prompt": "p", "model": None})

    def test_a_response_schema_is_sent_as_json(self) -> None:
        schema = {"type": "object", "properties": {"verdict": {"type": "string"}}}
        args = claude_code.build_args({"prompt": "p", "json_schema": schema})
        assert json.loads(args[args.index("--json-schema") + 1]) == schema

    def test_an_empty_schema_is_not_sent(self) -> None:
        assert "--json-schema" not in claude_code.build_args({"prompt": "p", "json_schema": {}})


class TestFailureDetail:
    def test_joins_what_the_cli_said(self) -> None:
        result = {"subtype": "error_during_execution", "api_error_status": 529, "result": "busy"}
        assert claude_code._failure_detail(result) == "error_during_execution: 529: busy"

    def test_drops_a_subtype_that_reads_success(self) -> None:
        """A failed turn can carry it, which produced "success: 529: API Error"."""
        result = {"subtype": "success", "api_error_status": 529, "result": "busy"}
        assert claude_code._failure_detail(result) == "529: busy"

    @pytest.mark.parametrize("absent", [None, ""])
    def test_drops_a_field_the_cli_did_not_fill_in(self, absent: object) -> None:
        assert claude_code._failure_detail({"subtype": absent, "result": "busy"}) == "busy"

    def test_says_nothing_when_there_is_nothing_to_say(self) -> None:
        assert claude_code._failure_detail({}) == ""

    def test_a_long_detail_is_truncated(self) -> None:
        """This ends up on one line of a terminal, under a failed step."""
        assert len(claude_code._failure_detail({"result": "x" * 900})) == 400


class TestSchema:
    def test_nothing_the_adapter_does_not_understand_is_accepted(self) -> None:
        """The engine builds this payload, so a typo in a forwarded field fails at lint."""
        assert claude_code.INPUT_SCHEMA["additionalProperties"] is False

    def test_effort_is_an_enumeration(self) -> None:
        assert claude_code.INPUT_SCHEMA["properties"]["effort"]["enum"] == [
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ]


class TestCliVersion:
    def test_a_runtime_that_is_not_installed_says_which_binary_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A host configuration problem, and distinct from a turn that failed: retrying it
        will not help."""
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(AdapterUnavailable, match="not on PATH"):
            claude_code.cli_version()
