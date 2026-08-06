"""The vault commands, and the one thing that makes them usable from more than one place:
a password that arrives as a callable and is only asked for if it is needed.

That is what keeps `vault create` from prompting before it tells you the file already
exists. The test for it is a provider that raises: if anything resolves the password too
early, the test says so rather than hanging on a prompt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import commands
from paths.resolver import Paths
from vault.vault import Vault, VaultError

PASSWORD = "demo"


def never_asked() -> str:
    raise AssertionError("the password was resolved before it was needed")


@pytest.fixture
def vault_file(workspace: Path) -> Path:
    path = workspace / "secrets.vault"
    Vault(path=path, values={"token": "abc", "other": "def"}).save(PASSWORD)
    return path


class TestUnlock:
    def test_a_password_already_in_hand_is_used_as_it_is(self) -> None:
        assert commands.unlock(PASSWORD) == PASSWORD

    def test_a_provider_is_asked_now(self) -> None:
        assert commands.unlock(lambda: "from the provider") == "from the provider"

    def test_none_falls_back_to_however_the_vault_module_resolves_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ATF_VAULT_PASSWORD", "from the environment")
        assert commands.unlock(None) == "from the environment"


class TestOpenVault:
    def test_no_reference_is_not_an_error(self, paths: Paths) -> None:
        """A flow declaring no secrets needs no vault and must not prompt."""
        assert commands.open_vault(None, paths, never_asked) is None

    def test_an_empty_reference_is_treated_the_same(self, paths: Paths) -> None:
        assert commands.open_vault("", paths, never_asked) is None

    def test_a_relative_reference_is_resolved_against_the_workspace(
        self, paths: Paths, vault_file: Path
    ) -> None:
        """So the vault travels with the project rather than with the shell's directory."""
        vault = commands.open_vault("secrets.vault", paths, PASSWORD)
        assert vault is not None
        assert vault.path == vault_file

    def test_an_absolute_reference_is_used_as_written(self, paths: Paths, vault_file: Path) -> None:
        vault = commands.open_vault(str(vault_file), paths, PASSWORD)
        assert vault is not None
        assert vault.values["token"] == "abc"

    def test_the_wrong_password_is_reported(self, paths: Paths, vault_file: Path) -> None:
        with pytest.raises(VaultError, match="wrong password"):
            commands.open_vault("secrets.vault", paths, "not it")


class TestCreateVault:
    def test_writes_a_vault_and_counts_what_went_in(self, paths: Paths, workspace: Path) -> None:
        path = workspace / "new.vault"
        result = commands.create_vault(path, paths, {"a": "1", "b": "2"}, PASSWORD)
        assert result.count == 2
        assert result.display == "./new.vault"
        assert Vault.open(path, PASSWORD).values == {"a": "1", "b": "2"}

    def test_an_empty_vault_is_a_reasonable_thing_to_create(
        self, paths: Paths, workspace: Path
    ) -> None:
        result = commands.create_vault(workspace / "empty.vault", paths, {}, PASSWORD)
        assert result.count == 0

    def test_values_are_coerced_to_text(self, paths: Paths, workspace: Path) -> None:
        """A YAML mapping yields ints and bools, and a secret is text by the time a step
        receives it."""
        path = workspace / "typed.vault"
        commands.create_vault(path, paths, {"port": 8080, "on": True}, PASSWORD)
        assert Vault.open(path, PASSWORD).values == {"port": "8080", "on": "True"}

    def test_an_existing_file_is_refused_before_anything_is_asked(
        self, paths: Paths, vault_file: Path
    ) -> None:
        with pytest.raises(VaultError, match="already exists: pass --force"):
            commands.create_vault(vault_file, paths, {"a": "1"}, never_asked)

    def test_force_replaces_it(self, paths: Paths, vault_file: Path) -> None:
        commands.create_vault(vault_file, paths, {"only": "this"}, PASSWORD, force=True)
        assert Vault.open(vault_file, PASSWORD).values == {"only": "this"}


class TestSetSecret:
    def test_adding_reports_that_it_was_an_addition(self, paths: Paths, vault_file: Path) -> None:
        result = commands.set_secret(vault_file, paths, "fresh", "value", PASSWORD)
        assert result.replaced is False
        assert result.name == "fresh"

    def test_replacing_reports_that_it_was_a_replacement(
        self, paths: Paths, vault_file: Path
    ) -> None:
        """The caller cannot tell afterwards, and it is the one thing worth reporting."""
        assert commands.set_secret(vault_file, paths, "token", "new", PASSWORD).replaced is True

    def test_the_rest_of_the_vault_is_left_alone(self, paths: Paths, vault_file: Path) -> None:
        commands.set_secret(vault_file, paths, "token", "new", PASSWORD)
        assert Vault.open(vault_file, PASSWORD).values == {"token": "new", "other": "def"}

    def test_an_empty_value_is_refused_rather_than_stored(
        self, paths: Paths, vault_file: Path
    ) -> None:
        """What an abandoned prompt or an empty pipe produces. A blank credential fails
        later, somewhere else."""
        with pytest.raises(VaultError, match="no value given for token"):
            commands.set_secret(vault_file, paths, "token", "", PASSWORD)

    def test_the_vault_is_only_opened_once(self, paths: Paths, vault_file: Path) -> None:
        """One resolved password covers the read and the write, so a provider is asked once."""
        asked = []

        def provider() -> str:
            asked.append(1)
            return PASSWORD

        commands.set_secret(vault_file, paths, "token", "new", provider)
        assert len(asked) == 1


class TestReading:
    def test_names_come_back_sorted_and_without_values(
        self, paths: Paths, vault_file: Path
    ) -> None:
        result = commands.secret_names(vault_file, paths, PASSWORD)
        assert result.names == ("other", "token")

    def test_contents_come_back_decrypted(self, paths: Paths, vault_file: Path) -> None:
        result = commands.vault_contents(vault_file, paths, PASSWORD)
        assert result.values == {"token": "abc", "other": "def"}

    def test_the_returned_values_are_a_copy_of_the_vaults(
        self, paths: Paths, vault_file: Path
    ) -> None:
        result = commands.vault_contents(vault_file, paths, PASSWORD)
        result.values["token"] = "changed"
        assert Vault.open(vault_file, PASSWORD).values["token"] == "abc"
