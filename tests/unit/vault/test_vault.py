"""The encrypted secrets file: its format, its failures, and what it refuses to leak.

Real cryptography throughout. There is nothing here to substitute and nothing worth
substituting: the properties being tested are that a wrong password fails, that an edited
header fails as tampering rather than as a wrong password, and that a file written from
plaintext is never briefly world-readable. A stand-in for AESGCM would have none of them.

`DEFAULT_KDF_PARAMS` is scrypt at n=16384, so each encrypt or decrypt costs real
milliseconds. Tests that only need a `Vault` object build one directly rather than paying
for a round trip they are not testing.
"""

from __future__ import annotations

import base64
import stat
from pathlib import Path

import pytest
import yaml

from vault.vault import (
    DEFAULT_KDF_PARAMS,
    MAGIC,
    PASSWORD_ENV,
    PASSWORD_FILE_ENV,
    Vault,
    VaultError,
    _derive,
    _format_header,
    _parse_header,
    decrypt,
    encrypt,
    resolve_password,
)

PASSWORD = "correct horse"


class TestResolvePassword:
    def test_reads_the_first_line_of_a_file(self, tmp_path: Path) -> None:
        path = tmp_path / "pw"
        path.write_text("s3cret\nsomething else\n")
        assert resolve_password(path, env={}) == "s3cret"

    def test_surrounding_whitespace_is_dropped(self, tmp_path: Path) -> None:
        """`echo pw > file` leaves a newline, and a credential with one on the end fails
        far from here."""
        path = tmp_path / "pw"
        path.write_text("  s3cret  \n")
        assert resolve_password(path, env={}) == "s3cret"

    def test_an_explicit_file_beats_the_environment(self, tmp_path: Path) -> None:
        path = tmp_path / "pw"
        path.write_text("from the file")
        env = {PASSWORD_ENV: "from the environment"}
        assert resolve_password(path, env=env) == "from the file"

    def test_the_environment_can_name_the_file_instead(self, tmp_path: Path) -> None:
        path = tmp_path / "pw"
        path.write_text("from the named file")
        env = {PASSWORD_FILE_ENV: str(path), PASSWORD_ENV: "from the environment"}
        assert resolve_password(None, env=env) == "from the named file"

    def test_the_environment_variable_is_used_when_no_file_is_named(self) -> None:
        assert resolve_password(None, env={PASSWORD_ENV: "from the environment"}) == (
            "from the environment"
        )

    def test_a_password_file_that_is_not_there_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(VaultError, match="cannot read a password from"):
            resolve_password(tmp_path / "absent", env={})

    @pytest.mark.parametrize("contents", ["", "\n", "   \n"])
    def test_an_empty_password_file_is_refused(self, tmp_path: Path, contents: str) -> None:
        path = tmp_path / "pw"
        path.write_text(contents)
        with pytest.raises(VaultError, match="cannot read a password from|is empty"):
            resolve_password(path, env={})

    def test_a_tilde_in_the_path_is_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "pw").write_text("s3cret")
        assert resolve_password(Path("~/pw"), env={}) == "s3cret"


