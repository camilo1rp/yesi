# `EmailProviderAdapter` contract

Adding a new email source means implementing a single Protocol and
registering it. The ingestion Celery tasks and `MailboxService` call
your adapter generically.

```python
from legalbot.providers.base import (
    EmailProviderAdapter, RawMessage, RawAttachment,
    MailboxCredentials, Cursor,
)
from legalbot.providers.registry import register_adapter

@register_adapter("myprovider")
class MyProviderAdapter(EmailProviderAdapter):
    provider_name = "myprovider"

    async def start_oauth(self, redirect_uri: str) -> str:
        "Return an authorization URL the user should visit."

    async def complete_oauth(self, code: str) -> MailboxCredentials:
        "Exchange the OAuth code for tokens. Plain secrets — the service encrypts."

    async def refresh_credentials(self, creds: MailboxCredentials) -> MailboxCredentials:
        "Rotate the access token. Only called when the existing creds expire."

    async def list_new_messages(
        self, creds: MailboxCredentials, cursor: Cursor | None, *, limit: int = 100,
    ) -> tuple[list[RawMessage], Cursor]:
        "Return new messages since `cursor`. Must be idempotent across calls."

    async def fetch_attachment(
        self, creds: MailboxCredentials, message_id: str, attachment_id: str,
    ) -> bytes:
        "Load attachment bytes. Called during ingestion into the BlobStore."

    async def send_draft(
        self, creds: MailboxCredentials, draft: ProviderDraft,
    ) -> str:
        "Deliver an outgoing message. Return provider message id."
```

## Required invariants

- `external_id` (from `RawMessage.message_id`) must be **stable per message**
  within a provider — the ingestion layer uses `UNIQUE(source, external_id)`
  for idempotency.
- `Cursor` must be JSON-serializable. It lands in `mailbox.poll_cursor`.
- `list_new_messages` must never raise on empty result — return `([], cursor)`.
- Attachment `content_id` / `filename` / `mime_type` come from the provider
  payload verbatim; the ingestion layer does not rename or transcode.

## Registration

```python
# src/legalbot/providers/myprovider.py
register_adapter("myprovider", MyProviderAdapter)
```

Then users can create a `Mailbox` with `provider="myprovider"` via
`POST /api/mailboxes` and everything else (poll, ingest, dispatch, agent,
replies) flows through without further changes.

## OAuth flow

```
client → POST /api/mailboxes        (returns mailbox_id)
client → GET  /api/mailboxes/{id}/oauth/start  → auth url
user   → authorizes with provider   → redirect hits /oauth/callback
server → POST /api/mailboxes/{id}/oauth/callback (code)
server → adapter.complete_oauth(code) → encrypt + store in mailbox_credentials
```
