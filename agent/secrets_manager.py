"""Encrypted-at-rest secrets manager using XOR + base64 (obfuscation).

Note: this provides basic obfuscation for local storage, not strong crypto.
For production, back this with a real KMS/Vault. The interface is designed so
the backend can be swapped without changing callers.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


class SecretsManager:
    def __init__(self, path: str | Path = ".secrets.enc", key: str | None = None) -> None:
        self.path = Path(path)
        self._key = (key or os.getenv("SECRETS_KEY", "default-dev-key")).encode()
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = base64.b64decode(self.path.read_bytes())
        try:
            self._data = json.loads(_xor(raw, self._key).decode())
        except (ValueError, UnicodeDecodeError):
            self._data = {}

    def _save(self) -> None:
        blob = _xor(json.dumps(self._data).encode(), self._key)
        self.path.write_bytes(base64.b64encode(blob))

    def set(self, name: str, value: str) -> None:
        self._data[name] = value
        self._save()

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._data.get(name, default)

    def delete(self, name: str) -> bool:
        if name in self._data:
            del self._data[name]
            self._save()
            return True
        return False

    def names(self) -> list[str]:
        return sorted(self._data)
