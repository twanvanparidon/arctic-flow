"""The shipped `glob`, run the way a flow runs it.

Three claims are the reason the tool is written the way it is.

**A slash changes what is matched.** Without one the pattern is compared to the file name,
with one to the whole path, and `find`'s `-path` lets `*` cross `/`. That is what makes one
pattern reach any depth without a `**` of its own.

**The order is sorted.** Not because anyone reads it in order, but because truncation has
to keep the same paths every run. Directory order would make a capped result depend on the
filesystem.

**Matching nothing exits 0.** The engine fails a step on any non-zero exit, so a flow could
not otherwise ask a question whose answer is "none".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from .conftest import Runner, requires


@pytest.fixture(autouse=True)
def needs_shell_tools() -> None:
    requires("jq", "find", "sed", "sort", "realpath")


def find_paths(atf: Runner, project: Path, **input_values: Any) -> Any:
    definition = {
        "flow": "list",
        "start": "look",
        "steps": [{"id": "look", "tool": "arctic/glob", "input": input_values}],
        "output": {"template": "{{ steps.look.text }}"},
    }
    (project / "flows").mkdir(exist_ok=True)
    (project / "flows" / "list.yaml").write_text(json.dumps(definition))
    return atf("--workspace", str(project), "run", "list")


def lines(result: Any) -> list[str]:
    return result.out.splitlines()


@pytest.fixture
def tree(project: Path) -> Path:
    (project / "src" / "deep").mkdir(parents=True)
    for name in ("src/a.py", "src/b.py", "src/deep/c.py", "src/deep/c_test.py", "notes.md"):
        (project / name).touch()
    return project


class TestMatchingByName:
    def test_a_pattern_without_a_slash_matches_at_any_depth(self, atf: Runner, tree: Path) -> None:
        found = lines(find_paths(atf, tree, pattern="*.py"))
        assert "src/a.py" in found
        assert "src/deep/c.py" in found

    def test_it_matches_the_name_rather_than_the_path(self, atf: Runner, tree: Path) -> None:
        assert lines(find_paths(atf, tree, pattern="c_test.py")) == ["src/deep/c_test.py"]

    def test_a_pattern_matching_nothing_of_that_name_is_empty(
        self, atf: Runner, tree: Path
    ) -> None:
        assert "no matches for *.rs" in find_paths(atf, tree, pattern="*.rs").out


class TestMatchingByPath:
    def test_a_slash_makes_it_a_path_pattern(self, atf: Runner, tree: Path) -> None:
        found = lines(find_paths(atf, tree, pattern="src/*.py"))
        assert "src/a.py" in found
        assert "notes.md" not in found

    def test_a_star_crosses_directories_in_a_path_pattern(self, atf: Runner, tree: Path) -> None:
        """Which is why there is no `**`: `-path`'s `*` already does that job."""
        assert "src/deep/c.py" in lines(find_paths(atf, tree, pattern="src/*.py"))

    def test_a_path_pattern_is_relative_to_the_search_path(self, atf: Runner, tree: Path) -> None:
        found = lines(find_paths(atf, tree, pattern="deep/*.py", path="src"))
        assert "src/deep/c.py" in found
        assert "src/a.py" not in found


class TestWhatIsListed:
    def test_files_by_default(self, atf: Runner, tree: Path) -> None:
        """`src/deep` is a directory and matches the pattern, so it is the default that
        keeps it out of the result."""
        assert "no matches for deep" in find_paths(atf, tree, pattern="deep").out

    def test_directories_when_asked(self, atf: Runner, tree: Path) -> None:
        found = lines(find_paths(atf, tree, pattern="*", type="dir"))
        assert "src/deep" in found
        assert "src/a.py" not in found

    def test_the_search_root_is_not_one_of_the_results(self, atf: Runner, tree: Path) -> None:
        """Nobody asked about the directory they named; the question is what is in it."""
        assert "." not in lines(find_paths(atf, tree, pattern="*", type="dir"))

    def test_any_lists_both(self, atf: Runner, tree: Path) -> None:
        found = lines(find_paths(atf, tree, pattern="deep*", type="any"))
        assert "src/deep" in found

    def test_a_type_that_is_not_one_of_the_three_is_refused(self, atf: Runner, tree: Path) -> None:
        assert find_paths(atf, tree, pattern="*", type="socket").code != 0


class TestOrdering:
    def test_the_result_is_sorted(self, atf: Runner, tree: Path) -> None:
        found = lines(find_paths(atf, tree, pattern="*.py"))
        assert found == sorted(found)

    def test_two_runs_over_an_unchanged_tree_agree(self, atf: Runner, tree: Path) -> None:
        """What sorting is for: a capped result that shifted between runs would be worse
        than a truncated one, because nothing would say it had."""
        assert (
            find_paths(atf, tree, pattern="*.py").out == find_paths(atf, tree, pattern="*.py").out
        )


class TestTruncation:
    def test_it_stops_at_max_results(self, atf: Runner, tree: Path) -> None:
        assert len(lines(find_paths(atf, tree, pattern="*.py", max_results=2))) == 3

    def test_it_says_it_truncated(self, atf: Runner, tree: Path) -> None:
        result = find_paths(atf, tree, pattern="*.py", max_results=2)
        assert "truncated at 2 paths" in result.out

    def test_landing_exactly_on_the_cap_is_not_truncation(self, atf: Runner, tree: Path) -> None:
        result = find_paths(atf, tree, pattern="*.py", path="src/deep", max_results=2)
        assert "truncated" not in result.out


class TestContainment:
    @pytest.mark.parametrize("path", ["/etc", "..", "src/../.."])
    def test_a_search_path_leaving_the_workspace_is_refused(
        self, atf: Runner, tree: Path, path: str
    ) -> None:
        result = find_paths(atf, tree, pattern="*", path=path)
        assert result.code != 0
        assert "outside the workspace root" in result.err

    def test_a_search_path_that_does_not_exist_is_reported(self, atf: Runner, tree: Path) -> None:
        result = find_paths(atf, tree, pattern="*", path="absent")
        assert result.code != 0
        assert "absent" in result.err

    def test_a_file_given_as_the_search_path_is_refused(self, atf: Runner, tree: Path) -> None:
        """It searches a directory. A file there is a mistake worth naming rather than an
        empty result to puzzle over."""
        result = find_paths(atf, tree, pattern="*", path="notes.md")
        assert result.code != 0
        assert "not a directory" in result.err