class TestHeader:
    def test_a_header_round_trips(self) -> None:
        assert _parse_header(_format_header(DEFAULT_KDF_PARAMS)) == DEFAULT_KDF_PARAMS

    def test_the_parameters_are_written_in_a_fixed_order(self) -> None:
        """The header is the ciphertext's associated data, so its bytes have to be stable."""
        assert _format_header({"p": 1, "r": 8, "n": 16384}).endswith("n=16384,r=8,p=1")

    @pytest.mark.parametrize(
        "line",
        [
            "not a vault at all",
            "$ARCTIC_FLOW_VAULT;1.0;AES256GCM;SCRYPT",
            "$OTHER_VAULT;1.0;AES256GCM;SCRYPT;n=16384,r=8,p=1",
        ],
    )
    def test_something_that_is_not_a_header_says_so(self, line: str) -> None:
        with pytest.raises(VaultError, match="not a vault file"):
            _parse_header(line)

    def test_a_format_version_this_build_does_not_know_is_refused(self) -> None:
        with pytest.raises(VaultError, match="unsupported vault format version 2.0"):
            _parse_header(f"{MAGIC};2.0;AES256GCM;SCRYPT;n=16384,r=8,p=1")

    @pytest.mark.parametrize("suite", ["AES128GCM;SCRYPT", "AES256GCM;PBKDF2"])
    def test_a_cipher_or_kdf_this_build_does_not_know_is_refused(self, suite: str) -> None:
        with pytest.raises(VaultError, match="unsupported cipher/kdf"):
            _parse_header(f"{MAGIC};1.0;{suite};n=16384,r=8,p=1")

    @pytest.mark.parametrize("params", ["n=lots,r=8,p=1", "n,r=8,p=1"])
    def test_parameters_that_are_not_numbers_are_refused(self, params: str) -> None:
        with pytest.raises(VaultError, match="malformed key-derivation parameters"):
            _parse_header(f"{MAGIC};1.0;AES256GCM;SCRYPT;{params}")

    @pytest.mark.parametrize("params", ["n=16384,r=8", "n=16384,r=8,p=1,q=2"])
    def test_the_parameter_set_has_to_be_exactly_n_r_and_p(self, params: str) -> None:
        with pytest.raises(VaultError, match="expected n, r and p in the header"):
            _parse_header(f"{MAGIC};1.0;AES256GCM;SCRYPT;{params}")


class TestDerive:
    def test_the_same_password_and_salt_give_the_same_key(self) -> None:
        params = {"n": 16, "r": 1, "p": 1}
        assert _derive("pw", b"salt" * 4, params) == _derive("pw", b"salt" * 4, params)

    def test_a_different_salt_gives_a_different_key(self) -> None:
        params = {"n": 16, "r": 1, "p": 1}
        assert _derive("pw", b"a" * 16, params) != _derive("pw", b"b" * 16, params)

    def test_parameters_scrypt_will_not_accept_are_reported_as_vault_errors(self) -> None:
        """n has to be a power of two, and a file carrying a bad one should say that."""
        with pytest.raises(VaultError, match="unusable key-derivation parameters"):
            _derive("pw", b"salt" * 4, {"n": 3, "r": 8, "p": 1})

    def test_a_missing_parameter_is_reported_the_same_way(self) -> None:
        with pytest.raises(VaultError, match="unusable key-derivation parameters"):
            _derive("pw", b"salt" * 4, {"n": 16, "r": 1})


class TestEncrypt:
    def test_the_file_starts_with_a_readable_header(self) -> None:
        assert encrypt({"token": "abc"}, PASSWORD).startswith(MAGIC)

    def test_the_body_is_wrapped_so_it_diffs_as_text(self) -> None:
        body = encrypt({"token": "abc"}, PASSWORD).splitlines()[1:]
        assert all(len(line) <= 72 for line in body)

    def test_the_file_ends_with_a_newline(self) -> None:
        assert encrypt({"token": "abc"}, PASSWORD).endswith("\n")

    def test_two_encryptions_of_the_same_thing_differ(self) -> None:
        """A fresh salt and nonce each time, so a re-save is not a hint about the contents."""
        assert encrypt({"token": "abc"}, PASSWORD) != encrypt({"token": "abc"}, PASSWORD)

    @pytest.mark.parametrize("name", ["1token", "with-hyphen", "with space", "", "tökén"])
    def test_a_name_that_could_not_be_an_environment_variable_is_refused(self, name: str) -> None:
        """Secrets reach a step as environment variables, so the name has to be usable as one."""
        with pytest.raises(VaultError, match="is not a usable secret name"):
            encrypt({name: "value"}, PASSWORD)

    @pytest.mark.parametrize("name", ["token", "_token", "TOKEN_2", "a"])
    def test_a_usable_name_is_accepted(self, name: str) -> None:
        assert encrypt({name: "value"}, PASSWORD)


