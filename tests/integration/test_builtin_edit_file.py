"""The shipped `edit_file`, run the way a flow runs it.

Here rather than in `tests/unit` because the script needs `jq`, `realpath` and `cmp`, and a
unit test must need nothing beyond a POSIX shell and Python. `requires()` skips by name when
they are absent, since a machine without `jq` is an environment and not a defect.

Two groups carry the weight. The literal-match cases are what separate `split/1` from
`sub/gsub`: a pattern would match text the caller never named. The untouched-file cases are
the other half, because every refusal here happens on a file that already has contents worth
keeping, so "it refused" is only half the claim.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import Runner, requires

EDIT = {"id": "change", "tool": "arctic/edit_file", "input": {"path": "{{ inputs.path }}"}}


@pytest.fixture(autouse=True)
def needs_shell_tools() -> None:
    requires("jq", "realpath", "cmp")


def flow_editing(old: str, new: str, **extra: object) -> dict[str, object]:
    """A one-step flow whose only job is to run the tool."""
    step = {**EDIT, "input": {**EDIT["input"], "old_string": old, "new_string": new, **extra}}
    return {
        "flow": "edit",
        "start": "change",
        "inputs": {"path": {"type": "string", "required": True}},
        "steps": [step],
        # Named, so stdout is the tool's own line rather than the whole results mapping.
        "output": {"template": "{{ steps.change.text }}"},
    }


def edit(atf: Runner, project: Path, definition: dict[str, object], path: str) -> object:
    (project / "flows").mkdir(exist_ok=True)
    (project / "flows" / "edit.yaml").write_text(json.dumps(definition))
    return atf("--workspace", str(project), "run", "edit", "--input", f"path={path}")


class TestEditing:
    def test_only_the_matched_text_changes(self, atf: Runner, project: Path) -> None:
        (project / "app.py").write_text("first\nTIMEOUT = 30\nlast\n")
        edit(atf, project, flow_editing("TIMEOUT = 30", "TIMEOUT = 120"), "app.py")
        assert (project / "app.py").read_text() == "first\nTIMEOUT = 120\nlast\n"

    def test_no_newline_is_added_to_a_file_that_had_none(self, atf: Runner, project: Path) -> None:
        """-j rather than -r in the jq that writes the result."""
        (project / "app.py").write_bytes(b"a\nb\nc")
        edit(atf, project, flow_editing("b", "B"), "app.py")
        assert (project / "app.py").read_bytes() == b"a\nB\nc"

    def test_an_old_string_spanning_lines_matches(self, atf: Runner, project: Path) -> None:
        (project / "app.py").write_text("one\ntwo\nthree\n")
        edit(atf, project, flow_editing("one\ntwo\n", "ONE\n"), "app.py")
        assert (project / "app.py").read_text() == "ONE\nthree\n"

    def test_an_empty_new_string_deletes_the_match(self, atf: Runner, project: Path) -> None:
        (project / "notes.md").write_text("keep\ndrop\nkeep\n")
        result = edit(atf, project, flow_editing("drop\n", ""), "notes.md")
        assert result.code == 0
        assert (project / "notes.md").read_text() == "keep\nkeep\n"

    def test_it_says_how_many_it_replaced(self, atf: Runner, project: Path) -> None:
        (project / "app.py").write_text("x\n")
        result = edit(atf, project, flow_editing("x", "y"), "app.py")
        assert result.out.rstrip("\n") == "replaced 1 occurrence in app.py"

    def test_its_line_carries_no_newline_of_its_own(self, atf: Runner, project: Path) -> None:
        """A single-value output gets templated mid-line, and a stray newline breaks it."""
        (project / "app.py").write_text("x\n")
        definition = flow_editing("x", "y")
        definition["output"] = {"template": "[{{ steps.change.text }}]"}
        result = edit(atf, project, definition, "app.py")
        assert result.out.rstrip("\n") == "[replaced 1 occurrence in app.py]"

    def test_the_rest_of_a_long_file_is_left_alone(self, atf: Runner, project: Path) -> None:
        """The reason the tool exists: one line changes and the other 500 are not rewritten."""
        lines = [f"line {number}\n" for number in range(500)]
        (project / "big.txt").write_text("".join(lines) + "TARGET\n")
        edit(atf, project, flow_editing("TARGET", "HIT"), "big.txt")
        assert (project / "big.txt").read_text() == "".join(lines) + "HIT\n"


class TestMatchingLiterally:
    """`split/1` splits on a string. `sub` would read old_string as a regular expression."""

    def test_a_regex_metacharacter_matches_only_itself(self, atf: Runner, project: Path) -> None:
        (project / "app.py").write_text("a.c\nabc\n")
        result = edit(atf, project, flow_editing("a.c", "REPLACED"), "app.py")
        assert result.code == 0
        assert (project / "app.py").read_text() == "REPLACED\nabc\n"

    @pytest.mark.parametrize("old", ["cost($9)", "a[0]*2", "^start", "p|q", "back\\slash"])
    def test_a_string_a_pattern_would_choke_on_is_replaced(
        self, atf: Runner, project: Path, old: str
    ) -> None:
        (project / "app.py").write_text(f"before\n{old}\nafter\n")
        result = edit(atf, project, flow_editing(old, "ok"), "app.py")
        assert result.code == 0
        assert (project / "app.py").read_text() == "before\nok\nafter\n"

    def test_a_new_string_is_inserted_and_not_expanded(self, atf: Runner, project: Path) -> None:
        r"""`&` and `\1` mean something to sed and nothing here."""
        (project / "app.py").write_text("value\n")
        edit(atf, project, flow_editing("value", r"& \1 $0"), "app.py")
        assert (project / "app.py").read_text() == "& \\1 $0\n"


class TestUniqueness:
    def test_several_matches_are_refused_by_default(self, atf: Runner, project: Path) -> None:
        (project / "app.py").write_text("x\nx\n")
        result = edit(atf, project, flow_editing("x", "y"), "app.py")
        assert result.code != 0
        assert (project / "app.py").read_text() == "x\nx\n"

    def test_the_refusal_says_how_many_and_how_to_proceed(self, atf: Runner, project: Path) -> None:
        """The count is the fact needed to fix the call, and replace_all is the other way out."""
        (project / "app.py").write_text("x\nx\nx\n")
        result = edit(atf, project, flow_editing("x", "y"), "app.py")
        assert "3 times" in result.err
        assert "replace_all" in result.err

    def test_replace_all_takes_every_one(self, atf: Runner, project: Path) -> None:
        (project / "app.py").write_text("x\nkeep\nx\n")
        result = edit(atf, project, flow_editing("x", "y", replace_all=True), "app.py")
        assert (project / "app.py").read_text() == "y\nkeep\ny\n"
        assert result.out.rstrip("\n") == "replaced 2 occurrences in app.py"

    def test_a_unique_match_needs_no_replace_all(self, atf: Runner, project: Path) -> None:
        (project / "app.py").write_text("one x here\n")
        result = edit(atf, project, flow_editing("x", "y"), "app.py")
        assert result.code == 0


class TestRefusals:
    def test_no_match_is_refused_and_writes_nothing(self, atf: Runner, project: Path) -> None:
        (project / "app.py").write_text("contents\n")
        result = edit(atf, project, flow_editing("absent", "y"), "app.py")
        assert result.code != 0
        assert "old_string" in result.err
        assert (project / "app.py").read_text() == "contents\n"

    def test_a_missing_file_is_reported_rather_than_created(
        self, atf: Runner, project: Path
    ) -> None:
        """Creating one is write_file's job, so a typo is reported as a typo."""
        result = edit(atf, project, flow_editing("a", "b"), "absent.md")
        assert result.code != 0
        assert not (project / "absent.md").exists()

    def test_a_directory_is_refused(self, atf: Runner, project: Path) -> None:
        (project / "notes").mkdir()
        result = edit(atf, project, flow_editing("a", "b"), "notes")
        assert result.code != 0
        assert (project / "notes").is_dir()

    def test_an_identical_old_and_new_string_is_refused(self, atf: Runner, project: Path) -> None:
        """A no-op reported as success would tell a caller its edit landed."""
        (project / "app.py").write_text("same\n")
        result = edit(atf, project, flow_editing("same", "same"), "app.py")
        assert result.code != 0
        assert "identical" in result.err

    def test_an_empty_old_string_is_refused(self, atf: Runner, project: Path) -> None:
        """It names no place in particular, so there is nothing to replace."""
        (project / "app.py").write_text("contents\n")
        result = edit(atf, project, flow_editing("", "x"), "app.py")
        assert result.code != 0
        assert "old_string" in result.err
        assert (project / "app.py").read_text() == "contents\n"

    def test_a_file_whose_bytes_do_not_survive_being_read_as_text_is_refused(
        self, atf: Runner, project: Path
    ) -> None:
        """jq works on decoded text, and what it does with a byte it cannot decode depends on
        the version: 1.8 passes it through, older ones substitute U+FFFD. Either is fine and
        which one is installed is not this test's business. What must hold on both is that the
        file is never left corrupted, so the round trip decides and the two outcomes are the
        edit or an untouched file."""
        original = b"ok \xff\xfe bytes\n"
        (project / "data.bin").write_bytes(original)
        result = edit(atf, project, flow_editing("ok", "OK"), "data.bin")
        if result.code == 0:
            assert (project / "data.bin").read_bytes() == original.replace(b"ok", b"OK", 1)
        else:
            assert (project / "data.bin").read_bytes() == original


