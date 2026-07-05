"""API key management and simple role-based access control.

Stores hashed API keys and maps them to roles/permissions. Suitable for gating
access to the agent when exposed as a service.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_key(prefix: str = "tak") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"chat", "tools", "admin", "read", "write"},
    "user": {"chat", "tools", "read"},
    "readonly": {"chat", "read"},
}


@dataclass
class ApiKeyRecord:
    key_hash: str
    role: str = "user"
    label: str = ""


class AuthManager:
    """Registers API keys and checks permissions."""

    def __init__(self) -> None:
        self._keys: dict[str, ApiKeyRecord] = {}

    def register(self, raw_key: str, role: str = "user", label: str = "") -> None:
        if role not in ROLE_PERMISSIONS:
            raise ValueError(f"Unknown role: {role}")
        record = ApiKeyRecord(key_hash=hash_key(raw_key), role=role, label=label)
        self._keys[record.key_hash] = record

    def create_key(self, role: str = "user", label: str = "") -> str:
        raw = generate_key()
        self.register(raw, role=role, label=label)
        return raw

    def authenticate(self, raw_key: str) -> ApiKeyRecord | None:
        target = hash_key(raw_key)
        for stored_hash, record in self._keys.items():
            if hmac.compare_digest(stored_hash, target):
                return record
        return None

    def has_permission(self, raw_key: str, permission: str) -> bool:
        record = self.authenticate(raw_key)
        if record is None:
            return False
        return permission in ROLE_PERMISSIONS.get(record.role, set())
