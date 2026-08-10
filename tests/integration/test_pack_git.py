"""The git pack's eight tools, run the way a flow runs them.

A real repository throughout, because every failure worth catching here is one only git
has: an index that is empty, a branch name already taken, a repository whose root is above
the workspace, a commit with no identity to sign it. A stand-in answers all of those the
same way, which is to say it answers none of them.

Nothing here asserts on a sha, a date or an author. Those are what the machine and the
moment decide, and a test that pinned one would fail on the second run.

What each class is protecting is the decision in its tool that could plausibly be
"simplified" away later: that a clean tree is a success, that a diff is bounded, that
staging is by name only, that nothing invents a committer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from paths.config import CONFIG_FILE
from support import repository
from support.outcome import Outcome, Runner

from .conftest import requires

SHORT_SHA = re.compile(r"\b[0-9a-f]{7,}\b")


@pytest.fixture(autouse=True)
def needs_git() -> None:
    requires("git", "jq", "awk", "realpath", "mktemp")


@pytest.fixture(autouse=True)
def no_ambient_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every git the pack runs reads the repository's config and nothing else.

    A developer's own `commit.gpgsign`, `core.hooksPath` or `init.templatedir` would
    otherwise reach the tools through `child_environment()`, which copies this process's
    environment, and turn a green suite red on one machine.
    """
    for name, value in repository.environment().items():
        if name.startswith("GIT_CONFIG"):
            monkeypatch.setenv(name, value)


@pytest.fixture
def enabled(home: Path) -> None:
    (home / ".arctic").mkdir(parents=True, exist_ok=True)
    (home / ".arctic" / CONFIG_FILE).write_text("packs:\n  - git\n")


@pytest.fixture
def repo(tmp_path: Path, enabled: None) -> Path:
    """A workspace that is a repository, with two commits and something uncommitted.

    `flows/` is ignored, because `call()` writes the flow it runs into the workspace and
    the workspace is the repository. Without this the harness's own file is an untracked
    change in every answer, and no tree here could ever be clean.
    """
    root = repository.initialise(tmp_path / "repo")
    repository.commit(
        root,
        "first commit",
        **{"a.txt": "one\n", "b.txt": "two\n", ".gitignore": "flows/\n"},
    )
    repository.commit(
        root,
        "second commit\n\nA body, and a #45 reference.",
        **{"src__app.py": "print('hi')\n"},
    )
    (root / "a.txt").write_text("one\nthree\n")
    (root / "loose.txt").write_text("untracked\n")
    return root


def call(atf: Runner, project: Path, tool: str, **input_values: Any) -> Outcome:
    """Run one pack tool as the only step of a flow, and return the whole outcome."""
    definition = {
        "flow": "call",
        "start": "act",
        "steps": [{"id": "act", "tool": f"arctic/git/{tool}", "input": input_values}],
        "output": {"template": "{{ steps.act.text }}"},
    }
    (project / "flows").mkdir(exist_ok=True)
    (project / "flows" / "call.yaml").write_text(json.dumps(definition))
    return atf("--workspace", str(project), "run", "call")


class TestStatus:
    def test_it_reports_the_branch(self, atf: Runner, repo: Path) -> None:
        assert "branch main" in call(atf, repo, "status").out

    def test_it_groups_the_changes(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "status").out
        assert "unstaged:" in out and "a.txt" in out
        assert "untracked:" in out and "loose.txt" in out

    def test_it_translates_the_porcelain_code(self, atf: Runner, repo: Path) -> None:
        """`MM` is not something anything downstream should have to know."""
        assert "modified" in call(atf, repo, "status").out

    def test_a_clean_tree_succeeds(self, atf: Runner, repo: Path) -> None:
        """The engine fails a step on any non-zero exit, so "nothing has changed" has to
        be a success or a flow could not branch on it."""
        (repo / "a.txt").write_text("one\n")
        (repo / "loose.txt").unlink()
        result = call(atf, repo, "status")
        assert result.code == 0
        assert "clean" in result.out

    def test_untracked_files_can_be_left_out(self, atf: Runner, repo: Path) -> None:
        assert "loose.txt" not in call(atf, repo, "status", no_untracked=True).out

    def test_it_truncates_across_all_the_groups_together(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "status", max_files=1).out
        assert "truncated" in out
        assert out.count("truncated") == 1, "one notice for the report, not one per group"