class TestDecrypt:
    def test_a_vault_round_trips(self) -> None:
        values = {"token": "abc", "other": "def"}
        assert decrypt(encrypt(values, PASSWORD), PASSWORD) == values

    def test_an_empty_vault_round_trips(self) -> None:
        assert decrypt(encrypt({}, PASSWORD), PASSWORD) == {}

    def test_a_value_survives_awkward_characters(self) -> None:
        values = {"token": "line one\nline two: with-punctuation #and a hash"}
        assert decrypt(encrypt(values, PASSWORD), PASSWORD) == values

    def test_the_wrong_password_does_not_say_which_of_the_two_it_was(self) -> None:
        """GCM cannot tell a wrong password from a tampered file, so it claims neither."""
        text = encrypt({"token": "abc"}, PASSWORD)
        with pytest.raises(VaultError, match="wrong password, or the vault file has been"):
            decrypt(text, "not the password")

    def test_editing_the_header_fails_as_tampering(self) -> None:
        """The header is the associated data. Without that it would fail as a wrong password,
        sending whoever hits it looking in the wrong place."""
        text = encrypt({"token": "abc"}, PASSWORD)
        tampered = text.replace("n=16384", "n=8192")
        with pytest.raises(VaultError, match="wrong password, or the vault file has been"):
            decrypt(tampered, PASSWORD)

    def test_editing_the_ciphertext_fails(self) -> None:
        lines = encrypt({"token": "abc"}, PASSWORD).splitlines()
        blob = bytearray(base64.b64decode("".join(lines[1:])))
        blob[-1] ^= 0xFF
        tampered = "\n".join([lines[0], base64.b64encode(bytes(blob)).decode()])
        with pytest.raises(VaultError, match="wrong password, or the vault file has been"):
            decrypt(tampered, PASSWORD)

    @pytest.mark.parametrize("text", ["", "   ", "\n\n"])
    def test_an_empty_file_is_reported_as_empty(self, text: str) -> None:
        with pytest.raises(VaultError, match="vault file is empty"):
            decrypt(text, PASSWORD)

    def test_a_body_that_is_not_base64_is_reported(self) -> None:
        with pytest.raises(VaultError, match="not valid base64"):
            decrypt(f"{_format_header(DEFAULT_KDF_PARAMS)}\nnot base64 at all!\n", PASSWORD)

    def test_a_header_with_no_body_is_reported_as_too_short(self) -> None:
        with pytest.raises(VaultError, match="too short to contain anything"):
            decrypt(f"{_format_header(DEFAULT_KDF_PARAMS)}\n", PASSWORD)

    def test_a_body_shorter_than_a_salt_and_a_nonce_is_reported(self) -> None:
        header = _format_header(DEFAULT_KDF_PARAMS)
        short = base64.b64encode(b"x" * 28).decode()
        with pytest.raises(VaultError, match="too short to contain anything"):
            decrypt(f"{header}\n{short}\n", PASSWORD)

    def test_contents_that_are_not_a_mapping_are_refused(self) -> None:
        """Encrypting cannot produce this, but a hand-built file can."""
        text = _encrypted_payload(yaml.safe_dump(["a", "b"]))
        with pytest.raises(VaultError, match="must be a mapping of name to value"):
            decrypt(text, PASSWORD)

    def test_a_value_left_blank_reads_as_an_empty_string(self) -> None:
        """`token:` with nothing after it is None in YAML, and None is not a credential."""
        assert decrypt(_encrypted_payload("token:\n"), PASSWORD) == {"token": ""}

    def test_a_value_yaml_reads_as_a_number_comes_back_as_text(self) -> None:
        assert decrypt(_encrypted_payload("pin: 42\n"), PASSWORD) == {"pin": "42"}

    def test_an_unquoted_number_with_a_leading_zero_is_read_as_octal(self) -> None:
        """A real trap for anyone hand-editing a decrypted vault: YAML 1.1 turns 0042 into
        34 long before this module sees it. `vault set` stores strings and is unaffected."""
        assert decrypt(_encrypted_payload("pin: 0042\n"), PASSWORD) == {"pin": "34"}


