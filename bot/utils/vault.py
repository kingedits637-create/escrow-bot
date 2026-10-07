"""Fernet-encrypted credential vault. The key lives ONLY in .env (FERNET_KEY).

Usage:
    from bot.utils.vault import Vault
    vault = Vault(settings.fernet_key)
    blob = vault.encrypt("user: x\\npass: y")
    plain = vault.decrypt(blob)

CLI:  python -m bot.utils.vault genkey   -> prints a fresh key for .env
"""
from __future__ import annotations

import sys

from cryptography.fernet import Fernet, InvalidToken


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, key: str) -> None:
        if not key:
            raise VaultError("FERNET_KEY is not configured")
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as exc:
            raise VaultError(f"Invalid FERNET_KEY: {exc}") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, blob: str) -> str:
        try:
            return self._fernet.decrypt(blob.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise VaultError("Could not decrypt vault blob (wrong key or corrupted data)") from exc


def _genkey() -> None:
    print(Fernet.generate_key().decode())


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "genkey":
        _genkey()
    else:
        print("usage: python -m bot.utils.vault genkey")
