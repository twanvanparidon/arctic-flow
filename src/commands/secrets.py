"""Commands that act on a vault, plus the one a flow needs: opening it.

Resolving a password can *prompt*, and where that prompt appears is a front end's
business. So a password arrives either as the string it already is, or as a callable
asked only if the command gets far enough to need one. That is what keeps `vault create`
from prompting before it tells you the file exists, and a flow with no secrets from
prompting at all.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from commands.results import SecretListing, SecretSet, VaultContents, VaultCreated
from paths.resolver import Paths
from vault.vault import Vault, VaultError, resolve_password

# Called with no arguments, returns the password.
PasswordProvider = Callable[[], str]

# A password, or a way of getting one later. Pass the string when you have it; pass a
# callable when asking is expensive, interactive, or might turn out to be unnecessary.
Password = str | PasswordProvider


def unlock(password: Password | None) -> str:
    """The password itself, asking for it now if that is what was handed over.

    `None` means "however the vault module resolves one": a file named in the
    environment, `$ATF_VAULT_PASSWORD`, or a prompt.
    """
    if password is None:
        return resolve_password()
    return password if isinstance(password, str) else password()


def open_vault(
    reference: str | None, paths: Paths, password: Password | None = None
) -> Vault | None:
    """Open the vault at `reference`, relative to the workspace unless absolute.

    None is not an error: a flow declaring no secrets needs no vault and must not prompt.
    Nothing asks for a password before that check, which is why `password` may be a
    callable.
    """
    if not reference:
        return None
    path = Path(reference)
    if not path.is_absolute():
        path = paths.workspace / path
    return Vault.open(path, unlock(password))


def create_vault(
    file: Path, paths: Paths, values: dict[str, str], password: Password, *, force: bool = False
) -> VaultCreated:
    """Write a new vault holding `values`.

    The `force` check runs before the password is touched, so a mistyped filename is
    answered with the mistake rather than a prompt. Values are coerced to strings: a YAML
    mapping yields ints and bools, and a secret is text by the time it reaches a step.
    """
    if file.exists() and not force:
        raise VaultError(f"{file} already exists: pass --force to replace it")

    coerced = {str(name): str(value) for name, value in values.items()}
    Vault(path=file, values=coerced).save(unlock(password))
    return VaultCreated(path=file, display=paths.display(file), count=len(coerced))


def set_secret(file: Path, paths: Paths, name: str, value: str, password: Password) -> SecretSet:
    """Add or replace one secret, leaving the rest of the vault as it was.

    An empty value is refused rather than stored. It is what an abandoned prompt or an
    empty pipe produces, and a blank credential fails later, somewhere else.
    """
    if not value:
        raise VaultError(f"no value given for {name}")

    resolved = unlock(password)
    vault = Vault.open(file, resolved)
    existed = name in vault.values
    vault.values[name] = value
    vault.save(resolved)
    return SecretSet(path=file, display=paths.display(file), name=name, replaced=existed)


def secret_names(file: Path, paths: Paths, password: Password) -> SecretListing:
    """The names in a vault, without their values."""
    vault = Vault.open(file, unlock(password))
    return SecretListing(path=file, display=paths.display(file), names=tuple(sorted(vault.values)))


def vault_contents(file: Path, paths: Paths, password: Password) -> VaultContents:
    """Decrypt a vault. The result holds real secrets: see `VaultContents`."""
    vault = Vault.open(file, unlock(password))
    return VaultContents(path=file, display=paths.display(file), values=dict(vault.values))
