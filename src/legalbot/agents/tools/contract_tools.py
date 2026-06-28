"""Contract tools: deterministic type lookup, template filling, example retrieval.

These wrap the deterministic ``legalbot.contracts`` registry. ``fill_contract_template``
refuses to write anything unless every required field is present, so the only way a
``contracts/draft`` artifact exists is if it was rendered from complete, validated input.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

try:
    from langchain_core.tools import tool
    from langgraph.prebuilt import InjectedState
except Exception:  # pragma: no cover — test shim

    def tool(*args, **kwargs):  # type: ignore[no-redef]
        def deco(fn):
            return fn

        return deco if not args else deco(args[0])

    InjectedState = object  # type: ignore[assignment]

from legalbot.contracts import (
    TemplateRenderError,
    classify_contract_type,
    get_contract_type,
    missing_fields,
    render_template,
    template_placeholders,
)
from legalbot.contracts import (
    list_contract_types as _list_contract_types,
)
from legalbot.contracts import (
    search_contract_examples as _search_contract_examples,
)
from legalbot.db.session import async_session_factory

CONTRACT_DRAFT_KEY = "contracts/draft"


def _session_id(state: dict[str, Any]) -> uuid.UUID:
    sid = state.get("session_id")
    if not sid:
        raise RuntimeError("contract tool called without session_id in state")
    return uuid.UUID(sid)


def _requirements(type_id: str) -> dict[str, Any]:
    ct = get_contract_type(type_id)
    if ct is None:
        return {}
    return {
        "type_id": ct.type_id,
        "display_name": ct.display_name,
        "required_fields": [
            {"name": f.name, "description": f.description, "example": f.example}
            for f in ct.required_fields
        ],
        "template_placeholders": template_placeholders(ct.type_id),
    }


@tool
def list_contract_types() -> dict[str, Any]:
    """List every supported contract type with its required fields and aliases.

    Use this first to confirm the requested contract is supported and to learn
    which fields must be gathered before the template can be filled.
    """
    return {"contract_types": _list_contract_types()}


@tool
def classify_contract(hint: str) -> dict[str, Any]:
    """Map a free-text description to a supported contract type id.

    Returns ``{"type_id": <id|null>, "candidates": [...]}``. A null ``type_id``
    with multiple ``candidates`` means the request is ambiguous; with no
    candidates it means the type is unsupported.
    """
    return classify_contract_type(hint)


@tool
def get_contract_requirements(contract_type: str) -> dict[str, Any]:
    """Return the required fields and template placeholders for a contract type."""
    req = _requirements(contract_type)
    if not req:
        return {
            "error": "unsupported_type",
            "contract_type": contract_type,
            "supported": [t["type_id"] for t in _list_contract_types()],
        }
    return req


@tool
async def fill_contract_template(
    contract_type: str,
    field_values: dict[str, Any],
    state: Annotated[dict, InjectedState] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Deterministically render a contract from `field_values` and persist it.

    Writes nothing unless the type is supported and every required field is
    present. On success, stores a ``contracts/draft`` artifact (kind="contract")
    holding the rendered Markdown plus the field values, and returns its key.
    """
    state = state or {}
    field_values = field_values or {}

    if get_contract_type(contract_type) is None:
        return {
            "status": "unsupported_type",
            "contract_type": contract_type,
            "supported": [t["type_id"] for t in _list_contract_types()],
        }

    missing = missing_fields(contract_type, field_values)
    if missing:
        return {
            "status": "missing_fields",
            "contract_type": contract_type,
            "missing": missing,
            "required_fields": _requirements(contract_type)["required_fields"],
        }

    try:
        markdown = render_template(contract_type, field_values)
    except TemplateRenderError as exc:
        return {"status": "render_error", "contract_type": contract_type, "detail": str(exc)}

    ct = get_contract_type(contract_type)
    content = {
        "contract_type": ct.type_id,
        "display_name": ct.display_name,
        "field_values": field_values,
        "markdown": markdown,
    }

    from legalbot.artifacts.service import ArtifactService

    sm = async_session_factory()
    async with sm() as db:
        art_svc = ArtifactService(db)
        ref = await art_svc.write(
            session_id=_session_id(state),
            key=CONTRACT_DRAFT_KEY,
            content=content,
            kind="contract",
            mime="application/json",
            producer="agent",
            run_id=uuid.UUID(state["run_id"]) if state.get("run_id") else None,
            metadata={"contract_type": ct.type_id, "display_name": ct.display_name},
        )
        await db.commit()
        return {
            "status": "filled",
            "contract_type": ct.type_id,
            "artifact_key": ref.key,
            "artifact_id": str(ref.id),
            "version": ref.version,
        }


@tool
def search_contract_examples(contract_type: str, query: str, k: int = 3) -> dict[str, Any]:
    """Retrieve partial snippets of reference contracts of the same type.

    Use this to compare the drafted contract against established patterns. Returns
    up to `k` ranked snippets, each with its source file and a relevance score.
    """
    results = _search_contract_examples(contract_type, query, k=k)
    return {"contract_type": contract_type, "query": query, "results": results}
