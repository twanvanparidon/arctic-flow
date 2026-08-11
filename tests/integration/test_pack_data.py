"""The data pack's five tools, run the way a flow runs them.

Real `jq` and real `awk` throughout, which is the whole point: every failure worth catching
here belongs to one of them. A quoted CSV field holding a line break, jq's alternative
operator answering `true` for `false`, a jq program reading the environment it was handed:
a stand-in would answer all three the way the test expected and prove nothing.

What each class protects is the decision in its tool that could plausibly be "simplified"
away later. That a string comes back unquoted, because that is what a `switch` compares.
That a stream is refused rather than run together. That a missing field fails instead of
answering nothing. That a ragged CSV row is a data error and not a row of nulls. That a
column appearing halfway down a table stops the write rather than leaving it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from paths.config import CONFIG_FILE
from support import components
from support.outcome import Outcome, Runner

from .conftest import requires

# What the pack's own specs say they need, named once.
BINARIES = ("jq", "awk", "realpath", "mktemp", "env")

# One tool out of the pack, for the tests about the pack rather than about a tool.
A_PACK_TOOL = "arctic/data/json/query"

PACK_ROOT = Path(__file__).resolve().parents[2] / "src" / "builtin" / "packs" / "data"


@pytest.fixture(autouse=True)
def needs_jq() -> None:
    requires(*BINARIES)


@pytest.fixture(autouse=True)
def enabled(home: Path) -> None:
    """The pack resolves only once `config.yaml` names it, and every test here needs it on."""
    (home / ".arctic").mkdir(parents=True, exist_ok=True)
    (home / ".arctic" / CONFIG_FILE).write_text("packs:\n  - data\n")


def call(atf: Runner, project: Path, tool: str, **input_values: Any) -> Outcome:
    """Run one pack tool as the only step of a flow, and return the whole outcome."""
    return run_flow(
        atf,
        project,
        [{"id": "act", "tool": f"arctic/data/{tool}", "input": input_values}],
        "{{ steps.act.text }}",
    )


def run_flow(atf: Runner, project: Path, steps: list[dict[str, Any]], output: str) -> Outcome:
    """Run steps a test wrote, for the claims that need more than one of them."""
    components.write_flow(
        project,
        "call",
        {
            "flow": "call",
            "start": steps[0]["id"],
            "steps": steps,
            "output": {"template": output},
        },
    )
    return atf("--workspace", str(project), "run", "call")


def specs() -> dict[str, dict[str, Any]]:
    """Every tool the pack ships, by the path it sits at."""
    return {
        str(path.parent.relative_to(PACK_ROOT / "tools")): json.loads(path.read_text())
        for path in sorted((PACK_ROOT / "tools").rglob("spec.json"))
    }


class TestQuery:
    def test_a_string_comes_back_without_quotes(self, atf: Runner, workspace: Path) -> None:
        """Which is what makes a result switchable: `switch` compares the rendered value
        whole, and `"open"` matches no case anybody would write."""
        result = call(atf, workspace, "json/query", data='{"state":"open"}', query=".state")
        assert result.out.strip() == "open"

    def test_a_document_comes_back_as_json(self, atf: Runner, workspace: Path) -> None:
        result = call(atf, workspace, "json/query", data='{"items":[1,2]}', query=".items")
        assert json.loads(result.out) == [1, 2]

    def test_it_reads_a_file_when_given_a_path(self, atf: Runner, workspace: Path) -> None:
        (workspace / "pr.json").write_text('{"state":"merged"}')
        result = call(atf, workspace, "json/query", path="pr.json", query=".state")
        assert result.out.strip() == "merged"

    def test_a_path_outside_the_workspace_is_refused(self, atf: Runner, workspace: Path) -> None:
        result = call(atf, workspace, "json/query", path="/etc/hostname", query=".")
        assert result.code != 0
        assert "outside the workspace root" in result.err

    def test_a_stream_is_refused_rather_than_run_together(
        self, atf: Runner, workspace: Path
    ) -> None:
        """Three values on stdout parse as none, so the answer would look like data and be
        unreadable. The fix is in the message instead."""
        result = call(atf, workspace, "json/query", data='{"items":[1,2,3]}', query=".items[]")
        assert result.code != 0
        assert "3 values" in result.err and "[ ]" in result.err

    def test_a_missing_field_fails_rather_than_answering_nothing(
        self, atf: Runner, workspace: Path
    ) -> None:
        """The engine's own rule for `{{ steps.x.json.field }}`. A transform that quietly
        answered "" would hand the next step a wrong value instead of stopping."""
        result = call(atf, workspace, "json/query", data="{}", query=".verdict")
        assert result.code != 0
        assert "default" in result.err

    @pytest.mark.parametrize(
        "query", [".verdict", '.items[] | select(. == "nothing")'], ids=["absent", "no match"]
    )
    def test_a_default_answers_for_either_kind_of_nothing(
        self, atf: Runner, workspace: Path, query: str
    ) -> None:
        result = call(
            atf, workspace, "json/query", data='{"items":[]}', query=query, default="none"
        )
        assert result.code == 0
        assert result.out.strip() == "none"

    def test_a_query_cannot_read_the_environment(
        self, atf: Runner, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`child_environment` hands a tool this process's environment, so jq's own `env`
        would reach a secret the step declared or a vault password a shell exported. The
        program runs under `env -i` for exactly that reason."""
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "hunter2")
        result = call(
            atf,
            workspace,
            "json/query",
            data="{}",
            query="env.ATF_VAULT_PASSWORD",
            default="(blocked)",
        )
        assert "hunter2" not in result.out
        assert result.out.strip() == "(blocked)"

    def test_data_that_is_not_json_names_the_data(self, atf: Runner, workspace: Path) -> None:
        result = call(atf, workspace, "json/query", data="Looks fine to me.", query=".")
        assert result.code != 0
        assert "not JSON" in result.err

    def test_a_skipped_step_is_named_as_one(self, atf: Runner, workspace: Path) -> None:
        """`(not run)` is what a step that did not run renders as, and "not JSON" would send
        someone to the transform rather than to the branch that never ran."""
        result = call(atf, workspace, "json/query", data="(not run)", query=".")
        assert result.code != 0
        assert "did not run" in result.err


