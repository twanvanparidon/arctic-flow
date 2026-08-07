"""The shipped examples, run the way the documentation says to run them.

These are the projects a reader meets first, and the commands in the README and
CONTRIBUTING are copied out of here verbatim. A flow that stopped working would be the most
expensive kind of broken, so the corpus is exercised rather than described.

`sign-release` runs for real: tools only, deterministic, and free. Its signature is checked
against one computed here with the key out of the vault, so what is asserted is that the
example produces a correct HMAC, not that it produces the same bytes as last time.

`file-review` and `gated-summary` call a model, so they reach the fake `claude`. What they
demonstrate is the engine's part, and none of that needs a real answer: a branch is taken,
the other subtree is skipped, a join runs anyway, and a gate that is never satisfied stops
the flow instead of letting it through.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
import yaml

from .conftest import Runner, requires

FLOWS = [
    ("sign-release", "sign_release"),
    ("file-review", "review_file"),
    ("gated-summary", "summarize"),
    ("agent-tools", "annotate"),
]


@pytest.mark.parametrize(("directory", "name"), FLOWS)
class TestEveryShippedFlow:
    def test_it_validates(self, examples: Path, atf: Runner, directory: str, name: str) -> None:
        """The same loop CI runs. A clean lint means it will not fail on its own definitions."""
        result = atf("--workspace", str(examples / directory), "lint", name)
        assert result.code == 0
        assert "no issues found" in result.out

    def test_its_graph_can_be_printed(
        self, examples: Path, atf: Runner, directory: str, name: str
    ) -> None:
        result = atf("--workspace", str(examples / directory), "graph", name)
        assert result.code == 0
        assert result.out.startswith(name)

    def test_its_diagram_can_be_rendered(
        self, examples: Path, atf: Runner, directory: str, name: str
    ) -> None:
        result = atf("--workspace", str(examples / directory), "diagram", name)
        assert result.code == 0
        assert "```mermaid" in result.out

    def test_the_diagram_can_be_written_to_a_file(
        self, examples: Path, atf: Runner, tmp_path: Path, directory: str, name: str
    ) -> None:
        out = tmp_path / f"{name}.md"
        result = atf("--workspace", str(examples / directory), "diagram", name, "-o", str(out))
        assert result.code == 0
        assert out.read_text().startswith("# ")


class TestSignRelease:
    """Tool-only, deterministic and free, which is why the docs point at it first."""

    @pytest.fixture(autouse=True)
    def needs(self) -> None:
        requires("jq", "openssl", "xxd", "awk", "realpath")

    @pytest.fixture
    def project(self, examples: Path) -> Path:
        return examples / "sign-release"

    def run_it(self, atf: Runner, project: Path, path: str = "release-notes.md") -> str:
        result = atf("--workspace", str(project), "run", "sign_release", "--input", f"path={path}")
        assert result.code == 0, result.err
        return result.out

    def test_it_signs_the_file_named_on_the_command_line(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        digest, _, name = self.run_it(atf, project).strip().partition("  ")
        assert name == "release-notes.md"
        assert len(digest) == 64

    def test_the_signature_verifies_against_the_key_in_the_vault(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Computed here rather than compared to a recorded digest, so editing the example's
        release notes does not look like a broken signature."""
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        viewed = atf("vault", "view", str(project / "secrets.vault"))
        key = yaml.safe_load(viewed.out)["signing_key"]
        payload = (project / "release-notes.md").read_bytes()
        expected = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
        assert self.run_it(atf, project).startswith(expected)

    def test_the_key_never_appears_in_the_output_or_the_progress(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of the example: the signing key is in neither stream."""
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        viewed = atf("vault", "view", str(project / "secrets.vault"))
        key = yaml.safe_load(viewed.out)["signing_key"]
        result = atf(
            "--workspace",
            str(project),
            "run",
            "sign_release",
            "--input",
            "path=release-notes.md",
            "--trace",
        )
        assert key not in result.out
        assert key not in result.err

    def test_it_needs_the_vault_password(self, project: Path, atf: Runner) -> None:
        result = atf(
            "--workspace",
            str(project),
            "run",
            "sign_release",
            "--input",
            "path=release-notes.md",
            "--vault-password-file",
            "/dev/null",
        )
        assert result.code == 1

    def test_reading_outside_the_workspace_is_refused_by_the_tool(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """read_file is sandboxed to the project, which is what bounds the example's reach."""
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "demo")
        result = atf(
            "--workspace",
            str(project),
            "run",
            "sign_release",
            "--input",
            "path=../../README.md",
        )
        assert result.code == 1
        assert "read_file" in result.err


class TestFileReview:
    """Two agents in parallel, a switch on one of their answers, a skip and a join."""

    @pytest.fixture(autouse=True)
    def needs(self) -> None:
        requires("jq", "awk", "realpath")

    @pytest.fixture
    def project(self, examples: Path) -> Path:
        return examples / "file-review"

    def run_it(self, atf: Runner, project: Path) -> tuple[str, str]:
        result = atf(
            "--workspace",
            str(project),
            "run",
            "review_file",
            "--input",
            "path=flows/review_file.yaml",
        )
        assert result.code == 0, result.err
        return result.out, result.err

    def test_the_risky_branch_runs_the_risk_scan(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_PREFER", "risky")
        out, err = self.run_it(atf, project)
        assert "✓ risk_scan" in err
        # The report ends with whatever risk_scan produced, so that last line is the slot.
        assert out.strip().splitlines()[-1] == '{"risks": []}'

    def test_the_clean_branch_skips_it_and_the_report_still_runs(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_PREFER", "clean")
        out, err = self.run_it(atf, project)
        assert "⤼ risk_scan" in err
        assert "✓ report" in err
        # A skipped step still resolves, so the report was handed the gap rather than a hole.
        assert out.strip().splitlines()[-1] == "(not run)"

    def test_the_two_steps_after_the_read_both_run(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_PREFER", "clean")
        _, err = self.run_it(atf, project)
        assert "✓ summarize" in err
        assert "✓ triage" in err

    def test_what_the_run_cost_is_reported(
        self, project: Path, atf: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_PREFER", "clean")
        _, err = self.run_it(atf, project)
        assert "$0." in err


class TestGatedSummary:
    """A tool holds the answer to a word budget, and the prompt cannot talk it round."""

    @pytest.fixture(autouse=True)
    def needs(self) -> None:
        requires("jq", "awk", "realpath")

    def test_an_answer_that_never_comes_in_under_the_limit_fails_the_run(
        self, examples: Path, atf: Runner
    ) -> None:
        """The fake answers with the prompt, which is far over sixty words, every time."""
        result = atf(
            "--workspace",
            str(examples / "gated-summary"),
            "run",
            "summarize",
            "--input",
            "path=incident.md",
        )
        assert result.code == 1
        assert "did not pass gate 'word_limit'" in result.err

    def test_every_attempt_is_reported_as_it_is_rejected(self, examples: Path, atf: Runner) -> None:
        result = atf(
            "--workspace",
            str(examples / "gated-summary"),
            "run",
            "summarize",
            "--input",
            "path=incident.md",
        )
        assert "1/3" in result.err
        assert "3/3" in result.err
