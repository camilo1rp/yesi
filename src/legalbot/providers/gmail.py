"""Gmail adapter — OAuth + users.history.list + users.messages.get.

This adapter is functional but the real OAuth + delta flows require valid Google
credentials. When `GOOGLE_OAUTH_CLIENT_ID` is unset the adapter still registers but
`authorize_start` / `list_new_messages` raise a helpful RuntimeError.
"""

from __future__ import annotations

import asyncio
import base64
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

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class GmailAdapter:
    provider_id: ClassVar[str] = "gmail"
    ingestion_mode: ClassVar[str] = "poll"

    def _require_config(self) -> tuple[str, str, str]:
        settings = get_settings()
        if not (
            settings.GOOGLE_OAUTH_CLIENT_ID
            and settings.GOOGLE_OAUTH_CLIENT_SECRET
            and settings.GOOGLE_OAUTH_REDIRECT_URI
        ):
            raise RuntimeError("Gmail OAuth credentials are not configured")
        return (
            settings.GOOGLE_OAUTH_CLIENT_ID,
            settings.GOOGLE_OAUTH_CLIENT_SECRET.get_secret_value(),
            settings.GOOGLE_OAUTH_REDIRECT_URI,
        )

    async def authorize_start(self, owner_user_id: str) -> OAuthStartResult:
        client_id, _, redirect = self._require_config()
        from google_auth_oauthlib.flow import Flow

        def _build() -> OAuthStartResult:
            flow = Flow.from_client_config(
                {
                    "web": {
                        "client_id": client_id,
                        "client_secret": self._require_config()[1],
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": [redirect],
                    }
                },
                scopes=GMAIL_SCOPES,
                redirect_uri=redirect,
            )
            state = uuid.uuid4().hex
            url, _ = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
                state=state,
            )
            return OAuthStartResult(authorization_url=url, state=state)

        return await asyncio.to_thread(_build)

    async def authorize_complete(self, code: str, state: str) -> MailboxCredentials:
        client_id, client_secret, redirect = self._require_config()
        from google_auth_oauthlib.flow import Flow

        def _complete() -> MailboxCredentials:
            flow = Flow.from_client_config(
                {
                    "web": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": [redirect],
                    }
                },
                scopes=GMAIL_SCOPES,
                redirect_uri=redirect,
            )
            flow.fetch_token(code=code)
            creds = flow.credentials
            return MailboxCredentials(
                access_token=creds.token,
                refresh_token=creds.refresh_token,
                token_uri=creds.token_uri,
                scopes=list(creds.scopes or GMAIL_SCOPES),
                expires_at=creds.expiry.replace(tzinfo=UTC) if creds.expiry else None,
                extra={"client_id": client_id, "client_secret": client_secret},
            )

        return await asyncio.to_thread(_complete)

    def _service(self, creds: MailboxCredentials) -> Any:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        g = Credentials(
            token=creds.access_token,
            refresh_token=creds.refresh_token,
            token_uri=creds.token_uri or "https://oauth2.googleapis.com/token",
            client_id=creds.extra.get("client_id"),
            client_secret=creds.extra.get("client_secret"),
            scopes=creds.scopes,
        )
        return build("gmail", "v1", credentials=g, cache_discovery=False)

    async def list_new_messages(self, mb: Any, cursor: Cursor) -> tuple[list[RawMessage], Cursor]:
        creds = mb.credentials_decrypted  # expected to be set by the caller
        service = self._service(creds)
        last_history_id: str | None = (cursor.value or {}).get("history_id")

        def _sync() -> tuple[list[RawMessage], Cursor]:
            ids: list[str] = []
            if last_history_id:
                try:
                    resp = (
                        service.users()
                        .history()
                        .list(
                            userId="me",
                            startHistoryId=last_history_id,
                            historyTypes=["messageAdded"],
                        )
                        .execute()
                    )
                    for h in resp.get("history", []):
                        for m in h.get("messagesAdded", []):
                            mid = m.get("message", {}).get("id")
                            if mid:
                                ids.append(mid)
                    new_history_id = resp.get("historyId", last_history_id)
                except Exception:  # pragma: no cover — delta expiry fallback
                    ids = []
                    new_history_id = last_history_id
            else:
                resp = service.users().messages().list(userId="me", maxResults=10).execute()
                ids = [m["id"] for m in resp.get("messages", [])]
                profile = service.users().getProfile(userId="me").execute()
                new_history_id = profile.get("historyId")

            messages: list[RawMessage] = []
            for mid in ids:
                msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
                messages.append(_gmail_to_raw(msg))
            return messages, Cursor(value={"history_id": new_history_id})

        return await asyncio.to_thread(_sync)

    async def fetch_message(self, mb: Any, provider_message_id: str) -> RawMessage:
        creds = mb.credentials_decrypted
        service = self._service(creds)

        def _sync() -> RawMessage:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=provider_message_id, format="full")
                .execute()
            )
            return _gmail_to_raw(msg)

        return await asyncio.to_thread(_sync)

    async def download_attachment(self, mb: Any, message_id: str, part_id: str) -> bytes:
        creds = mb.credentials_decrypted
        service = self._service(creds)

        def _sync() -> bytes:
            resp = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=part_id)
                .execute()
            )
            return _b64url_decode(resp["data"])

        return await asyncio.to_thread(_sync)

    async def send_draft(self, mb: Any, draft: Draft) -> SendResult:
        return SendResult(provider_message_id=None, ok=False, error="send not enabled v1")


def _gmail_headers(headers: list[dict[str, Any]]) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in headers}


def _extract_body(payload: dict[str, Any]) -> tuple[str | None, str | None, list[RawAttachment]]:
    text: str | None = None
    html: str | None = None
    attachments: list[RawAttachment] = []

    def _walk(p: dict[str, Any]) -> None:
        nonlocal text, html
        mime = p.get("mimeType", "")
        body = p.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data and text is None:
            text = _b64url_decode(data).decode("utf-8", errors="replace")
        elif mime == "text/html" and data and html is None:
            html = _b64url_decode(data).decode("utf-8", errors="replace")
        elif body.get("attachmentId"):
            attachments.append(
                RawAttachment(
                    part_id=body["attachmentId"],
                    name=p.get("filename") or "attachment",
                    mime_type=mime,
                    size=body.get("size"),
                )
            )
        for part in p.get("parts", []) or []:
            _walk(part)

    _walk(payload)
    return text, html, attachments


def _gmail_to_raw(msg: dict[str, Any]) -> RawMessage:
    payload = msg.get("payload", {})
    headers = _gmail_headers(payload.get("headers", []))
    text, html, attachments = _extract_body(payload)
    internal = int(msg.get("internalDate", "0") or 0) / 1000
    received_at = datetime.fromtimestamp(internal, tz=UTC) if internal else None
    return RawMessage(
        provider_message_id=msg["id"],
        provider_thread_id=msg.get("threadId"),
        subject=headers.get("subject"),
        body_text=text,
        body_html=html,
        from_addr=headers.get("from"),
        to_addrs=[a.strip() for a in (headers.get("to") or "").split(",") if a.strip()],
        cc_addrs=[a.strip() for a in (headers.get("cc") or "").split(",") if a.strip()],
        bcc_addrs=[a.strip() for a in (headers.get("bcc") or "").split(",") if a.strip()],
        in_reply_to=headers.get("in-reply-to"),
        references=[r.strip() for r in (headers.get("references") or "").split() if r.strip()],
        headers=headers,
        received_at=received_at,
        attachments=attachments,
        raw={"gmail_id": msg.get("id"), "snippet": msg.get("snippet")},
    )


registry.register(GmailAdapter())
