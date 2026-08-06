"""An encrypted secrets file, in the spirit of ansible-vault.

A header line plus base64, so it commits next to the flow and diffs as text. The payload
is salt ‖ nonce ‖ ciphertext over a YAML mapping:

    $ARCTIC_FLOW_VAULT;1.0;AES256GCM;SCRYPT;n=16384,r=8,p=1
    NGY4YmY5Y2Q3ZTJhMWIwYzNkNGU1ZjY3ODkwYWJjZGVmMDEyMzQ1Njc4OWFiY2RlZjAxMjM0

KDF parameters live in the header, not in code, so a file encrypted today still opens
after they are raised. The header is also GCM's associated data, so editing it fails as
tampering rather than as a wrong password.

AES-256-GCM over AES-CTR-plus-HMAC, and scrypt over PBKDF2: no separate verify step to get
wrong, and memory-hardness against offline GPU attack. New code, no compatibility to keep.

Nothing here decides *who* may read a secret. That is the engine.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
import re
import secrets as secrets_module
import textwrap
from dataclasses import dataclass
from pathlib import Path

import yaml
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = "$ARCTIC_FLOW_VAULT"
FORMAT_VERSION = "1.0"
CIPHER = "AES256GCM"
KDF = "SCRYPT"

SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32
WRAP_COLUMNS = 72

# Raising these only affects files written from now on; existing files carry their own.
DEFAULT_KDF_PARAMS = {"n": 16384, "r": 8, "p": 1}

PASSWORD_ENV = "ATF_VAULT_PASSWORD"
PASSWORD_FILE_ENV = "ATF_VAULT_PASSWORD_FILE"

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class VaultError(RuntimeError):
    """The vault could not be read, written, or unlocked."""


def resolve_password(password_file: Path | None = None, env: dict[str, str] | None = None) -> str:
    """First of: an explicit file, the environment, or a prompt.

    There is deliberately no flag for one: it would land in shell history and the
    process list.
    """
    env = dict(os.environ) if env is None else env

    for source in (password_file, env.get(PASSWORD_FILE_ENV)):
        if not source:
            continue
        path = Path(source).expanduser()
        try:
            password = path.read_text().splitlines()[0].strip()
        except (OSError, IndexError) as exc:
            raise VaultError(f"cannot read a password from {path}: {exc}") from exc
        if not password:
            raise VaultError(f"{path} is empty")
        return password

    if env.get(PASSWORD_ENV):
        return env[PASSWORD_ENV]

    try:
        password = getpass.getpass("Vault password: ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise VaultError("no vault password given") from exc
    if not password:
        raise VaultError("no vault password given")
    return password


def _derive(password: str, salt: bytes, params: dict[str, int]) -> bytes:
    try:
        return hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=params["n"],
            r=params["r"],
            p=params["p"],
            dklen=KEY_BYTES,
            # OpenSSL refuses a derivation that needs more than maxmem, and its default
            # is lower than some parameter sets need, so size it from the parameters.
            maxmem=128 * params["n"] * params["r"] * 2,
        )
    except (KeyError, ValueError) as exc:
        raise VaultError(f"unusable key-derivation parameters: {exc}") from exc


def _format_header(params: dict[str, int]) -> str:
    joined = ",".join(f"{key}={params[key]}" for key in ("n", "r", "p"))
    return ";".join([MAGIC, FORMAT_VERSION, CIPHER, KDF, joined])


def _parse_header(line: str) -> dict[str, int]:
    parts = line.strip().split(";")
    if len(parts) != 5 or parts[0] != MAGIC:
        raise VaultError("not a vault file (no $ARCTIC_FLOW_VAULT header)")
    _, version, cipher, kdf, joined = parts
    if version != FORMAT_VERSION:
        raise VaultError(f"unsupported vault format version {version}")
    if cipher != CIPHER or kdf != KDF:
        raise VaultError(f"unsupported cipher/kdf combination {cipher}/{kdf}")
    try:
        params = {k: int(v) for k, v in (pair.split("=", 1) for pair in joined.split(","))}
    except ValueError as exc:
        raise VaultError(f"malformed key-derivation parameters: {joined}") from exc
    if set(params) != {"n", "r", "p"}:
        raise VaultError(f"expected n, r and p in the header, got {', '.join(sorted(params))}")
    return params


def encrypt(values: dict[str, str], password: str) -> str:
    """Serialise a mapping to the on-disk vault format."""
    for name in values:
        if not NAME.match(str(name)):
            raise VaultError(
                f"'{name}' is not a usable secret name. Letters, digits and underscores "
                "only, not starting with a digit, so it can also be an environment variable"
            )

    plaintext = yaml.safe_dump(dict(values), sort_keys=True, default_flow_style=False).encode()
    salt = secrets_module.token_bytes(SALT_BYTES)
    nonce = secrets_module.token_bytes(NONCE_BYTES)
    key = _derive(password, salt, DEFAULT_KDF_PARAMS)

    # Passed as associated data, so the header is authenticated despite not being
    # encrypted. Without this an edited KDF parameter derives a different key and fails
    # with "wrong password", sending whoever hits it looking in the wrong place.
    header = _format_header(DEFAULT_KDF_PARAMS)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, header.encode())

    body = base64.b64encode(salt + nonce + ciphertext).decode()
    return "\n".join([header, *textwrap.wrap(body, WRAP_COLUMNS), ""])


def decrypt(text: str, password: str) -> dict[str, str]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise VaultError("vault file is empty")

    header = lines[0].strip()
    params = _parse_header(header)
    try:
        blob = base64.b64decode("".join(lines[1:]), validate=True)
    except (ValueError, TypeError) as exc:
        raise VaultError(f"vault body is not valid base64: {exc}") from exc
    if len(blob) <= SALT_BYTES + NONCE_BYTES:
        raise VaultError("vault body is too short to contain anything")

    salt = blob[:SALT_BYTES]
    nonce = blob[SALT_BYTES : SALT_BYTES + NONCE_BYTES]
    ciphertext = blob[SALT_BYTES + NONCE_BYTES :]
    key = _derive(password, salt, params)

    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, header.encode())
    except InvalidTag as exc:
        # GCM cannot tell a wrong password from a tampered file: both fail the same
        # check. Say both rather than guessing.
        raise VaultError("wrong password, or the vault file has been modified") from exc

    loaded = yaml.safe_load(plaintext.decode()) or {}
    if not isinstance(loaded, dict):
        raise VaultError("vault contents must be a mapping of name to value")
    return {str(k): "" if v is None else str(v) for k, v in loaded.items()}


@dataclass
class Vault:
    """An opened vault. `values` is plaintext, so keep instances short-lived."""

    path: Path
    values: dict[str, str]

    @classmethod
    def open(cls, path: Path, password: str) -> Vault:
        try:
            text = Path(path).read_text()
        except OSError as exc:
            raise VaultError(f"cannot read vault {path}: {exc}") from exc
        return cls(path=Path(path), values=decrypt(text, password))

    def save(self, password: str) -> None:
        path = Path(self.path)
        text = encrypt(self.values, password)
        # 0600 before anything is written, so the plaintext-derived file is never
        # briefly world-readable.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)

    def select(self, names: list[str]) -> dict[str, str]:
        """The named secrets, or an error naming the ones that are missing.

        The error deliberately lists what is available: names, never values.
        """
        missing = [name for name in names if name not in self.values]
        if missing:
            available = ", ".join(sorted(self.values)) or "nothing"
            raise VaultError(
                f"vault {self.path.name} has no {', '.join(missing)} (it holds: {available})"
            )
        return {name: self.values[name] for name in names}

    def scrub(self, text: str) -> str:
        """Replace every secret value in a string with ***.

        For messages and traces, not step results: a tool whose output legitimately
        contains a token still returns it to the next step, but nothing landing in a log
        or an error should carry one.
        """
        for value in sorted(self.values.values(), key=len, reverse=True):
            if value and value in text:
                text = text.replace(value, "***")
        return text
