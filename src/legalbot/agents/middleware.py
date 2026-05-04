"""Custom middleware: message-projection after_model hook."""

from __future__ import annotations

import uuid
from typing import Any

try:
    from langchain.agents.middleware import AgentMiddleware
except Exception:  # pragma: no cover
    AgentMiddleware = object  # type: ignore[assignment]

from legalbot.core.logging import get_logger
from legalbot.db.session import async_session_factory
from legalbot.services.session import SessionService

log = get_logger(__name__)


class SessionProjectionMiddleware(AgentMiddleware):  # type: ignore[misc]
    """after_model hook that projects the latest assistant response into `session_message`."""

    async def aafter_model(self, request: Any, response: Any) -> Any:
        try:
            state = getattr(request, "state", {}) or {}
            session_id = state.get("session_id")
            run_id = state.get("run_id")
            if not session_id:
                return response
            message = getattr(response, "message", None) or getattr(response, "result", None)
            text = self._text_of(message)
            if not text:
                return response
            sm = async_session_factory()
            async with sm() as db:
                svc = SessionService(db)
                await svc.append_message(
                    uuid.UUID(session_id),
                    role="assistant",
                    content=text,
                    run_id=uuid.UUID(run_id) if run_id else None,
                )
                await db.commit()
        except Exception as e:  # pragma: no cover
            log.warning("session.projection_failed", error=str(e))
        return response

    def _text_of(self, message: Any) -> str | None:
        if message is None:
            return None
        if isinstance(message, str):
            return message
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            out: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    out.append(str(block["text"]))
            return "\n".join(out) if out else None
        return None