class TestLog:
    def test_it_lists_commits_newest_first(self, atf: Runner, repo: Path) -> None:
        lines = call(atf, repo, "log").out.splitlines()
        assert lines[0].endswith("second commit")
        assert lines[1].endswith("first commit")

    def test_each_line_carries_a_sha(self, atf: Runner, repo: Path) -> None:
        assert SHORT_SHA.match(call(atf, repo, "log").out.splitlines()[0])

    def test_the_body_is_left_out_by_default(self, atf: Runner, repo: Path) -> None:
        assert "#45 reference" not in call(atf, repo, "log").out

    def test_the_body_is_included_when_asked(self, atf: Runner, repo: Path) -> None:
        assert "#45 reference" in call(atf, repo, "log", body=True).out

    def test_a_path_narrows_it(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "log", path="src/app.py").out
        assert "second commit" in out
        assert "first commit" not in out

    def test_a_range_is_a_ref(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "log", ref="HEAD~1..HEAD").out
        assert "second commit" in out
        assert "first commit" not in out

    def test_it_truncates(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "log", max_commits=1).out
        assert "second commit" in out
        assert "truncated" in out

    def test_a_repository_with_no_commits_succeeds(
        self, atf: Runner, tmp_path: Path, enabled: None
    ) -> None:
        """A flow asking about history has to survive the answer being "none", or it
        cannot run against a repository it just made."""
        fresh = repository.initialise(tmp_path / "fresh")
        result = call(atf, fresh, "log")
        assert result.code == 0
        assert "no commits" in result.out

    def test_a_ref_that_does_not_resolve_fails(self, atf: Runner, repo: Path) -> None:
        assert call(atf, repo, "log", ref="nope").code != 0

    def test_the_failure_carries_git_s_reason_and_not_its_usage_line(
        self, atf: Runner, repo: Path
    ) -> None:
        """git answers an unknown revision with three lines, of which the last is a usage
        example. Repeating that one would be answering a question nobody asked."""
        assert "unknown revision" in call(atf, repo, "log", ref="nope").err


class TestDiff:
    def test_it_shows_the_unstaged_change_by_default(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "diff").out
        assert "+three" in out

    def test_staged_is_a_different_question(self, atf: Runner, repo: Path) -> None:
        result = call(atf, repo, "diff", staged=True)
        assert result.code == 0
        assert "no changes" in result.out

    def test_a_summary_counts_instead_of_quoting(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "diff", summary=True).out
        assert "a.txt" in out and "1 file changed" in out
        assert "+three" not in out

    def test_a_ref_compares_against_it(self, atf: Runner, repo: Path) -> None:
        assert "app.py" in call(atf, repo, "diff", ref="HEAD~1").out

    def test_it_truncates(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "diff", max_lines=2).out
        assert "truncated" in out
        assert len(out.splitlines()) == 3

    def test_nothing_to_diff_succeeds(self, atf: Runner, repo: Path) -> None:
        (repo / "a.txt").write_text("one\n")
        result = call(atf, repo, "diff")
        assert result.code == 0
        assert "no changes" in result.out


class TestShow:
    def test_it_carries_the_message_and_the_diff(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "show").out
        assert "second commit" in out
        assert "#45 reference" in out
        assert "app.py" in out

    def test_the_header_is_the_pack_s_own_shape(self, atf: Runner, repo: Path) -> None:
        """So it lines up with what git/log prints rather than with git's default."""
        out = call(atf, repo, "show").out
        assert out.startswith("commit ")
        assert "\nauthor " in out and "\ndate " in out

    def test_an_earlier_commit_can_be_named(self, atf: Runner, repo: Path) -> None:
        assert "first commit" in call(atf, repo, "show", ref="HEAD~1").out

    def test_a_ref_that_does_not_resolve_fails(self, atf: Runner, repo: Path) -> None:
        """A `fail` inside a command substitution passed as an argument exits only the
        subshell, so this once printed its error and then succeeded with no output."""
        result = call(atf, repo, "show", ref="deadbeef")
        assert result.code != 0
        assert result.out == ""


