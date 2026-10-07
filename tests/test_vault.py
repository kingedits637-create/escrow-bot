"""Spec §18: vault encrypt/decrypt round-trip."""
import pytest
from cryptography.fernet import Fernet

from bot.utils.vault import Vault, VaultError


def test_round_trip():
    v = Vault(Fernet.generate_key().decode())
    blob = v.encrypt("user: seller01\npass: s3cret!")
    assert blob != "user: seller01\npass: s3cret!"
    assert v.decrypt(blob) == "user: seller01\npass: s3cret!"


def test_wrong_key_fails():
    v1 = Vault(Fernet.generate_key().decode())
    v2 = Vault(Fernet.generate_key().decode())
    blob = v1.encrypt("secret")
    with pytest.raises(VaultError):
        v2.decrypt(blob)


def test_missing_key_rejected():
    with pytest.raises(VaultError):
        Vault("")


def test_tampered_blob_fails():
    v = Vault(Fernet.generate_key().decode())
    blob = v.encrypt("secret")
    with pytest.raises(VaultError):
        v.decrypt(blob[:-4] + "AAAA")
