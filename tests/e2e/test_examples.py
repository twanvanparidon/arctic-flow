"""The shipped examples, run with the binary a user downloads.

The integration suite already runs these against the checkout, so what is added here is the
bundle. Freezing Python does not freeze `bash`, `jq`, `openssl` or `awk`, and the components
still shell out to all of them. A frozen process spawning a system binary is the case that
has actually gone wrong: PyInstaller points `LD_LIBRARY_PATH` at the bundle so its own
libraries resolve, and a child inheriting that loads the bundle's OpenSSL instead of the
system's and dies. `child_environment()` undoes it, and `sign_release` is what proves the
undoing works, because signing is `openssl dgst` in a subprocess.

`sign-release` is checked against an HMAC computed here from the key in the vault, rather
than against a recorded digest, so editing the example's release notes is not a failing
signature. The vault itself is the other half: real scrypt and real AES-GCM, from
`cryptography` wheels compiled into the bundle.

`file-review` and `gated-summary` reach the fake `claude`. What they show here is not the
engine's branching, which is covered twice already, but that a frozen process can spawn a
runtime, write a prompt to its stdin and read an envelope back off its stdout.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
import yaml

from support.outcome import Runner

from .conftest import requires

FLOWS = [
    ("sign-release", "sign_release"),
    ("file-review", "review_file"),
    ("gated-summary", "summarize"),
    ("draft-review", "draft_review"),
    ("agent-tools", "annotate"),
]

VAULT_PASSWORD = "demo"


@pytest.mark.parametrize(("directory", "name"), FLOWS)
class TestEveryShippedFlow:
    def test_it_validates(self, examples: Path, atf: Runner, directory: str, name: str) -> None:
        """The loop CI runs over `examples/*/flows/*.yaml`, against the artefact. It reaches
        every component spec, so a tool that did not survive the freeze fails here."""
        result = atf("--workspace", str(examples / directory), "lint", name)
        assert result.code == 0, result.err
        assert "no issues found" in result.out

    def test_its_graph_can_be_printed(
        self, examples: Path, atf: Runner, directory: str, name: str
    ) -> None:
        result = atf("--workspace", str(examples / directory), "inspect", "flow", name)
        assert result.code == 0
        assert result.out.startswith(name)

    def test_its_diagram_can_be_rendered(
        self, examples: Path, atf: Runner, directory: str, name: str
    ) -> None:
        """`util/` is reached only through a lazy import, so the analysis pass cannot see it
        and `atf.spec` names it by hand. A missing hiddenimport fails right here."""
        result = atf("--workspace", str(examples / directory), "inspect", "flow", name, "-o", "md")
        assert result.code == 0
        assert "```mermaid" in result.out


class TestSignRelease:
    """Tool-only, deterministic and free, which is why the docs point at it first."""

    @pytest.fixture(autouse=True)
    def needs(self) -> None:
        requires("jq", "openssl", "xxd", "awk", "realpath")

    @pytest.fixture
    def project(self, examples: Path) -> Path:
        return examples / "sign-release"

    def run_it(self, atf: Runner, project: Path) -> str:
        result = atf(
            "--workspace",
            str(project),
            "run",
            "sign_release",
            "--input",
            "path=release-notes.md",
            env={"ATF_VAULT_PASSWORD": VAULT_PASSWORD},
        )
        assert result.code == 0, result.err
        return result.out

    def test_a_frozen_process_can_still_spawn_the_system_openssl(
        self, project: Path, atf: Runner
    ) -> None:
        """The one thing only this suite can ask. PyInstaller rewrites `LD_LIBRARY_PATH` so
        the bundle's own libraries resolve, and a child inheriting it loads the bundle's
        OpenSSL rather than the system's. `child_environment()` puts it back. Delete that
        correction and this is the test that says so.
        """
        viewed = atf(
            "vault",
            "view",
            str(project / "secrets.vault"),
            env={"ATF_VAULT_PASSWORD": VAULT_PASSWORD},
        )
        key = yaml.safe_load(viewed.out)["signing_key"]
        payload = (project / "release-notes.md").read_bytes()
        expected = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
        assert self.run_it(atf, project).startswith(expected)

    def test_the_output_is_the_signature_and_the_name(self, project: Path, atf: Runner) -> None:
        digest, _, name = self.run_it(atf, project).strip().partition("  ")
        assert name == "release-notes.md"
        assert len(digest) == 64

    def test_the_vault_opens_with_the_scrypt_compiled_into_the_bundle(
        self, project: Path, atf: Runner
    ) -> None:
        result = atf(
            "vault",
            "list",
            str(project / "secrets.vault"),
            env={"ATF_VAULT_PASSWORD": VAULT_PASSWORD},
        )
        assert result.code == 0
        assert "signing_key" in result.out
        assert "demo" not in result.out

    def test_a_wrong_password_is_a_sentence_rather_than_a_traceback(
        self, project: Path, atf: Runner
    ) -> None:
        result = atf(
            "vault", "list", str(project / "secrets.vault"), env={"ATF_VAULT_PASSWORD": "wrong"}
        )
        assert result.code == 1
        assert "Traceback" not in result.err

    def test_the_key_never_appears_in_either_stream(self, project: Path, atf: Runner) -> None:
        """The whole point of the example, asked of the binary that ships it."""
        viewed = atf(
            "vault",
            "view",
            str(project / "secrets.vault"),
            env={"ATF_VAULT_PASSWORD": VAULT_PASSWORD},
        )
        key = yaml.safe_load(viewed.out)["signing_key"]
        result = atf(
            "--workspace",
            str(project),
            "run",
            "sign_release",
            "--input",
            "path=release-notes.md",
            "--trace",
            env={"ATF_VAULT_PASSWORD": VAULT_PASSWORD},
        )
        assert key not in result.out
        assert key not in result.err

    def test_the_sandbox_still_bounds_the_tool(self, project: Path, atf: Runner) -> None:
        """`read_file` resolves with realpath, which is a subprocess reading a real
        filesystem. Its workspace check has to survive the tool being read out of a bundle."""
        result = atf(
            "--workspace",
            str(project),
            "run",
            "sign_release",
            "--input",
            "path=../../README.md",
            env={"ATF_VAULT_PASSWORD": VAULT_PASSWORD},
        )
        assert result.code == 1
        assert "read_file" in result.err


class TestAgentExamples:
    """The two that call a model, answered by the fake `claude` on PATH."""

    @pytest.fixture(autouse=True)
    def needs(self) -> None:
        requires("jq", "awk", "realpath")

    def test_a_frozen_process_can_spawn_a_model_runtime(self, examples: Path, atf: Runner) -> None:
        """Prompt down a pipe, JSON envelope back up one, from inside a bundle."""
        result = atf(
            "--workspace",
            str(examples / "file-review"),
            "run",
            "review_file",
            "--input",
            "path=flows/review_file.yaml",
            env={"FAKE_CLAUDE_PREFER": "clean"},
        )
        assert result.code == 0, result.err
        assert "✓ summarize" in result.err

    def test_the_branch_is_taken_and_the_skip_travels(self, examples: Path, atf: Runner) -> None:
        result = atf(
            "--workspace",
            str(examples / "file-review"),
            "run",
            "review_file",
            "--input",
            "path=flows/review_file.yaml",
            env={"FAKE_CLAUDE_PREFER": "clean"},
        )
        assert "⤼ risk_scan" in result.err
        assert "✓ report" in result.err
        # A skipped step still resolves, so the report was handed the gap rather than a hole.
        assert result.out.strip().splitlines()[-1] == "(not run)"

    def test_what_the_run_cost_is_reported(self, examples: Path, atf: Runner) -> None:
        result = atf(
            "--workspace",
            str(examples / "file-review"),
            "run",
            "review_file",
            "--input",
            "path=flows/review_file.yaml",
            env={"FAKE_CLAUDE_PREFER": "clean"},
        )
        assert "$0." in result.err

    def test_a_gate_that_is_never_satisfied_stops_the_flow(
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
        assert "3/3" in result.err