class TestCsvToJson:
    def test_a_quoted_field_holds_the_delimiter_a_line_break_and_a_doubled_quote(
        self, atf: Runner, workspace: Path
    ) -> None:
        csv = 'id,note\n1,"a,b"\n2,"one\ntwo"\n3,"say ""hi"""\n'
        rows = json.loads(call(atf, workspace, "csv/to_json", data=csv).out)
        assert [row["note"] for row in rows] == ["a,b", "one\ntwo", 'say "hi"']

    def test_every_value_is_a_string(self, atf: Runner, workspace: Path) -> None:
        """A CSV field has no type, and a column of postcodes that lost its leading zeros is
        a bug that surfaces a long way from here."""
        rows = json.loads(call(atf, workspace, "csv/to_json", data="code\n007\n").out)
        assert rows == [{"code": "007"}]

    def test_header_false_really_turns_the_header_off(self, atf: Runner, workspace: Path) -> None:
        """jq's `//` treats false as absent, so `.header // true` answers true for
        `header: false` and the parameter would silently do nothing."""
        rows = json.loads(call(atf, workspace, "csv/to_json", data="a,b\nc,d\n", header=False).out)
        assert rows == [["a", "b"], ["c", "d"]]

    def test_a_tab_delimited_file(self, atf: Runner, workspace: Path) -> None:
        rows = json.loads(
            call(atf, workspace, "csv/to_json", data="a\tb\n1\t2\n", delimiter="\t").out
        )
        assert rows == [{"a": "1", "b": "2"}]

    def test_crlf_endings_and_a_byte_order_mark(self, atf: Runner, workspace: Path) -> None:
        """Both come off anything exported from Excel, and both are invisible in a diff."""
        rows = json.loads(call(atf, workspace, "csv/to_json", data="﻿id,name\r\n1,pen\r\n").out)
        assert rows == [{"id": "1", "name": "pen"}]

    def test_a_blank_line_is_not_a_row(self, atf: Runner, workspace: Path) -> None:
        rows = json.loads(call(atf, workspace, "csv/to_json", data="id\n1\n\n2\n").out)
        assert rows == [{"id": "1"}, {"id": "2"}]

    def test_a_file_holding_only_a_header_is_an_empty_array(
        self, atf: Runner, workspace: Path
    ) -> None:
        """A read that found no rows is a successful read, the way a search that found
        nothing is a successful search."""
        result = call(atf, workspace, "csv/to_json", data="id,name\n")
        assert result.code == 0
        assert json.loads(result.out) == []

    def test_a_ragged_row_is_refused_and_names_the_line(self, atf: Runner, workspace: Path) -> None:
        """Padding it with nulls would answer a question the file cannot answer."""
        result = call(atf, workspace, "csv/to_json", data="id,name\n1,pen\n2\n")
        assert result.code != 0
        assert "line 3" in result.err

    def test_a_column_named_twice_is_refused(self, atf: Runner, workspace: Path) -> None:
        result = call(atf, workspace, "csv/to_json", data="id,id\n1,2\n")
        assert result.code != 0
        assert "twice" in result.err

    def test_a_quote_that_is_never_closed_is_refused(self, atf: Runner, workspace: Path) -> None:
        result = call(atf, workspace, "csv/to_json", data='id,name\n1,"open\n')
        assert result.code != 0
        assert "line 2" in result.err

    def test_nothing_is_truncated(self, atf: Runner, workspace: Path) -> None:
        """Every other reader that ships bounds its output. Half a JSON document is not a
        JSON document, so this one does not, and `path` is what keeps `read_file`'s own
        limit out of the way."""
        rows = "\n".join(f"{n},row {n}" for n in range(1, 1001))
        (workspace / "big.csv").write_text(f"id,name\n{rows}\n")
        parsed = json.loads(call(atf, workspace, "csv/to_json", path="big.csv").out)
        assert len(parsed) == 1000
        assert parsed[-1] == {"id": "1000", "name": "row 1000"}


