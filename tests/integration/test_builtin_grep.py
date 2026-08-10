"""The shipped `grep`, run the way a flow runs it.

Two claims here are the reason the tool is written the way it is, and neither is visible
from reading the script.

**A batch ending in one file must still be prefixed.** `grep` given a single file prints
matches without the filename, so `find -exec grep {} +` would emit a differently shaped
line whenever a batch happened to hold one. `/dev/null` is passed as a second file to force
the prefix on, and `test_a_single_file_is_still_prefixed` is what says so.

**Finding nothing exits 0.** The engine fails a step on any non-zero exit, so a search whose
honest answer is "nowhere" has to succeed or no flow could ask the question.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from .conftest import Runner, requires


@pytest.fixture(autouse=True)
def needs_shell_tools() -> None:
    requires("jq", "find", "grep", "sed", "realpath")


def search(atf: Runner, project: Path, **input_values: Any) -> Any:
    definition = {
        "flow": "find",
        "start": "look",
        "steps": [{"id": "look", "tool": "arctic/grep", "input": input_values}],
        "output": {"template": "{{ steps.look.text }}"},
    }
    (project / "flows").mkdir(exist_ok=True)
    (project / "flows" / "find.yaml").write_text(json.dumps(definition))
    return atf("--workspace", str(project), "run", "find")


@pytest.fixture
def tree(project: Path) -> Path:
    (project / "src").mkdir()
    (project / "src" / "a.py").write_text("def alpha():\n    return 1\n\ndef beta():\n    pass\n")
    (project / "src" / "b.py").write_text("class Alpha:\n    pass\n")
    (project / "notes.md").write_text("alpha is documented\n")
    return project


class TestMatching:
    def test_a_match_is_path_line_text(self, atf: Runner, tree: Path) -> None:
        out = search(atf, tree, pattern="def alpha").out
        assert "src/a.py:1:def alpha():" in out

    def test_the_path_is_relative_to_the_workspace(self, atf: Runner, tree: Path) -> None:
        """The `./` that `find .` produces is stripped, so a result reads like a path a
        flow could hand straight to read_file."""
        out = search(atf, tree, pattern="def alpha").out
        assert "./src/a.py" not in out

    def test_a_single_file_is_still_prefixed(self, atf: Runner, tree: Path) -> None:
        """Why /dev/null is handed to grep as a second file. Without it this line would
        arrive as bare text and nothing downstream could tell which file it came from."""
        out = search(atf, tree, path="src/a.py", pattern="alpha").out
        assert "src/a.py:1:def alpha():" in out

    def test_the_pattern_is_an_extended_regex(self, atf: Runner, tree: Path) -> None:
        """-E, so alternation works without backslashes. -F is the opt-out."""
        out = search(atf, tree, pattern="def (alpha|beta)").out
        assert "a.py:1:" in out
        assert "a.py:4:" in out

    def test_fixed_treats_the_pattern_as_text(self, atf: Runner, tree: Path) -> None:
        (tree / "literal.txt").write_text("a.b\naxb\n")
        out = search(atf, tree, pattern="a.b", fixed=True).out
        assert "literal.txt:1:a.b" in out
        assert "axb" not in out

    def test_ignore_case_matches_either_way(self, atf: Runner, tree: Path) -> None:
        out = search(atf, tree, pattern="alpha", ignore_case=True).out
        assert "b.py:1:class Alpha:" in out


class TestNarrowingTheSearch:
    def test_glob_matches_the_file_name(self, atf: Runner, tree: Path) -> None:
        out = search(atf, tree, pattern="alpha", glob="*.py").out
        assert "a.py" in out
        assert "notes.md" not in out

    def test_path_bounds_it_to_a_subtree(self, atf: Runner, tree: Path) -> None:
        out = search(atf, tree, pattern="alpha", path="src").out
        assert "a.py" in out
        assert "notes.md" not in out


class TestFindingNothing:
    """Scoped to `src`, because the flow this runs from is itself a file in the workspace
    and the pattern is written into it. Searching `.` for anything therefore always finds
    at least the flow, which is correct and useless for asking about an empty result."""

    def test_it_succeeds(self, atf: Runner, tree: Path) -> None:
        """grep itself exits 1 for this. A step would fail on that, so the tool does not."""
        assert search(atf, tree, pattern="handle_retry", path="src").code == 0

    def test_it_says_so_rather_than_answering_with_nothing(self, atf: Runner, tree: Path) -> None:
        """An empty result reaching a model reads as a tool that broke."""
        result = search(atf, tree, pattern="handle_retry", path="src")
        assert "no matches for handle_retry" in result.out


class TestTruncation:
    @pytest.fixture
    def many(self, tree: Path) -> Path:
        (tree / "many.txt").write_text("".join(f"hit {n}\n" for n in range(1, 11)))
        return tree

    def test_it_stops_at_max_matches(self, atf: Runner, many: Path) -> None:
        out = search(atf, many, pattern="hit", path="many.txt", max_matches=3).out
        assert len([line for line in out.splitlines() if ":hit " in line]) == 3

    def test_it_says_it_truncated(self, atf: Runner, many: Path) -> None:
        result = search(atf, many, pattern="hit", path="many.txt", max_matches=3)
        assert "truncated at 3 matches" in result.out

    def test_landing_exactly_on_the_cap_is_not_truncation(self, atf: Runner, tree: Path) -> None:
        """Why one line past the cap is read: otherwise the notice appears on a complete
        result, and a model raises max_matches for nothing."""
        (tree / "many.txt").write_text("".join(f"hit {n}\n" for n in range(1, 4)))
        out = search(atf, tree, pattern="hit", path="many.txt", max_matches=3).out
        assert "truncated" not in out


class TestContainment:
    @pytest.mark.parametrize("path", ["/etc", "..", "src/../.."])
    def test_a_search_path_leaving_the_workspace_is_refused(
        self, atf: Runner, tree: Path, path: str
    ) -> None:
        result = search(atf, tree, pattern="root", path=path)
        assert result.code != 0
        assert "outside the workspace root" in result.err

    def test_the_workspace_root_itself_is_allowed(self, atf: Runner, tree: Path) -> None:
        """Unlike read_file, searching the whole workspace is this tool's default."""
        assert search(atf, tree, pattern="alpha", path=".").code == 0

    def test_a_search_path_that_does_not_exist_is_reported(self, atf: Runner, tree: Path) -> None:
        result = search(atf, tree, pattern="x", path="absent")
        assert result.code != 0
        assert "absent" in result.err
