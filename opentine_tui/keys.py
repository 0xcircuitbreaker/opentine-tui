"""Ed25519 signing keys the dashboard can use without a terminal prompt.

opentine 0.41 stores new private keys as encrypted PKCS#8 (PBES2/AES-256) and
looks for the passphrase in ``OPENTINE_KEY_PASSPHRASE``, the OS keyring, and —
last — a ``getpass`` prompt on the terminal. Inside a running Textual app that
prompt would seize the screen the app is drawing on, so this module never lets
opentine ask: the passphrase comes from the dashboard's own (masked) field or
from the environment, and a missing one is an error the dialog can show.

Plaintext-hex keys (0.40 and earlier) still load, and are reported as such so
the user can encrypt them.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PASSPHRASE_ENV = "OPENTINE_KEY_PASSPHRASE"
PEM_ENCRYPTED = b"-----BEGIN ENCRYPTED PRIVATE KEY-----"
#: The dashboard refuses a trivially short passphrase for a new key file.
MIN_PASSPHRASE = 8
#: A key file is a few hundred bytes; anything bigger is not one.
MAX_KEY_BYTES = 64 * 1024


class KeyError_(ValueError):
    """A key that cannot be loaded or written, with a message fit to show."""


@dataclass(slots=True)
class LoadedKey:
    signer: Any
    plaintext: bool


def _read(path: Path) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise KeyError_(f"cannot read {path}: {exc.strerror or exc}") from exc
    if size > MAX_KEY_BYTES:
        raise KeyError_(f"{path} is too large to be a key file")
    return path.read_bytes()


def _secret(passphrase: str | None) -> bytes | None:
    value = passphrase or os.environ.get(PASSPHRASE_ENV) or ""
    return value.encode() if value else None


def is_encrypted(path: str | Path) -> bool:
    try:
        return _read(Path(path).expanduser()).lstrip().startswith(PEM_ENCRYPTED)
    except KeyError_:
        return False


def load_signing_key(path: str | Path, passphrase: str | None = None) -> LoadedKey:
    """The ``SignerKey`` opentine signs with, decrypted here — never prompted for."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from opentine import _key_store, _signer
    from opentine._signing_keys import load_ed25519_private

    target = Path(path).expanduser()
    raw = _read(target)
    plaintext = False
    if _key_store.is_pem(raw):
        secret = None
        if raw.lstrip().startswith(PEM_ENCRYPTED):
            secret = _secret(passphrase)
            if secret is None:
                raise KeyError_(
                    f"{target.name} is encrypted: enter its passphrase (or set {PASSPHRASE_ENV})."
                )
        try:
            private = serialization.load_pem_private_key(raw, password=secret)
        except (TypeError, ValueError) as exc:
            raise KeyError_(
                f"cannot decrypt {target.name}: wrong passphrase or damaged key"
            ) from exc
        if not isinstance(private, Ed25519PrivateKey):
            raise KeyError_(f"{target.name} is not an Ed25519 private key")
    else:
        try:
            private = load_ed25519_private(raw.decode().strip())
        except Exception as exc:
            raise KeyError_(f"{target.name} is not an Ed25519 private key") from exc
        plaintext = True
    return LoadedKey(_signer.SignerKey(_signer.LocalSigner(private, f"file:{target}")), plaintext)


def generate(passphrase: str | None, *, unencrypted: bool = False) -> tuple[bytes, str]:
    """``(private file bytes, public hex)`` for a new Ed25519 key.

    Encrypted unless the caller explicitly asks for the deprecated plaintext form.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from opentine._key_store import encrypted_pem

    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes_raw().hex()
    if unencrypted:
        return (private.private_bytes_raw().hex() + "\n").encode(), public_hex
    secret = _secret(passphrase)
    if secret is None:
        raise KeyError_(
            f"Choose a passphrase (or set {PASSPHRASE_ENV}); a plaintext key needs "
            "'unencrypted' turned on explicitly."
        )
    if len(secret) < MIN_PASSPHRASE:
        raise KeyError_(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    return encrypted_pem(private, secret), public_hex


def encrypt_existing(path: str | Path, passphrase: str | None) -> bytes:
    """The encrypted PEM for an existing plaintext-hex key (``tine key encrypt``)."""
    from opentine._key_store import encrypted_pem
    from opentine._signing_keys import load_ed25519_private

    target = Path(path).expanduser()
    raw = _read(target)
    if raw.lstrip().startswith(PEM_ENCRYPTED):
        raise KeyError_(f"{target.name} is already encrypted")
    secret = _secret(passphrase)
    if secret is None or len(secret) < MIN_PASSPHRASE:
        raise KeyError_(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        private = load_ed25519_private(raw.decode().strip())
    return encrypted_pem(private, secret)
