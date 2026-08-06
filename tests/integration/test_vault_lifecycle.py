"""A vault from `create` to a step that reads what is in it.

Each command is a real process, because how a secret gets in is half the design: never a
flag, so it comes from stdin or a prompt, and the password comes from a file or the
environment. None of that is observable from a function call.

The last test is the one the rest exist for. A credential goes in through a pipe, is
encrypted at rest, is handed to exactly one step, and never appears on either stream.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from support import components as make

from .conftest import Runner

PASSWORD = "correct horse"


@pytest.fixture
def password_file(tmp_path: Path) -> Path:
    path = tmp_path / "vault.pw"
    path.write_text(PASSWORD + "\n")
    path.chmod(0o600)
    return path


@pytest.fixture
def vault(project: Path, password_file: Path, atf_process: Runner) -> Path:
    path = project / "secrets.vault"
    result = atf_process(
        "vault",
        "create",
        str(path),
        "--vault-password-file",
        str(password_file),
        stdin="ATF_PROBE_token: from-the-yaml\n",
    )
    assert result.code == 0
    return path


class TestCreate:
    def test_it_reads_a_yaml_mapping_from_stdin_and_says_what_it_wrote(
        self, project: Path, password_file: Path, atf_process: Runner
    ) -> None:
        path = project / "new.vault"
        result = atf_process(
            "vault",
            "create",
            str(path),
            "--vault-password-file",
            str(password_file),
            stdin="one: a\ntwo: b\n",
        )
        assert result.code == 0
        assert "2 secrets" in result.out

    def test_the_file_on_disk_gives_nothing_away(self, vault: Path) -> None:
        """It commits next to the flow, so the header is readable and the rest is not."""
        text = vault.read_text()
        assert text.startswith("$ARCTIC_FLOW_VAULT;1.0;AES256GCM;SCRYPT")
        assert "from-the-yaml" not in text

    def test_it_is_not_readable_by_anyone_else(self, vault: Path) -> None:
        assert oct(vault.stat().st_mode)[-3:] == "600"

    def test_an_existing_file_is_refused_before_a_password_is_asked_for(
        self, vault: Path, atf_process: Runner
    ) -> None:
        """No --vault-password-file here, so a prompt is the only other way to get one.
        Reaching it would hang; the refusal has to come first."""
        result = atf_process("vault", "create", str(vault), stdin="a: b\n", timeout=20)
        assert result.code == 1
        assert "already exists" in result.err

    def test_force_replaces_it(self, vault: Path, password_file: Path, atf_process: Runner) -> None:
        result = atf_process(
            "vault",
            "create",
            str(vault),
            "--force",
            "--vault-password-file",
            str(password_file),
            stdin="only: this\n",
        )
        assert result.code == 0
        assert "1 secret" in result.out


class TestSetAndRead:
    def test_a_value_is_piped_in_rather_than_passed_as_a_flag(
        self, vault: Path, password_file: Path, atf_process: Runner
    ) -> None:
        """A --value would land in shell history and the process list."""
        result = atf_process(
            "vault",
            "set",
            str(vault),
            "ATF_PROBE_other",
            "--vault-password-file",
            str(password_file),
            stdin="from-the-pipe\n",
        )
        assert result.code == 0
        assert "added ATF_PROBE_other" in result.out

    def test_setting_a_name_that_is_already_there_says_it_replaced_it(
        self, vault: Path, password_file: Path, atf_process: Runner
    ) -> None:
        result = atf_process(
            "vault",
            "set",
            str(vault),
            "ATF_PROBE_token",
            "--vault-password-file",
            str(password_file),
            stdin="a new value\n",
        )
        assert "replaced ATF_PROBE_token" in result.out

    def test_the_rest_of_the_vault_survives_a_set(
        self, vault: Path, password_file: Path, atf_process: Runner
    ) -> None:
        atf_process(
            "vault",
            "set",
            str(vault),
            "ATF_PROBE_other",
            "--vault-password-file",
            str(password_file),
            stdin="second\n",
        )
        viewed = atf_process(
            "vault", "view", str(vault), "--vault-password-file", str(password_file)
        )
        assert yaml.safe_load(viewed.out) == {
            "ATF_PROBE_token": "from-the-yaml",
            "ATF_PROBE_other": "second",
        }

    def test_list_is_the_one_command_safe_to_run_in_front_of_people(
        self, vault: Path, password_file: Path, atf_process: Runner
    ) -> None:
        result = atf_process(
            "vault", "list", str(vault), "--vault-password-file", str(password_file)
        )
        assert "ATF_PROBE_token" in result.out
        assert "from-the-yaml" not in result.out

    def test_view_prints_secrets_because_that_is_what_it_is_for(
        self, vault: Path, password_file: Path, atf_process: Runner
    ) -> None:
        result = atf_process(
            "vault", "view", str(vault), "--vault-password-file", str(password_file)
        )
        assert yaml.safe_load(result.out) == {"ATF_PROBE_token": "from-the-yaml"}


class TestUnlocking:
    def test_the_environment_can_carry_the_password_instead(
        self, vault: Path, atf_process: Runner
    ) -> None:
        result = atf_process("vault", "list", str(vault), env={"ATF_VAULT_PASSWORD": PASSWORD})
        assert result.code == 0

    def test_the_environment_can_name_a_file_instead(
        self, vault: Path, password_file: Path, atf_process: Runner
    ) -> None:
        result = atf_process(
            "vault", "list", str(vault), env={"ATF_VAULT_PASSWORD_FILE": str(password_file)}
        )
        assert result.code == 0

    def test_the_wrong_password_says_it_could_also_be_tampering(
        self, vault: Path, atf_process: Runner
    ) -> None:
        result = atf_process("vault", "list", str(vault), env={"ATF_VAULT_PASSWORD": "wrong"})
        assert result.code == 1
        assert "wrong password" in result.err


class TestAVaultInAFlow:
    def test_a_credential_goes_in_through_a_pipe_and_comes_out_of_one_step(
        self, project: Path, vault: Path, password_file: Path, atf_process: Runner
    ) -> None:
        atf_process(
            "vault",
            "set",
            str(vault),
            "ATF_PROBE_signing_key",
            "--vault-password-file",
            str(password_file),
            stdin="the-key\n",
        )
        make.write_flow(
            project,
            "signing",
            {
                "flow": "signing",
                "vault": "secrets.vault",
                "start": "sign",
                "steps": [
                    {
                        "id": "sign",
                        "tool": "reveal",
                        "secrets": ["ATF_PROBE_signing_key"],
                        "input": {"name": "ATF_PROBE_signing_key"},
                    }
                ],
                "output": {"template": "{{ steps.sign.text }}"},
            },
        )
        result = atf_process(
            "--workspace",
            str(project),
            "run",
            "signing",
            "--vault-password-file",
            str(password_file),
        )
        assert result.out == "the-key\n"
        # It was asked for on stdout, so it is there. What matters is that nothing else
        # reported it: not the progress lines, not the frame.
        assert "the-key" not in result.err

    def test_a_flow_with_no_vault_never_asks_for_a_password(
        self, project: Path, atf_process: Runner
    ) -> None:
        """No password anywhere here, so anything that asked would hang rather than fail."""
        make.write_flow(
            project,
            "plain",
            {
                "flow": "plain",
                "start": "a",
                "steps": [{"id": "a", "tool": "shout", "input": {"text": "hi"}}],
                "output": {"template": "{{ steps.a.text }}"},
            },
        )
        result = atf_process("--workspace", str(project), "run", "plain", timeout=20)
        assert result.out == "HI\n"

    def test_a_mistyped_input_is_answered_before_the_vault_is_opened(
        self, project: Path, vault: Path, atf_process: Runner
    ) -> None:
        """Otherwise a typo arrives as a password prompt, which is a confusing way to be told."""
        make.write_flow(
            project,
            "signing",
            {
                "flow": "signing",
                "vault": "secrets.vault",
                "start": "sign",
                "inputs": {"path": {"required": False}},
                "steps": [{"id": "sign", "tool": "shout", "input": {"text": "x"}}],
            },
        )
        result = atf_process(
            "--workspace", str(project), "run", "signing", "--input", "paht=x", timeout=20
        )
        assert result.code == 1
        assert "unknown input 'paht'" in result.err
