"""The shipped `read_file`, run the way a flow runs it.

Here rather than in `tests/unit` for the same reason `write_file`'s tests are: the script
needs `jq`, `awk` and `realpath`, and a unit test must need nothing beyond a POSIX shell
and Python.

What carries the weight is the shape of the output, because that is what gets templated
into a prompt or a file. One path has to stay byte-exact, several have to say which is
which, and a call naming one bad path must produce no output at all rather than the files
it did manage to read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from .conftest import Runner, requires


@pytest.fixture(autouse=True)
def needs_shell_tools() -> None:
    requires("jq", "awk", "realpath")


def read(atf: Runner, project: Path, **input_values: Any) -> Any:
    """A one-step flow whose only job is to run the tool, with `input` written literally.

    The input goes in the flow rather than through `--input` because `path` may be a list,
    and a CLI `KEY=VALUE` can only carry a string.
    """
    definition = {
        "flow": "read",
        "start": "get",
        "steps": [{"id": "get", "tool": "arctic/read_file", "input": input_values}],
        # Named, so stdout is the tool's own output rather than the results mapping.
        "output": {"template": "{{ steps.get.text }}"},
    }
    (project / "flows").mkdir(exist_ok=True)
    (project / "flows" / "read.yaml").write_text(json.dumps(definition))
    return atf("--workspace", str(project), "run", "read")


@pytest.fixture
def files(project: Path) -> Path:
    (project / "a.txt").write_text("alpha one\nalpha two\n")
    (project / "b.txt").write_text("beta\n")
    (project / "long.txt").write_text("".join(f"line {n}\n" for n in range(1, 9)))
    return project


class TestOnePath:
    def test_the_contents_come_back_verbatim(self, atf: Runner, files: Path) -> None:
        assert read(atf, files, path="a.txt").out == "alpha one\nalpha two\n"

    def test_a_list_of_one_is_the_same_bytes_as_the_bare_path(
        self, atf: Runner, files: Path
    ) -> None:
        """The count decides the shape, not whether an array was used, so a caller building
        the list programmatically can still template a single read straight into a prompt."""
        assert read(atf, files, path=["a.txt"]).out == read(atf, files, path="a.txt").out

    def test_a_long_file_is_truncated_and_says_so(self, atf: Runner, files: Path) -> None:
        result = read(atf, files, path="long.txt", max_lines=3)
        assert "line 3" in result.out
        assert "line 4" not in result.out
        assert "showing 3 of 8 lines" in result.out


class TestSeveralPaths:
    def test_each_file_is_headed_with_the_path_that_was_asked_for(
        self, atf: Runner, files: Path
    ) -> None:
        out = read(atf, files, path=["a.txt", "b.txt"]).out
        assert "==> a.txt <==" in out
        assert "==> b.txt <==" in out

    def test_the_order_asked_for_is_the_order_returned(self, atf: Runner, files: Path) -> None:
        out = read(atf, files, path=["b.txt", "a.txt"]).out
        assert out.index("==> b.txt <==") < out.index("==> a.txt <==")

    def test_every_file_is_there(self, atf: Runner, files: Path) -> None:
        out = read(atf, files, path=["a.txt", "b.txt"]).out
        assert "alpha one" in out
        assert "beta" in out

    def test_max_lines_applies_per_file(self, atf: Runner, files: Path) -> None:
        """Otherwise a long first file would spend the whole budget and the rest would
        arrive empty, which reads as files that have nothing in them."""
        out = read(atf, files, path=["long.txt", "b.txt"], max_lines=2).out
        assert "long.txt truncated" in out
        assert "beta" in out

    def test_the_truncation_notice_names_the_file(self, atf: Runner, files: Path) -> None:
        out = read(atf, files, path=["long.txt", "b.txt"], max_lines=2).out
        assert "[read_file] long.txt truncated" in out


class TestOneBadPathFailsTheWholeCall:
    def test_nothing_is_written_when_a_later_path_is_missing(
        self, atf: Runner, files: Path
    ) -> None:
        """A partial answer with an error after it is the failure worth preventing: the
        good files would read as the complete result."""
        result = read(atf, files, path=["a.txt", "ghost.txt"])
        assert result.code != 0
        assert "alpha one" not in result.out

    def test_the_missing_path_is_the_one_named(self, atf: Runner, files: Path) -> None:
        result = read(atf, files, path=["a.txt", "ghost.txt"])
        assert "ghost.txt" in result.err

    def test_a_path_leaving_the_workspace_fails_the_call(self, atf: Runner, files: Path) -> None:
        result = read(atf, files, path=["a.txt", "/etc/passwd"])
        assert result.code != 0
        assert "outside the workspace root" in result.err
        assert "alpha one" not in result.out

    def test_a_directory_among_the_paths_is_refused(self, atf: Runner, files: Path) -> None:
        (files / "notes").mkdir()
        result = read(atf, files, path=["a.txt", "notes"])
        assert result.code != 0
        assert "not a regular file" in result.err


class TestRefusals:
    def test_an_empty_list_is_refused_by_the_schema(self, atf: Runner, files: Path) -> None:
        """`minItems: 1`, so `lint` catches it without the tool running at all."""
        result = read(atf, files, path=[])
        assert result.code != 0

    def test_a_path_that_is_not_a_string_is_refused(self, atf: Runner, files: Path) -> None:
        result = read(atf, files, path=["a.txt", 7])
        assert result.code != 0
