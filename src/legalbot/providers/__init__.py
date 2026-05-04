"""Email provider adapter layer."""

from legalbot.providers.base import (
    EmailProviderAdapter,
    MailboxCredentials,
    OAuthStartResult,
    RawAttachment,
    RawMessage,
    SendResult,
)
from legalbot.providers.registry import get, register

__all__ = [
    "EmailProviderAdapter",
    "MailboxCredentials",
    "OAuthStartResult",
    "RawAttachment",
    "RawMessage",
    "SendResult",
    "get",
    "register",
]
