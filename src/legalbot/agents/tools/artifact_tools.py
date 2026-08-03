"""LangChain tools that read/write the `artifact` table via ArtifactService.

State threading: tools receive `session_id` via `InjectedState` so they can scope
artifacts to the active session without the model having to restate identifiers.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

try:
    from langchain_core.tools import InjectedToolArg, tool
    from langgraph.prebuilt import InjectedState
except Exception:  # pragma: no cover — unit-test shim

    def tool(*args, **kwargs):  # type: ignore[no-redef]
        def deco(fn):
            return fn

        return deco if not args else deco(args[0])

    InjectedToolArg = object  # type: ignore[assignment]
    InjectedState = object  # type: ignore[assignment]

from legalbot.artifacts.models import ArtifactRef
from legalbot.artifacts.read_policy import maybe_summarize_artifact_content
from legalbot.artifacts.service import ArtifactService
from legalbot.db.session import async_session_factory


async def _with_service():
    sm = async_session_factory()
    async with sm() as session:
        yield ArtifactService(session), session


def _session_id_from_state(state: dict[str, Any]) -> uuid.UUID:
    sid = state.get("session_id")
    if not sid:
        raise RuntimeError("artifact tool called without session_id in state")
    return uuid.UUID(sid)


def _run_id_from_state(state: dict[str, Any]) -> uuid.UUID | None:
    rid = state.get("run_id")
    return uuid.UUID(rid) if rid else None


@tool
async def write_artifact(
    key: str,
    content: Any,
    kind: str = "analysis",
    mime: str = "application/json",
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Persist a new artifact version (keyed by `key`) for this session."""
    state = state or {}
    meta = dict(metadata or {})
    if summary is not None:
        meta["summary"] = summary
    async for svc, db in _with_service():
        ref = await svc.write(
            session_id=_session_id_from_state(state),
            key=key,
            content=content,
            kind=kind,
            mime=mime,
            metadata=meta,
            run_id=_run_id_from_state(state),
            producer="agent",
        )
        await db.commit()
        return _ref_to_dict(ref)


@tool
async def read_artifact(
    key: str,
    version: int | None = None,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Load the latest (or a specific version) of an artifact for this session."""
    state = state or {}
    async for svc, _ in _with_service():
        try:
            row, content = await svc.read(
                session_id=_session_id_from_state(state),
                key_or_id=key,
                version=version,
            )
        except Exception:
            return {"error": "not_found", "key": key}
        content, truncated = maybe_summarize_artifact_content(content, key=key)
        out: dict[str, Any] = {
            "key": row.key,
            "version": row.version,
            "kind": row.kind,
            "content": content,
            "metadata": row.meta,
        }
        if truncated:
            out["truncated"] = True
        return out


@tool
async def update_artifact(
    key: str,
    patch_or_content: Any,
    merge: str = "replace",
    kind: str = "analysis",
    mime: str = "application/json",
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Write a new version by merging (`replace` | `json_merge_patch`) onto the latest."""
    state = state or {}
    meta = dict(metadata or {})
    if summary is not None:
        meta["summary"] = summary
    async for svc, db in _with_service():
        ref = await svc.update(
            session_id=_session_id_from_state(state),
            key=key,
            patch_or_content=patch_or_content,
            merge=merge,
            kind=kind,
            mime=mime,
            metadata=meta,
            run_id=_run_id_from_state(state),
            producer="agent",
        )
        await db.commit()
        return _ref_to_dict(ref)


@tool
async def list_artifacts(
    key_prefix: str | None = None,
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> list[dict[str, Any]]:
    """List the latest versions of artifacts produced in this session."""
    state = state or {}
    async for svc, _ in _with_service():
        rows = await svc.list(session_id=_session_id_from_state(state))
        out: list[dict[str, Any]] = []
        for r in rows:
            if key_prefix and not r.key.startswith(key_prefix):
                continue
            out.append(
                {
                    "id": str(r.id),
                    "key": r.key,
                    "version": r.version,
                    "kind": r.kind,
                    "mime": r.mime,
                    "size_bytes": r.size_bytes,
                    "storage": r.storage,
                }
            )
        return out


def _ref_to_dict(ref: ArtifactRef) -> dict[str, Any]:
    return {
        "id": str(ref.id),
        "key": ref.key,
        "version": ref.version,
        "kind": ref.kind,
        "mime": ref.mime,
        "size_bytes": ref.size_bytes,
        "storage": ref.storage,
        "summary": getattr(ref, "summary", None),
    }