class TestJsonToCsv:
    def test_it_quotes_only_the_cells_that_need_it(self, atf: Runner, workspace: Path) -> None:
        data = json.dumps([{"a": "plain", "b": "x,y", "c": 'say "hi"'}])
        lines = call(atf, workspace, "json/to_csv", data=data).out.splitlines()
        assert lines[1] == 'plain,"x,y","say ""hi"""'

    def test_null_is_an_empty_cell_and_a_number_is_itself(
        self, atf: Runner, workspace: Path
    ) -> None:
        data = json.dumps([{"a": None, "b": 3.5, "c": True}])
        lines = call(atf, workspace, "json/to_csv", data=data).out.splitlines()
        assert lines[1] == ",3.5,true"

    def test_a_column_only_a_later_row_carries_is_refused(
        self, atf: Runner, workspace: Path
    ) -> None:
        """The alternative is a file missing a column nobody noticed was there."""
        data = json.dumps([{"a": 1}, {"a": 2, "notes": "late"}])
        result = call(atf, workspace, "json/to_csv", data=data)
        assert result.code != 0
        assert "notes" in result.err and "columns" in result.err

    def test_naming_the_columns_lets_a_row_leave_one_out(
        self, atf: Runner, workspace: Path
    ) -> None:
        data = json.dumps([{"a": 1}, {"a": 2, "b": 9}])
        result = call(atf, workspace, "json/to_csv", data=data, columns=["a", "b"])
        assert result.code == 0
        assert result.out.splitlines() == ["a,b", "1,", "2,9"]

    def test_a_nested_value_is_refused(self, atf: Runner, workspace: Path) -> None:
        """CSV has one level, so an array in a cell can only be some rendering of it, and
        picking the rendering is not this tool's decision."""
        result = call(atf, workspace, "json/to_csv", data=json.dumps([{"a": [1, 2]}]))
        assert result.code != 0
        assert "cannot carry" in result.err

    def test_an_empty_array_needs_the_columns_named(self, atf: Runner, workspace: Path) -> None:
        assert call(atf, workspace, "json/to_csv", data="[]").code != 0
        named = call(atf, workspace, "json/to_csv", data="[]", columns=["a", "b"])
        assert named.code == 0
        assert named.out.strip() == "a,b"

    def test_a_round_trip_is_byte_for_byte(self, atf: Runner, workspace: Path) -> None:
        """The two parsers have to agree, and the awkward cases are exactly the ones a
        simpler CSV reader gets wrong."""
        original = 'id,note\n1,"a,b"\n2,"say ""hi"""\n3,"one\ntwo"\n'
        (workspace / "in.csv").write_text(original)
        result = run_flow(
            atf,
            workspace,
            [
                {
                    "id": "read",
                    "tool": "arctic/data/csv/to_json",
                    "input": {"path": "in.csv"},
                    "push": ["write"],
                },
                {
                    "id": "write",
                    "tool": "arctic/data/json/to_csv",
                    "input": {"data": "{{ steps.read.text }}"},
                },
            ],
            "{{ steps.write.text }}",
        )
        assert result.out == original