class TestBranch:
    def test_it_marks_the_checked_out_branch(self, atf: Runner, repo: Path) -> None:
        assert call(atf, repo, "branch").out.startswith("* main")

    def test_it_lists_a_second_branch(self, atf: Runner, repo: Path) -> None:
        repository.git(repo, "branch", "spare")
        out = call(atf, repo, "branch").out
        assert "spare" in out
        assert out.count("*") == 1

    def test_a_repository_with_no_commits_succeeds(
        self, atf: Runner, tmp_path: Path, enabled: None
    ) -> None:
        fresh = repository.initialise(tmp_path / "fresh")
        result = call(atf, fresh, "branch")
        assert result.code == 0
        assert "no branches" in result.out


class TestAdd:
    def test_it_stages_a_named_path(self, atf: Runner, repo: Path) -> None:
        assert "a.txt" in call(atf, repo, "add", path="a.txt").out

    def test_what_comes_back_is_the_index_and_not_the_call(self, atf: Runner, repo: Path) -> None:
        """Which is what the next commit would record, and the question worth answering."""
        repository.git(repo, "add", "--", "loose.txt")
        out = call(atf, repo, "add", path="a.txt").out
        assert "a.txt" in out and "loose.txt" in out

    def test_several_paths_at_once(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "add", path=["a.txt", "loose.txt"]).out
        assert "a.txt" in out and "loose.txt" in out

    def test_a_path_outside_the_workspace_is_refused(self, atf: Runner, repo: Path) -> None:
        result = call(atf, repo, "add", path="../escape")
        assert result.code != 0
        assert "outside the workspace root" in result.err

    def test_one_bad_path_stages_none_of_them(self, atf: Runner, repo: Path) -> None:
        """A partial index with an error about the rest is easy to commit by mistake."""
        assert call(atf, repo, "add", path=["a.txt", "/etc/passwd"]).code != 0
        assert repository.git(repo, "diff", "--cached", "--name-only") == ""


class TestCommit:
    def test_it_records_what_is_staged(self, atf: Runner, repo: Path) -> None:
        repository.git(repo, "add", "--", "a.txt")
        result = call(atf, repo, "commit", message="third commit")
        assert result.code == 0
        assert repository.git(repo, "log", "-1", "--pretty=%s") == "third commit"

    def test_it_answers_with_the_sha_first(self, atf: Runner, repo: Path) -> None:
        """Which is what a later step templates."""
        repository.git(repo, "add", "--", "a.txt")
        first = call(atf, repo, "commit", message="third commit").out.splitlines()[0]
        assert SHORT_SHA.match(first)
        assert first.endswith("third commit")

    def test_it_lists_what_it_recorded(self, atf: Runner, repo: Path) -> None:
        repository.git(repo, "add", "--", "a.txt")
        assert "a.txt" in call(atf, repo, "commit", message="third commit").out

    def test_an_empty_index_is_refused(self, atf: Runner, repo: Path) -> None:
        """Rather than recorded as an empty commit, and rather than a silent no-op."""
        result = call(atf, repo, "commit", message="nothing here")
        assert result.code != 0
        assert "nothing is staged" in result.err

    def test_the_refusal_names_the_tool_that_fixes_it(self, atf: Runner, repo: Path) -> None:
        assert "git/add" in call(atf, repo, "commit", message="nothing here").err

    def test_a_hash_in_the_body_survives(self, atf: Runner, repo: Path) -> None:
        """git's default cleanup deletes every line starting with #, which exists for a
        message typed over a commented template. There is no editor here, and a `#45`
        written against an issue would vanish."""
        repository.git(repo, "add", "--", "a.txt")
        call(atf, repo, "commit", message="third commit\n\n#45 is the issue")
        assert "#45 is the issue" in repository.git(repo, "log", "-1", "--pretty=%B")

    def test_an_author_can_be_named(self, atf: Runner, repo: Path) -> None:
        repository.git(repo, "add", "--", "a.txt")
        call(atf, repo, "commit", message="third", author="Someone <someone@example.com>")
        assert repository.git(repo, "log", "-1", "--pretty=%ae") == "someone@example.com"

    def test_an_author_that_is_not_name_and_email_is_refused(self, atf: Runner, repo: Path) -> None:
        """git refuses it too, but names neither the parameter nor the shape."""
        repository.git(repo, "add", "--", "a.txt")
        result = call(atf, repo, "commit", message="third", author="nobody")
        assert result.code != 0
        assert "author" in result.err

    def test_no_identity_is_refused_rather_than_invented(
        self, atf: Runner, tmp_path: Path, enabled: None
    ) -> None:
        """A commit carries an author, and that name goes into history. This is the state
        a build machine is in, and guessing attributes the work to somebody else."""
        bare = repository.initialise(tmp_path / "bare", identity=False)
        (bare / "f.txt").write_text("x\n")
        repository.git(bare, "add", "--", "f.txt")

        result = call(atf, bare, "commit", message="who wrote this")
        assert result.code != 0
        assert "no identity" in result.err or "identity" in result.err