def _encrypted_payload(plaintext: str) -> str:
    """A vault file wrapping arbitrary YAML, which `encrypt` itself would refuse to write."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    header = _format_header(DEFAULT_KDF_PARAMS)
    salt, nonce = b"s" * 16, b"n" * 12
    key = _derive(PASSWORD, salt, DEFAULT_KDF_PARAMS)
    blob = salt + nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), header.encode())
    return f"{header}\n{base64.b64encode(blob).decode()}\n"


class TestVaultFile:
    def test_open_reads_what_save_wrote(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.vault"
        Vault(path=path, values={"token": "abc"}).save(PASSWORD)
        assert Vault.open(path, PASSWORD).values == {"token": "abc"}

    def test_the_file_is_never_world_readable(self, tmp_path: Path) -> None:
        """0600 is set as the file is created, not afterwards, so there is no window."""
        path = tmp_path / "secrets.vault"
        Vault(path=path, values={"token": "abc"}).save(PASSWORD)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_saving_over_an_existing_vault_truncates_it(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.vault"
        Vault(path=path, values={"a": "1", "b": "2", "c": "3"}).save(PASSWORD)
        Vault(path=path, values={"a": "1"}).save(PASSWORD)
        assert Vault.open(path, PASSWORD).values == {"a": "1"}

    def test_opening_a_file_that_is_not_there_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(VaultError, match="cannot read vault"):
            Vault.open(tmp_path / "absent.vault", PASSWORD)

    def test_opening_a_directory_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(VaultError, match="cannot read vault"):
            Vault.open(tmp_path, PASSWORD)

    def test_a_path_given_as_a_string_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.vault"
        Vault(path=path, values={"token": "abc"}).save(PASSWORD)
        assert Vault.open(str(path), PASSWORD).path == path


class TestSelect:
    VAULT = Vault(path=Path("secrets.vault"), values={"token": "abc", "other": "def"})

    def test_hands_back_only_what_was_asked_for(self) -> None:
        assert self.VAULT.select(["token"]) == {"token": "abc"}

    def test_asking_for_nothing_is_not_an_error(self) -> None:
        assert self.VAULT.select([]) == {}

    def test_a_name_the_vault_does_not_hold_is_reported(self) -> None:
        with pytest.raises(VaultError, match="secrets.vault has no missing"):
            self.VAULT.select(["token", "missing"])

    def test_the_error_lists_the_names_it_does_hold(self) -> None:
        """Names, never values: this text ends up in front of people."""
        message = str(pytest.raises(VaultError, self.VAULT.select, ["missing"]).value)
        assert "it holds: other, token" in message
        assert "abc" not in message

    def test_an_empty_vault_says_it_holds_nothing(self) -> None:
        empty = Vault(path=Path("v"), values={})
        with pytest.raises(VaultError, match=r"it holds: nothing"):
            empty.select(["token"])


class TestScrub:
    def test_replaces_a_secret_value_wherever_it_appears(self) -> None:
        vault = Vault(path=Path("v"), values={"token": "s3cret"})
        assert vault.scrub("sent s3cret twice: s3cret") == "sent *** twice: ***"

    def test_leaves_text_that_holds_no_secret_alone(self) -> None:
        vault = Vault(path=Path("v"), values={"token": "s3cret"})
        assert vault.scrub("nothing to hide") == "nothing to hide"

    def test_the_longest_value_goes_first(self) -> None:
        """Scrubbing the short one first would leave the tail of the long one behind."""
        vault = Vault(path=Path("v"), values={"short": "abc", "long": "abcdef"})
        assert vault.scrub("abcdef") == "***"

    def test_an_empty_value_is_not_matched_everywhere(self) -> None:
        """ "" is in every string, so scrubbing it would replace the gap between every letter."""
        vault = Vault(path=Path("v"), values={"blank": "", "token": "s3cret"})
        assert vault.scrub("a s3cret here") == "a *** here"

    def test_secret_names_are_not_scrubbed(self) -> None:
        """Only values. A trace naming the secret a step was granted is the useful part."""
        vault = Vault(path=Path("v"), values={"token": "s3cret"})
        assert vault.scrub("step 'sign' was granted token") == "step 'sign' was granted token"