class TestToMarkdown:
    def test_an_array_of_objects_is_a_table(self, atf: Runner, workspace: Path) -> None:
        data = json.dumps([{"name": "lint", "state": "passed"}])
        lines = call(atf, workspace, "json/to_markdown", data=data).out.splitlines()
        assert lines == ["| name | state |", "| --- | --- |", "| lint | passed |"]

    def test_an_object_is_its_fields_down_the_page(self, atf: Runner, workspace: Path) -> None:
        data = json.dumps({"state": "open", "mergeable": None})
        out = call(atf, workspace, "json/to_markdown", data=data).out
        assert "| state | open |" in out
        assert "| mergeable |  |" in out

    def test_an_array_of_values_is_a_list(self, atf: Runner, workspace: Path) -> None:
        out = call(atf, workspace, "json/to_markdown", data='["lint","tests"]').out
        assert out.splitlines() == ["- lint", "- tests"]

    def test_a_pipe_and_a_line_break_stay_inside_the_row(
        self, atf: Runner, workspace: Path
    ) -> None:
        """A row is one line, and a bare pipe would end the cell early."""
        data = json.dumps([{"a": "x|y", "b": "one\ntwo"}])
        lines = call(atf, workspace, "json/to_markdown", data=data).out.splitlines()
        assert lines[2] == r"| x\|y | one<br>two |"
        assert len(lines) == 3

    def test_the_columns_choose_and_order_the_fields(self, atf: Runner, workspace: Path) -> None:
        data = json.dumps([{"id": 1, "name": "pen", "extra": "x"}])
        lines = call(atf, workspace, "json/to_markdown", data=data, columns=["name", "id"]).out
        assert lines.splitlines()[0] == "| name | id |"

    def test_an_empty_array_is_a_header_when_the_columns_are_named(
        self, atf: Runner, workspace: Path
    ) -> None:
        result = call(atf, workspace, "json/to_markdown", data="[]", columns=["a"])
        assert result.code == 0
        assert result.out.splitlines() == ["| a |", "| --- |"]

    @pytest.mark.parametrize("data", ["7", '[{"a":1},"x"]'], ids=["a scalar", "a mixed array"])
    def test_a_shape_with_no_one_rendering_is_refused(
        self, atf: Runner, workspace: Path, data: str
    ) -> None:
        assert call(atf, workspace, "json/to_markdown", data=data).code != 0


class TestMerge:
    def test_named_keeps_each_part_under_its_own_name(self, atf: Runner, workspace: Path) -> None:
        result = call(
            atf,
            workspace,
            "json/merge",
            part_review='{"verdict":"pass"}',
            part_tests='{"failed":0}',
        )
        assert json.loads(result.out) == {"review": {"verdict": "pass"}, "tests": {"failed": 0}}

    @pytest.mark.parametrize(
        ("strategy", "expected"),
        [("shallow", {"o": {"y": 2}}), ("deep", {"o": {"x": 1, "y": 2}})],
    )
    def test_shallow_replaces_a_nested_object_where_deep_merges_it(
        self, atf: Runner, workspace: Path, strategy: str, expected: dict[str, Any]
    ) -> None:
        result = call(
            atf,
            workspace,
            "json/merge",
            part_a='{"o":{"x":1}}',
            part_b='{"o":{"y":2}}',
            strategy=strategy,
        )
        assert json.loads(result.out) == expected

    def test_the_parts_keep_the_order_they_were_written_in(
        self, atf: Runner, workspace: Path
    ) -> None:
        """So a collision goes to the part written last. Nothing sorts them."""
        result = call(
            atf, workspace, "json/merge", part_b='{"n":1}', part_a='{"n":9}', strategy="shallow"
        )
        assert json.loads(result.out) == {"n": 9}

    def test_named_carries_a_part_that_is_not_an_object(self, atf: Runner, workspace: Path) -> None:
        result = call(atf, workspace, "json/merge", part_count="7")
        assert json.loads(result.out) == {"count": 7}

    def test_merging_fields_needs_every_part_to_be_an_object(
        self, atf: Runner, workspace: Path
    ) -> None:
        result = call(atf, workspace, "json/merge", part_count="7", strategy="deep")
        assert result.code != 0
        assert "count" in result.err and "named" in result.err

    def test_a_part_that_did_not_run_is_named_as_one(self, atf: Runner, workspace: Path) -> None:
        """A join whose other branch was skipped is the failure this tool meets most, and
        "not JSON" would point at the wrong step."""
        result = call(atf, workspace, "json/merge", part_review="(not run)", part_tests="{}")
        assert result.code != 0
        assert "review" in result.err and "did not run" in result.err

    def test_a_part_that_is_prose_is_refused(self, atf: Runner, workspace: Path) -> None:
        result = call(atf, workspace, "json/merge", part_review="Looks fine to me.")
        assert result.code != 0
        assert "review" in result.err and "not JSON" in result.err

    def test_a_key_that_is_not_a_part_is_refused(self, atf: Runner, workspace: Path) -> None:
        """`unevaluatedProperties` in the spec, because the names are a pattern rather than a
        list. Spelled `additionalProperties`, lint would refuse every part instead."""
        result = call(atf, workspace, "json/merge", parts='{"a":1}')
        assert result.code != 0
        assert "parts" in result.err


