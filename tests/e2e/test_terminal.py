"""What only a controlling terminal can answer.

`resolve_password` and `vault set` both fall back to `getpass`, and `getpass` does not read
stdin: it opens `/dev/tty`. A process without a controlling terminal has no `/dev/tty` to
open, so no unit or integration test can reach either prompt. Both were written down as
owed to this suite, and this is where they are paid.

The design being checked is that a password is never a flag. It comes from a file, from the
environment, or from a person, and the third of those is the one nothing else covers. So is
the value in `vault set`, for the same reason: a `--value` would sit in shell history and in
the process list.

`support.console.Console` gives the binary a terminal it owns. Every wait carries a deadline
and fails saying what it saw, so a command that stops prompting fails a test rather than
hanging the suite.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from support import components as make
from support.console import Console
from support.outcome import Runner

PASSWORD = "correct horse"
SECRET = "ATF_PROBE_token"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A workspace with one agent in it, for the flows these tests need to run."""
    root = tmp_path / "project"
    root.mkdir()
    make.write_agent(root, "writer")
    return root


@pytest.fixture
def vault(tmp_path: Path, atf: Runner) -> Path:
    """A vault built the non-interactive way, so the prompt tests start from a known file."""
    path = tmp_path / "secrets.vault"
    result = atf(
        "vault",
        "create",
        str(path),
        stdin=f"{SECRET}: from-the-yaml\n",
        env={"ATF_VAULT_PASSWORD": PASSWORD},
    )
    assert result.code == 0, result.err
    return path


class TestThePasswordPrompt:
    def test_a_typed_password_unlocks_the_vault(
        self, vault: Path, console: Callable[..., Console]
    ) -> None:
        """No file and nothing in the environment, so the prompt is the only way through."""
        with console("vault", "list", str(vault)) as session:
            session.expect("Vault password:")
            session.send(PASSWORD)
            assert session.wait() == 0
        assert SECRET in session.output

    def test_the_password_is_not_echoed_back(
        self, vault: Path, console: Callable[..., Console]
    ) -> None:
        """getpass turns the terminal's echo off. On a shared screen that is the point."""
        with console("vault", "list", str(vault)) as session:
            session.expect("Vault password:")
            session.send(PASSWORD)
            session.wait()
        assert PASSWORD not in session.output

    def test_a_wrong_password_is_indistinguishable_from_tampering(
        self, vault: Path, console: Callable[..., Console]
    ) -> None:
        with console("vault", "list", str(vault)) as session:
            session.expect("Vault password:")
            session.send("wrong")
            assert session.wait() == 1
        assert "wrong password" in session.output

    def test_declining_the_prompt_is_answered_rather_than_waited_on(
        self, vault: Path, console: Callable[..., Console]
    ) -> None:
        """Ctrl-D at the prompt. getpass raises EOFError, which has to become a sentence."""
        with console("vault", "list", str(vault)) as session:
            session.expect("Vault password:")
            session.decline()
            assert session.wait() == 1
        assert "no vault password given" in session.output

    def test_a_password_file_is_used_without_asking(
        self, vault: Path, tmp_path: Path, console: Callable[..., Console]
    ) -> None:
        """The prompt is the last resort, so anything that reached it here would hang."""
        password_file = tmp_path / "vault.pw"
        password_file.write_text(PASSWORD + "\n")
        with console("vault", "list", str(vault), "--vault-password-file", str(password_file)) as (
            session
        ):
            assert session.wait(timeout=30) == 0
        assert "Vault password:" not in session.output


class TestTheValuePrompt:
    def test_a_secret_is_typed_rather_than_passed_as_a_flag(
        self, vault: Path, atf: Runner, console: Callable[..., Console]
    ) -> None:
        """Two prompts in order: the password first, then the value. The password is
        resolved before the value is read, so they cannot arrive the other way round."""
        with console("vault", "set", str(vault), "ATF_PROBE_other") as session:
            session.expect("Vault password:")
            session.send(PASSWORD)
            session.expect("Value for ATF_PROBE_other:")
            session.send("from-the-keyboard")
            assert session.wait() == 0

        viewed = atf("vault", "view", str(vault), env={"ATF_VAULT_PASSWORD": PASSWORD})
        assert yaml.safe_load(viewed.out)["ATF_PROBE_other"] == "from-the-keyboard"

    def test_it_says_what_it_did(self, vault: Path, console: Callable[..., Console]) -> None:
        with console("vault", "set", str(vault), SECRET) as session:
            session.expect("Vault password:")
            session.send(PASSWORD)
            session.expect(f"Value for {SECRET}:")
            session.send("replaced by hand")
            session.wait()
        assert f"replaced {SECRET}" in session.output


class TestARunThatNeedsUnlocking:
    def test_the_flow_prompts_once_and_then_runs(
        self, project: Path, vault: Path, console: Callable[..., Console]
    ) -> None:
        make.write_flow(
            project,
            "probing",
            {
                "flow": "probing",
                "vault": str(vault),
                "start": "probe",
                "steps": [
                    {"id": "probe", "agent": "writer", "secrets": [SECRET], "prompt": "!invocation"}
                ],
                "output": {"template": "{{ steps.probe.text }}"},
            },
        )
        with console("--workspace", str(project), "run", "probing") as session:
            session.expect("Vault password:")
            session.send(PASSWORD)
            assert session.wait() == 0
        assert "from-the-yaml" in session.output

    def test_a_flow_with_no_vault_never_asks(
        self, project: Path, console: Callable[..., Console]
    ) -> None:
        """Nothing supplies a password here, so anything that asked would hang instead."""
        make.write_flow(
            project,
            "plain",
            {
                "flow": "plain",
                "start": "draft",
                "steps": [{"id": "draft", "agent": "writer", "prompt": "no secrets here"}],
                "output": {"template": "{{ steps.draft.text }}"},
            },
        )
        with console("--workspace", str(project), "run", "plain") as session:
            assert session.wait(timeout=30) == 0
        assert "Vault password:" not in session.output


class TestWhatAPersonSees:
    def test_the_banner_is_drawn_for_a_person(self, console: Callable[..., Console]) -> None:
        """`atf --version` is one parseable line on a pipe and a banner on a terminal. Both
        halves matter; the pipe half is in test_binary.py."""
        with console("--version") as session:
            session.wait()
        assert "A R C T I C" in session.output

    def test_the_output_is_framed(self, project: Path, console: Callable[..., Console]) -> None:
        """The frame draws only when stdout and stderr are both terminals."""
        make.write_flow(
            project,
            "framed",
            {
                "flow": "framed",
                "start": "draft",
                "steps": [{"id": "draft", "agent": "writer", "prompt": "the answer"}],
                "output": {"template": "{{ steps.draft.text }}"},
            },
        )
        with console("--workspace", str(project), "run", "framed") as session:
            assert session.wait(timeout=30) == 0
        rule = next(line for line in session.output.splitlines() if "─" in line)
        assert "framed" in rule  # the flow's name sits on the rule