class TestContainment:
    @pytest.mark.parametrize("path", ["../escaped.md", "/tmp/escaped.md", "notes/../../escaped.md"])
    def test_a_path_that_leaves_the_workspace_is_refused(
        self, atf: Runner, project: Path, path: str
    ) -> None:
        (project / "notes").mkdir(exist_ok=True)
        result = edit(atf, project, flow_editing("a", "b"), path)
        assert result.code != 0
        assert "outside the workspace root" in result.err

    def test_containment_is_decided_before_existence(self, atf: Runner, project: Path) -> None:
        """`realpath -m`, so an escaping path is reported as one whether or not anything is
        there. `-e` would answer "no such file" and report on a path outside the workspace."""
        result = edit(atf, project, flow_editing("a", "b"), "../absent.md")
        assert "outside the workspace root" in result.err

    def test_a_symlink_to_something_outside_is_refused(
        self, atf: Runner, project: Path, tmp_path: Path
    ) -> None:
        outside = tmp_path / "target.md"
        outside.write_text("do not touch")
        (project / "escape").symlink_to(outside)
        result = edit(atf, project, flow_editing("do not touch", "touched"), "escape")
        assert result.code != 0
        assert outside.read_text() == "do not touch"

    def test_a_dangling_symlink_out_of_the_workspace_is_refused(
        self, atf: Runner, project: Path, tmp_path: Path
    ) -> None:
        """`realpath -m` follows one whose target does not exist, which is the point."""
        (project / "escape").symlink_to(tmp_path / "absent.md")
        result = edit(atf, project, flow_editing("a", "b"), "escape")
        assert result.code != 0
        assert not (tmp_path / "absent.md").exists()

    def test_a_symlink_inside_the_workspace_is_followed(self, atf: Runner, project: Path) -> None:
        """Contained, so it is an ordinary path and the target is what gets edited."""
        (project / "real.md").write_text("before\n")
        (project / "link.md").symlink_to(project / "real.md")
        result = edit(atf, project, flow_editing("before", "after"), "link.md")
        assert result.code == 0
        assert (project / "real.md").read_text() == "after\n"


class TestWhatItLeavesBehind:
    def test_editing_keeps_the_files_mode(self, atf: Runner, project: Path) -> None:
        """Why the new contents are copied over the target rather than renamed onto it: a
        rename would replace the inode, and the mode with it."""
        target = project / "app.py"
        target.write_text("old\n")
        target.chmod(0o640)
        edit(atf, project, flow_editing("old", "new"), "app.py")
        assert target.stat().st_mode & 0o777 == 0o640

    def test_no_temporary_file_is_left_in_the_workspace(self, atf: Runner, project: Path) -> None:
        before = {path.name for path in project.iterdir()}
        (project / "app.py").write_text("old\n")
        edit(atf, project, flow_editing("old", "new"), "app.py")
        assert {path.name for path in project.iterdir()} == before | {"app.py", "flows"}
