from __future__ import annotations

import base64
import binascii
import secrets
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .db import Database


class SecretStoreUnavailable(RuntimeError):
    """Encrypted storage cannot be safely read or written."""


class SecretStore:
    _VERSION = 1

    def __init__(self, db: Database, key_file: str | Path):
        self.db = db
        self.key_file = Path(key_file)
        self._aes = None
        try:
            encoded = self.key_file.read_bytes().strip()
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32:
                raise ValueError("invalid key length")
            self._aes = AESGCM(key)
        except (OSError, ValueError, binascii.Error):
            self._aes = None

    @property
    def available(self) -> bool:
        return self._aes is not None

    def _require(self) -> AESGCM:
        if self._aes is None:
            raise SecretStoreUnavailable("encrypted secret store unavailable")
        return self._aes

    def seal(self, name: str, value: str) -> tuple[int, bytes, bytes]:
        aes = self._require()
        nonce = secrets.token_bytes(12)
        return self._VERSION, nonce, aes.encrypt(nonce, value.encode("utf-8"), name.encode("utf-8"))

    def open(self, name: str, version: int, nonce: bytes, ciphertext: bytes) -> str:
        aes = self._require()
        if version != self._VERSION:
            raise SecretStoreUnavailable("encrypted secret store unavailable")
        try:
            return aes.decrypt(nonce, ciphertext, name.encode("utf-8")).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise SecretStoreUnavailable("encrypted secret store unavailable") from exc

    def get(self, name: str) -> str | None:
        aes = self._require()
        row = self.db.get_encrypted_secret(name)
        if row is None:
            return None
        if row["version"] != self._VERSION:
            raise SecretStoreUnavailable("encrypted secret store unavailable")
        try:
            plaintext = aes.decrypt(row["nonce"], row["ciphertext"], name.encode("utf-8"))
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise SecretStoreUnavailable("encrypted secret store unavailable") from exc

    def put(self, name: str, value: str) -> None:
        version, nonce, ciphertext = self.seal(name, value)
        self.db.put_encrypted_secrets({name: (version, nonce, ciphertext)}, time.time())
