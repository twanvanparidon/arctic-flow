"""The shipped `write_file`, run the way a flow runs it.

Here rather than in `tests/unit` because the script needs `jq` and `realpath`, and a unit
test must need nothing beyond a POSIX shell and Python. `requires()` skips by name when
they are absent, since a machine without `jq` is an environment and not a defect.

The containment cases carry the weight. `read_file` canonicalises with `realpath -e`, which
refuses a file that does not exist yet, so this uses `-m` instead. The symlink cases are
what separate that from the wrong fix: canonicalising only the parent directory would let
every one of them through.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import Runner, requires

WRITE = {"id": "put", "tool": "write_file", "input": {"path": "{{ inputs.path }}"}}


@pytest.fixture(autouse=True)
def needs_shell_tools() -> None:
    requires("jq", "realpath")


def flow_writing(content: str, **extra: object) -> dict[str, object]:
    """A one-step flow whose only job is to run the tool."""
    step = {**WRITE, "input": {**WRITE["input"], "content": content, **extra}}
    return {
        "flow": "put_file",
        "start": "put",
        "inputs": {"path": {"type": "string", "required": True}},
        "steps": [step],
        # Named, so stdout is the tool's own line rather than the whole results mapping.
        "output": {"template": "{{ steps.put.text }}"},
    }


def write(atf: Runner, project: Path, definition: dict[str, object], path: str) -> object:
    (project / "flows").mkdir(exist_ok=True)
    (project / "flows" / "put_file.yaml").write_text(json.dumps(definition))
    return atf("--workspace", str(project), "run", "put_file", "--input", f"path={path}")


class TestWriting:
    def test_the_content_lands_byte_for_byte(self, atf: Runner, project: Path) -> None:
        """No newline is added: what the caller asked to write is what is on disk."""
        write(atf, project, flow_writing("one\ntwo"), "out.md")
        assert (project / "out.md").read_bytes() == b"one\ntwo"

    def test_an_empty_string_is_a_file_worth_writing(self, atf: Runner, project: Path) -> None:
        """`has("content")` rather than `//`, which treats an empty string as absent."""
        result = write(atf, project, flow_writing(""), "empty.md")
        assert result.code == 0
        assert (project / "empty.md").read_bytes() == b""

    def test_it_says_how_much_it_wrote(self, atf: Runner, project: Path) -> None:
        result = write(atf, project, flow_writing("hello"), "out.md")
        assert result.out.rstrip("\n") == "wrote 5 bytes to out.md"

    def test_its_line_carries_no_newline_of_its_own(self, atf: Runner, project: Path) -> None:
        """A single-value output gets templated mid-line, and a stray newline breaks it."""
        definition = flow_writing("hello")
        definition["output"] = {"template": "[{{ steps.put.text }}]"}
        result = write(atf, project, definition, "out.md")
        assert result.out.rstrip("\n") == "[wrote 5 bytes to out.md]"

    def test_it_writes_into_a_directory_that_is_already_there(
        self, atf: Runner, project: Path
    ) -> None:
        (project / "notes").mkdir()
        write(atf, project, flow_writing("x"), "notes/deep.md")
        assert (project / "notes" / "deep.md").read_text() == "x"


class TestNotClobbering:
    def test_an_existing_file_is_refused_by_default(self, atf: Runner, project: Path) -> None:
        (project / "out.md").write_text("keep me")
        result = write(atf, project, flow_writing("replace"), "out.md")
        assert result.code != 0
        assert (project / "out.md").read_text() == "keep me"

    def test_the_refusal_says_how_to_proceed(self, atf: Runner, project: Path) -> None:
        (project / "out.md").write_text("keep me")
        result = write(atf, project, flow_writing("replace"), "out.md")
        assert "overwrite" in result.err

    def test_overwrite_replaces_it(self, atf: Runner, project: Path) -> None:
        (project / "out.md").write_text("old")
        write(atf, project, flow_writing("new", overwrite=True), "out.md")
        assert (project / "out.md").read_text() == "new"

    def test_overwriting_keeps_the_files_mode(self, atf: Runner, project: Path) -> None:
        """Why it truncates in place: a rename would replace the inode, and the mode with it."""
        target = project / "out.md"
        target.write_text("old")
        target.chmod(0o640)
        write(atf, project, flow_writing("new", overwrite=True), "out.md")
        assert target.stat().st_mode & 0o777 == 0o640


class TestContainment:
    @pytest.mark.parametrize("path", ["../escaped.md", "/tmp/escaped.md", "notes/../../escaped.md"])
    def test_a_path_that_leaves_the_workspace_is_refused(
        self, atf: Runner, project: Path, path: str
    ) -> None:
        (project / "notes").mkdir(exist_ok=True)
        result = write(atf, project, flow_writing("x"), path)
        assert result.code != 0
        assert "outside the workspace root" in result.err

    def test_a_symlink_to_something_outside_is_refused(
        self, atf: Runner, project: Path, tmp_path: Path
    ) -> None:
        """The case parent-only canonicalisation lets through: the parent is inside and
        the target is not."""
        outside = tmp_path / "target.md"
        outside.write_text("do not touch")
        (project / "escape").symlink_to(outside)
        result = write(atf, project, flow_writing("x", overwrite=True), "escape")
        assert result.code != 0
        assert outside.read_text() == "do not touch"

    def test_a_dangling_symlink_out_of_the_workspace_is_refused(
        self, atf: Runner, project: Path, tmp_path: Path
    ) -> None:
        """`realpath -m` follows one whose target does not exist, which is the point."""
        (project / "escape").symlink_to(tmp_path / "absent.md")
        result = write(atf, project, flow_writing("x"), "escape")
        assert result.code != 0
        assert not (tmp_path / "absent.md").exists()

    def test_lexical_collapse_through_a_missing_component_is_refused(
        self, atf: Runner, project: Path
    ) -> None:
        """Only reads as an escape once -m has folded it, so the check runs on the result."""
        result = write(atf, project, flow_writing("x"), "ghost/../../escaped.md")
        assert result.code != 0
        assert "outside the workspace root" in result.err


class TestRefusals:
    def test_a_missing_directory_is_reported_rather_than_created(
        self, atf: Runner, project: Path
    ) -> None:
        """A typo in a path should be reported as a typo, not leave a directory behind."""
        result = write(atf, project, flow_writing("x"), "absent/out.md")
        assert result.code != 0
        assert not (project / "absent").exists()

    def test_a_directory_in_the_way_is_refused(self, atf: Runner, project: Path) -> None:
        (project / "notes").mkdir()
        result = write(atf, project, flow_writing("x", overwrite=True), "notes")
        assert result.code != 0
        assert (project / "notes").is_dir()
