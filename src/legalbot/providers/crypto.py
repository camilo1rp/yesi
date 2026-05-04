"""Fernet-based credential encryption."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from cryptography.fernet import Fernet

from legalbot.core.config import get_settings
from legalbot.providers.base import MailboxCredentials


def _fernet() -> Fernet:
    settings = get_settings()
    return Fernet(settings.FERNET_KEY.get_secret_value().encode())


def encrypt_credentials(creds: MailboxCredentials) -> bytes:
    payload = asdict(creds)
    if payload.get("expires_at") is not None:
        payload["expires_at"] = payload["expires_at"].isoformat()
    return _fernet().encrypt(json.dumps(payload).encode("utf-8"))


def decrypt_credentials(blob: bytes) -> MailboxCredentials:
    raw = json.loads(_fernet().decrypt(blob).decode("utf-8"))
    if raw.get("expires_at") is not None:
        from datetime import datetime

        raw["expires_at"] = datetime.fromisoformat(raw["expires_at"])
    return MailboxCredentials(**raw)


def encrypt_json(value: dict[str, Any]) -> bytes:
    return _fernet().encrypt(json.dumps(value).encode("utf-8"))


def decrypt_json(blob: bytes) -> dict[str, Any]:
    return json.loads(_fernet().decrypt(blob).decode("utf-8"))