class TestCheckout:
    def test_it_creates_and_switches(self, atf: Runner, repo: Path) -> None:
        result = call(atf, repo, "checkout", branch="spare", create=True)
        assert result.code == 0
        assert repository.git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "spare"

    def test_it_reports_where_it_came_from(self, atf: Runner, repo: Path) -> None:
        out = call(atf, repo, "checkout", branch="spare", create=True).out
        assert "on spare" in out and "was main" in out

    def test_switching_to_the_current_branch_succeeds(self, atf: Runner, repo: Path) -> None:
        """So a flow that ensures it is on a branch does not have to check first."""
        result = call(atf, repo, "checkout", branch="main")
        assert result.code == 0
        assert "already on main" in result.out

    def test_creating_a_name_that_exists_is_refused(self, atf: Runner, repo: Path) -> None:
        """It means the name was not the one you thought, which is worth stopping for."""
        repository.git(repo, "branch", "spare")
        assert call(atf, repo, "checkout", branch="spare", create=True).code != 0

    def test_a_branch_that_does_not_exist_is_refused(self, atf: Runner, repo: Path) -> None:
        assert call(atf, repo, "checkout", branch="ghost").code != 0

    def test_a_start_point_without_create_is_refused(self, atf: Runner, repo: Path) -> None:
        result = call(atf, repo, "checkout", branch="main", start_point="HEAD")
        assert result.code != 0
        assert "start_point" in result.err


class TestContainment:
    """The rule the whole pack shares: the repository is the workspace, never one above it.

    Without it a flow run in `myrepo/subproject` would log, diff and commit the whole of
    `myrepo`, which is not what the workspace says the flow is about.
    """

    @pytest.fixture
    def subdirectory(self, repo: Path) -> Path:
        inner = repo / "subproject"
        inner.mkdir()
        return inner

    @pytest.mark.parametrize("tool", ["status", "log", "diff", "show", "branch"])
    def test_a_repository_above_the_workspace_is_refused(
        self, atf: Runner, subdirectory: Path, tool: str
    ) -> None:
        result = call(atf, subdirectory, tool)
        assert result.code != 0
        assert "above the workspace" in result.err

    def test_the_refusal_says_how_to_work_on_it_anyway(
        self, atf: Runner, subdirectory: Path
    ) -> None:
        assert "--workspace" in call(atf, subdirectory, "status").err

    @pytest.mark.parametrize("tool", ["status", "log", "branch"])
    def test_a_workspace_that_is_not_a_repository_is_refused(
        self, atf: Runner, project: Path, enabled: None, tool: str
    ) -> None:
        result = call(atf, project, tool)
        assert result.code != 0
        assert "not a git repository" in result.err


class TestPermissions:
    """What a spec declares is what decides whether an agent may be granted the tool.

    `filesystem: write` is the gate: granting one needs `unattended: true` on the agent.
    A tool that read and wrote could only ever be granted as one that writes, which is why
    listing branches and switching them are two tools.
    """

    @pytest.mark.parametrize("tool", ["status", "log", "diff", "show", "branch"])
    def test_the_reading_tools_declare_read(self, tool: str) -> None:
        assert self.spec(tool)["permissions"]["filesystem"] == "read"

    @pytest.mark.parametrize("tool", ["add", "commit", "checkout"])
    def test_the_writing_tools_declare_write(self, tool: str) -> None:
        assert self.spec(tool)["permissions"]["filesystem"] == "write"

    @pytest.mark.parametrize(
        "tool", ["status", "log", "diff", "show", "branch", "add", "commit", "checkout"]
    )
    def test_nothing_in_the_pack_reaches_the_network(self, tool: str) -> None:
        """No push, pull, fetch or clone, so this is a fact something can check rather
        than a promise in a paragraph."""
        assert self.spec(tool)["permissions"]["network"] is False

    @staticmethod
    def spec(tool: str) -> dict[str, Any]:
        from paths.resolver import packs_root

        path = packs_root() / "git" / "tools" / "arctic" / "git" / tool / "spec.json"
        return json.loads(path.read_text())