class TestThePackTouchesNothing:
    """The pack's own promise, which is what makes every tool in it grantable to an agent."""

    def test_no_tool_writes_or_reaches_the_network(self) -> None:
        for name, spec in specs().items():
            assert spec["permissions"]["filesystem"] in ("none", "read"), name
            assert spec["permissions"]["network"] is False, name

    def test_no_tool_declares_a_secret(self) -> None:
        """`validate` refuses to grant a credentialled tool to an agent, so one declared
        secret anywhere here would take the promise away from that tool."""
        assert [name for name, spec in specs().items() if spec.get("secrets")] == []

    def test_an_agent_can_be_granted_one_without_saying_it_is_unattended(
        self, atf: Runner, workspace: Path
    ) -> None:
        """Which is the claim the two tests above add up to, made through the engine that
        enforces it rather than about the files."""
        components.write_agent(workspace, "reader", tools=[A_PACK_TOOL])
        components.write_flow(
            workspace,
            "granted",
            {
                "flow": "granted",
                "start": "ask",
                "steps": [{"id": "ask", "agent": "reader", "prompt": "Read it."}],
                "output": {"template": "{{ steps.ask.text }}"},
            },
        )
        result = atf("--workspace", str(workspace), "lint", "granted")
        assert result.code == 0, result.err


class TestAFlowThatUsesThePack:
    def test_a_query_drives_a_switch_and_a_join_merges_both_branches(
        self, atf: Runner, workspace: Path
    ) -> None:
        """The castle rather than the blocks: a file read from disk, a count deciding the
        branch, and a join whose skipped side is guarded by a conditional."""
        (workspace / "checks.csv").write_text("name,state\nlint,passed\ntests,failed\n")
        steps = [
            {
                "id": "rows",
                "tool": "arctic/data/csv/to_json",
                "input": {"path": "checks.csv"},
                "push": ["failing"],
            },
            {
                "id": "failing",
                "tool": "arctic/data/json/query",
                "input": {
                    "data": "{{ steps.rows.text }}",
                    "query": '[.[] | select(.state == "failed")] | length',
                },
                "switch": "{{ this.text }}",
                "cases": {"0": ["summary"]},
                "default": ["names", "summary"],
            },
            {
                "id": "names",
                "tool": "arctic/data/json/query",
                "input": {
                    "data": "{{ steps.rows.text }}",
                    "query": '[.[] | select(.state == "failed") | .name]',
                },
                "push": ["summary"],
            },
            {
                "id": "summary",
                "tool": "arctic/data/json/merge",
                "input": {
                    "part_failing": "{{ steps.failing.text }}",
                    "part_names": (
                        "{% if steps.names %}{{ steps.names.text }}{% else %}[]{% endif %}"
                    ),
                },
            },
        ]
        result = run_flow(atf, workspace, steps, "{{ steps.summary.text }}")
        assert result.code == 0, result.err
        assert json.loads(result.out) == {"failing": 1, "names": ["tests"]}

    def test_the_same_flow_when_the_branch_is_not_taken(self, atf: Runner, workspace: Path) -> None:
        """The guard is the half that only fires here: `names` never runs, and the part it
        would have filled is the conditional's other branch."""
        (workspace / "checks.csv").write_text("name,state\nlint,passed\n")
        steps = [
            {
                "id": "rows",
                "tool": "arctic/data/csv/to_json",
                "input": {"path": "checks.csv"},
                "push": ["failing"],
            },
            {
                "id": "failing",
                "tool": "arctic/data/json/query",
                "input": {
                    "data": "{{ steps.rows.text }}",
                    "query": '[.[] | select(.state == "failed")] | length',
                },
                "switch": "{{ this.text }}",
                "cases": {"0": ["summary"]},
                "default": ["names", "summary"],
            },
            {
                "id": "names",
                "tool": "arctic/data/json/query",
                "input": {"data": "{{ steps.rows.text }}", "query": "[.[].name]"},
                "push": ["summary"],
            },
            {
                "id": "summary",
                "tool": "arctic/data/json/merge",
                "input": {
                    "part_failing": "{{ steps.failing.text }}",
                    "part_names": (
                        "{% if steps.names %}{{ steps.names.text }}{% else %}[]{% endif %}"
                    ),
                },
            },
        ]
        result = run_flow(atf, workspace, steps, "{{ steps.summary.text }}")
        assert result.code == 0, result.err
        assert json.loads(result.out) == {"failing": 0, "names": []}
