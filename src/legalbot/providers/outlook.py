"""Outlook adapter — OAuth + /me/messages delta queries.

Kept intentionally thin. msgraph-sdk calls happen behind `asyncio.to_thread` wrappers
where convenient because the SDK's auth layer is synchronous. `list_new_messages` falls
back to a full sync when the delta link is rejected with a 410/invalid-state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger
from legalbot.providers import registry
from legalbot.providers.base import (
    Cursor,
    Draft,
    MailboxCredentials,
    OAuthStartResult,
    RawAttachment,
    RawMessage,
    SendResult,
)

log = get_logger(__name__)

OUTLOOK_SCOPES = [
    "offline_access",
    "Mail.Read",
    "Mail.Send",
    "User.Read",
]


class OutlookAdapter:
    provider_id: ClassVar[str] = "outlook"
    ingestion_mode: ClassVar[str] = "poll"

    def _cfg(self) -> tuple[str, str, str, str]:
        settings = get_settings()
        if not (
            settings.MS_GRAPH_CLIENT_ID
            and settings.MS_GRAPH_CLIENT_SECRET
            and settings.MS_GRAPH_TENANT_ID
            and settings.MS_GRAPH_REDIRECT_URI
        ):
            raise RuntimeError("MS Graph OAuth credentials are not configured")
        return (
            settings.MS_GRAPH_CLIENT_ID,
            settings.MS_GRAPH_CLIENT_SECRET.get_secret_value(),
            settings.MS_GRAPH_TENANT_ID,
            settings.MS_GRAPH_REDIRECT_URI,
        )

    async def authorize_start(self, owner_user_id: str) -> OAuthStartResult:
        client_id, _, tenant, redirect = self._cfg()
        from urllib.parse import urlencode

        state = uuid.uuid4().hex
        params = {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "response_mode": "query",
            "scope": " ".join(OUTLOOK_SCOPES),
            "state": state,
        }
        url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?" + urlencode(
            params
        )
        return OAuthStartResult(authorization_url=url, state=state)

    async def authorize_complete(self, code: str, state: str) -> MailboxCredentials:
        import httpx

        client_id, client_secret, tenant, redirect = self._cfg()
        token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect,
                    "scope": " ".join(OUTLOOK_SCOPES),
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
        expires_in = int(data.get("expires_in", 3600))
        return MailboxCredentials(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            token_uri=token_url,
            scopes=OUTLOOK_SCOPES,
            expires_at=datetime.fromtimestamp(datetime.now(UTC).timestamp() + expires_in, tz=UTC),
            extra={"client_id": client_id, "client_secret": client_secret, "tenant": tenant},
        )

    async def _ensure_fresh_token(self, creds: MailboxCredentials) -> MailboxCredentials:
        if creds.expires_at and creds.expires_at > datetime.now(UTC):
            return creds
        if not creds.refresh_token:
            return creds
        import httpx

        tenant = creds.extra.get("tenant")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                creds.token_uri or f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "client_id": creds.extra.get("client_id"),
                    "client_secret": creds.extra.get("client_secret"),
                    "grant_type": "refresh_token",
                    "refresh_token": creds.refresh_token,
                    "scope": " ".join(creds.scopes or OUTLOOK_SCOPES),
                },
                timeout=30.0,
            )
        if resp.status_code != 200:
            return creds
        data = resp.json()
        expires_in = int(data.get("expires_in", 3600))
        return MailboxCredentials(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", creds.refresh_token),
            token_uri=creds.token_uri,
            scopes=creds.scopes,
            expires_at=datetime.fromtimestamp(datetime.now(UTC).timestamp() + expires_in, tz=UTC),
            extra=creds.extra,
        )

    async def list_new_messages(self, mb: Any, cursor: Cursor) -> tuple[list[RawMessage], Cursor]:
        creds = await self._ensure_fresh_token(mb.credentials_decrypted)
        delta_link: str | None = (cursor.value or {}).get("delta_link")

        import httpx

        url = delta_link or "https://graph.microsoft.com/v1.0/me/messages/delta?$top=25"
        headers = {"Authorization": f"Bearer {creds.access_token}"}
        messages: list[RawMessage] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                resp = await client.get(url, headers=headers)
                if resp.status_code in (410, 404) and delta_link:
                    # fallback to full sync
                    url = "https://graph.microsoft.com/v1.0/me/messages/delta?$top=25"
                    delta_link = None
                    continue
                resp.raise_for_status()
                data = resp.json()
                for m in data.get("value", []):
                    messages.append(_graph_to_raw(m))
                next_link = data.get("@odata.nextLink")
                if next_link:
                    url = next_link
                    continue
                new_delta = data.get("@odata.deltaLink")
                return messages, Cursor(value={"delta_link": new_delta})

    async def fetch_message(self, mb: Any, provider_message_id: str) -> RawMessage:
        creds = await self._ensure_fresh_token(mb.credentials_decrypted)
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"https://graph.microsoft.com/v1.0/me/messages/{provider_message_id}",
                headers={"Authorization": f"Bearer {creds.access_token}"},
            )
            resp.raise_for_status()
            return _graph_to_raw(resp.json())

    async def download_attachment(self, mb: Any, message_id: str, part_id: str) -> bytes:
        import base64

        import httpx

        creds = await self._ensure_fresh_token(mb.credentials_decrypted)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments/{part_id}",
                headers={"Authorization": f"Bearer {creds.access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("contentBytes")
            if content:
                return base64.b64decode(content)
            return b""

    async def send_draft(self, mb: Any, draft: Draft) -> SendResult:
        return SendResult(provider_message_id=None, ok=False, error="send not enabled v1")


def _graph_to_raw(m: dict[str, Any]) -> RawMessage:
    body = m.get("body", {})
    content = body.get("content", "")
    content_type = body.get("contentType", "text")
    text = content if content_type.lower() == "text" else None
    html = content if content_type.lower() == "html" else None

    to_recipients = [r.get("emailAddress", {}).get("address") for r in m.get("toRecipients", [])]
    cc_recipients = [r.get("emailAddress", {}).get("address") for r in m.get("ccRecipients", [])]
    from_addr = (m.get("from") or {}).get("emailAddress", {}).get("address")

    received_at = None
    if m.get("receivedDateTime"):
        try:
            received_at = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00"))
        except ValueError:
            received_at = None

    attachments = [
        RawAttachment(
            part_id=a["id"],
            name=a.get("name") or "attachment",
            mime_type=a.get("contentType") or "application/octet-stream",
            size=a.get("size"),
        )
        for a in m.get("attachments", [])
        if a.get("@odata.type", "").endswith("fileAttachment")
    ]

    return RawMessage(
        provider_message_id=m["id"],
        provider_thread_id=m.get("conversationId"),
        subject=m.get("subject"),
        body_text=text,
        body_html=html,
        from_addr=from_addr,
        to_addrs=[a for a in to_recipients if a],
        cc_addrs=[a for a in cc_recipients if a],
        in_reply_to=m.get("internetMessageHeaders", {})
        if isinstance(m.get("internetMessageHeaders"), dict)
        else None,
        headers={h.get("name"): h.get("value") for h in m.get("internetMessageHeaders", []) or []},
        received_at=received_at,
        attachments=attachments,
        raw={"graph_id": m.get("id"), "conversation_id": m.get("conversationId")},
    )


registry.register(OutlookAdapter())
