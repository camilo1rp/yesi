"""Phase 2: build graph projections from pipeline artifacts (+ optional LLM)."""

from __future__ import annotations

import json
from typing import Any

from legalbot.core.config import get_settings
from legalbot.core.logging import get_logger
from legalbot.memory.ontology import (
    EntityType,
    GraphProjection,
    artifact_canonical_key,
    attachment_filename_key,
    infer_entity_type_from_name,
    is_party_field_name,
    law_area_canonical_key,
    LlmGraphExtraction,
    normalize_name,
    organization_canonical_key,
    contract_canonical_key,
    ProjectionEdge,
    ProjectionEntity,
    Relation,
)

log = get_logger(__name__)

EMAIL_SRC_KEY = "__email__"


def _parse_entity_entry(raw: Any) -> tuple[EntityType, str] | None:
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            return None
        return infer_entity_type_from_name(name), name
    if isinstance(raw, dict):
        name = str(raw.get("name") or raw.get("canonical_name") or "").strip()
        if not name:
            return None
        type_raw = raw.get("type") or raw.get("entity_type")
        if type_raw:
            try:
                etype = EntityType(str(type_raw))
            except ValueError:
                etype = infer_entity_type_from_name(name)
        else:
            etype = infer_entity_type_from_name(name)
        return etype, name
    return None


def _entity_key(etype: EntityType, name: str, attributes: dict[str, Any] | None = None) -> str:
    attrs = attributes or {}
    if etype == EntityType.ORGANIZATION:
        return organization_canonical_key(name, attrs.get("domain"))
    if etype == EntityType.PERSON:
        email = attrs.get("email")
        if email:
            return f"email:{str(email).strip().lower()}"
        return f"name:{normalize_name(name)}"
    if etype == EntityType.CONTRACT:
        parties = attrs.get("parties") or []
        ctype = attrs.get("contract_type") or "unknown"
        return contract_canonical_key(str(ctype), list(parties))
    return f"name:{normalize_name(name)}"


def build_projection_from_artifacts(artifacts: dict[str, Any]) -> GraphProjection:
    """Deterministic projection from known artifact shapes (no LLM)."""
    projection = GraphProjection()
    entity_by_key: dict[tuple[str, str], ProjectionEntity] = {}

    def add(
        *,
        entity_type: EntityType,
        canonical_name: str,
        canonical_key: str | None = None,
        attributes: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
        snippet: str | None = None,
        confidence: float = 0.9,
        origin: str = "artifact",
    ) -> ProjectionEntity:
        attrs = attributes or {}
        key = canonical_key or _entity_key(entity_type, canonical_name, attrs)
        bucket = (str(entity_type), key)
        if bucket in entity_by_key:
            return entity_by_key[bucket]
        ent = ProjectionEntity(
            entity_type=entity_type,
            canonical_key=key,
            canonical_name=canonical_name.strip(),
            attributes=attrs,
            aliases=list(aliases or []),
            snippet=snippet,
            confidence=confidence,
            origin=origin,
        )
        entity_by_key[bucket] = ent
        projection.entities.append(ent)
        return ent

    def link(
        src: ProjectionEntity,
        dst: ProjectionEntity,
        relation: Relation,
        **kwargs: Any,
    ) -> None:
        projection.edges.append(
            ProjectionEdge(
                src_type=src.entity_type,
                src_key=src.canonical_key,
                dst_type=dst.entity_type,
                dst_key=dst.canonical_key,
                relation=relation,
                attributes=kwargs.get("attributes") or {},
                confidence=kwargs.get("confidence", 0.9),
                origin=kwargs.get("origin", "artifact"),
            )
        )

    def link_email(dst: ProjectionEntity, relation: Relation, **kwargs: Any) -> None:
        projection.edges.append(
            ProjectionEdge(
                src_type=EntityType.EMAIL,
                src_key=EMAIL_SRC_KEY,
                dst_type=dst.entity_type,
                dst_key=dst.canonical_key,
                relation=relation,
                attributes=kwargs.get("attributes") or {},
                confidence=kwargs.get("confidence", 0.9),
                origin=kwargs.get("origin", "artifact"),
            )
        )

    report = artifacts.get("analysis/report") or {}

    for key, content in artifacts.items():
        if not key.startswith("extracted_data/") or not isinstance(content, dict):
            continue
        snippet = str(content.get("summary") or key)[:500]
        for raw_ent in content.get("entities") or []:
            parsed = _parse_entity_entry(raw_ent)
            if parsed is None:
                continue
            etype, name = parsed
            ent = add(
                entity_type=etype,
                canonical_name=name,
                snippet=snippet,
                attributes={"source_file": content.get("source_file")},
            )
            link_email(ent, Relation.MENTIONS, snippet=snippet)

    if isinstance(report, dict):
        intention = str(report.get("intention") or "").strip()
        if intention:
            law_ent = add(
                entity_type=EntityType.DOCUMENT,
                canonical_name=intention[:120],
                canonical_key=law_area_canonical_key(intention[:80]),
                attributes={"analysis_intention": True},
                confidence=0.9,
            )
            link_email(law_ent, Relation.CONCERNS)

        action = str(report.get("action") or "").strip()
        action_payload = report.get("action_payload") or {}
        if action == "create_contract" and isinstance(action_payload, dict):
            type_hint = str(action_payload.get("contract_type_hint") or "contract").strip()
            provided = action_payload.get("provided_fields") or {}
            party_names: list[str] = []
            party_entities: list[ProjectionEntity] = []

            for field_name, value in provided.items():
                if not is_party_field_name(str(field_name)):
                    continue
                name = str(value).strip()
                if not name:
                    continue
                party_names.append(name)
                etype = infer_entity_type_from_name(name)
                party_ent = add(
                    entity_type=etype,
                    canonical_name=name,
                    snippet=f"{field_name}: {name}",
                    attributes={"field": field_name},
                )
                party_entities.append(party_ent)

            if party_names or type_hint:
                contract_ent = add(
                    entity_type=EntityType.CONTRACT,
                    canonical_name=type_hint,
                    canonical_key=contract_canonical_key(type_hint, party_names),
                    attributes={
                        "contract_type": type_hint,
                        "parties": party_names,
                        "status": "requested",
                    },
                )
                for party_ent in party_entities:
                    link(party_ent, contract_ent, Relation.PARTY_TO)
                link_email(contract_ent, Relation.REFERENCES)

    draft = artifacts.get("contracts/draft")
    if isinstance(draft, dict):
        contract_type = str(draft.get("contract_type") or "contract").strip()
        field_values = draft.get("field_values") or {}
        party_names: list[str] = []
        party_entities: list[ProjectionEntity] = []

        for field_name, value in field_values.items():
            if not is_party_field_name(str(field_name)):
                continue
            name = str(value).strip()
            if not name:
                continue
            party_names.append(name)
            etype = infer_entity_type_from_name(name)
            party_ent = add(
                entity_type=etype,
                canonical_name=name,
                attributes={"field": field_name},
            )
            party_entities.append(party_ent)

        contract_ent = add(
            entity_type=EntityType.CONTRACT,
            canonical_name=str(draft.get("display_name") or contract_type),
            canonical_key=contract_canonical_key(contract_type, party_names),
            attributes={
                "contract_type": contract_type,
                "parties": party_names,
                "field_values": field_values,
                "status": "draft",
            },
            confidence=0.95,
        )
        for party_ent in party_entities:
            link(party_ent, contract_ent, Relation.PARTY_TO)

        governing = field_values.get("governing_law")
        if governing:
            jur_ent = add(
                entity_type=EntityType.JURISDICTION,
                canonical_name=str(governing).strip(),
                canonical_key=f"jurisdiction:{normalize_name(str(governing))}",
            )
            link(contract_ent, jur_ent, Relation.GOVERNED_BY)

    return projection


def build_artifact_anchor_projection(
    artifacts: dict[str, Any],
    session_id: uuid.UUID,
) -> GraphProjection:
    """Link analysis artifacts to the email and to each source file they describe."""
    projection = GraphProjection()
    entity_by_key: dict[tuple[str, str], ProjectionEntity] = {}

    def add_doc(
        artifact_key: str,
        *,
        display_name: str | None = None,
        doc_kind: str | None = None,
        source_file: str | None = None,
        extra_attrs: dict[str, Any] | None = None,
    ) -> ProjectionEntity:
        key = artifact_canonical_key(session_id, artifact_key)
        bucket = (str(EntityType.DOCUMENT), key)
        if bucket in entity_by_key:
            return entity_by_key[bucket]
        attrs: dict[str, Any] = {
            "artifact_key": artifact_key,
            "session_id": str(session_id),
        }
        if doc_kind:
            attrs["doc_kind"] = doc_kind
        if source_file:
            attrs["source_file"] = source_file
        if extra_attrs:
            attrs.update(extra_attrs)
        ent = ProjectionEntity(
            entity_type=EntityType.DOCUMENT,
            canonical_key=key,
            canonical_name=display_name or artifact_key,
            attributes=attrs,
            snippet=display_name or artifact_key,
            confidence=1.0,
            origin="artifact",
        )
        entity_by_key[bucket] = ent
        projection.entities.append(ent)
        return ent

    def link_docs(
        src: ProjectionEntity,
        dst: ProjectionEntity,
        relation: Relation = Relation.REFERENCES,
        **attrs: Any,
    ) -> None:
        projection.edges.append(
            ProjectionEdge(
                src_type=src.entity_type,
                src_key=src.canonical_key,
                dst_type=dst.entity_type,
                dst_key=dst.canonical_key,
                relation=relation,
                attributes=dict(attrs),
                confidence=1.0,
                origin="artifact",
            )
        )

    def link_email_doc(dst: ProjectionEntity, **attrs: Any) -> None:
        projection.edges.append(
            ProjectionEdge(
                src_type=EntityType.EMAIL,
                src_key=EMAIL_SRC_KEY,
                dst_type=dst.entity_type,
                dst_key=dst.canonical_key,
                relation=Relation.REFERENCES,
                attributes={"role": "analysis_anchor", **attrs},
                confidence=1.0,
                origin="artifact",
            )
        )

    extracted_raw = artifacts.get("analysis/extracted")
    report_raw = artifacts.get("analysis/report")
    extracted_doc: ProjectionEntity | None = None
    report_doc: ProjectionEntity | None = None

    if isinstance(extracted_raw, dict):
        extracted_doc = add_doc(
            "analysis/extracted",
            display_name="Email extraction",
            doc_kind="analysis/extracted",
            extra_attrs={
                "subject": extracted_raw.get("subject"),
                "action_requested": extracted_raw.get("action_requested"),
            },
        )
        link_email_doc(extracted_doc, role="extraction")

    if isinstance(report_raw, dict):
        report_doc = add_doc(
            "analysis/report",
            display_name="Email analysis report",
            doc_kind="analysis/report",
            extra_attrs={
                "action": report_raw.get("action"),
                "intention": report_raw.get("intention"),
            },
        )
        link_email_doc(report_doc, role="report")
        if extracted_doc is not None:
            link_docs(
                report_doc,
                extracted_doc,
                attributes={"role": "derived_from", "lineage": "report_from_extraction"},
            )

    source_artifact_keys: set[str] = set()
    if isinstance(extracted_raw, dict):
        for att in extracted_raw.get("attachments") or []:
            if not isinstance(att, dict) or not att.get("processed"):
                continue
            for field in ("data_artifact_key", "text_artifact_key"):
                ref = att.get(field)
                if ref:
                    source_artifact_keys.add(str(ref))
            name = att.get("name")
            if name:
                source_artifact_keys.add(f"extracted_data/{name}")

    for artifact_key in artifacts:
        if artifact_key.startswith(("extracted_data/", "extracted_text/", "image_analysis/")):
            source_artifact_keys.add(artifact_key)

    for artifact_key in sorted(source_artifact_keys):
        content = artifacts.get(artifact_key)
        source_file: str | None = None
        if isinstance(content, dict):
            source_file = str(content.get("source_file") or "").strip() or None
        if not source_file and "/" in artifact_key:
            source_file = artifact_key.rsplit("/", 1)[-1]

        prefix = artifact_key.split("/", 1)[0] if "/" in artifact_key else "artifact"
        source_doc = add_doc(
            artifact_key,
            display_name=artifact_key,
            doc_kind=prefix,
            source_file=source_file,
        )

        if extracted_doc is not None:
            link_docs(
                extracted_doc,
                source_doc,
                attributes={"role": "extraction_source", "source_file": source_file},
            )
        if report_doc is not None:
            link_docs(
                report_doc,
                source_doc,
                attributes={"role": "analysis_source", "source_file": source_file},
            )

        if source_file:
            projection.edges.append(
                ProjectionEdge(
                    src_type=EntityType.ATTACHMENT,
                    src_key=attachment_filename_key(source_file),
                    dst_type=source_doc.entity_type,
                    dst_key=source_doc.canonical_key,
                    relation=Relation.REFERENCES,
                    attributes={"role": "analyzed_as", "artifact_key": artifact_key},
                    confidence=1.0,
                    origin="artifact",
                )
            )

    return projection


def merge_projections(base: GraphProjection, extra: GraphProjection) -> GraphProjection:
    seen = {(str(e.entity_type), e.canonical_key) for e in base.entities}
    for ent in extra.entities:
        k = (str(ent.entity_type), ent.canonical_key)
        if k not in seen:
            base.entities.append(ent)
            seen.add(k)
    base.edges.extend(extra.edges)
    return base


async def llm_enrich_projection(artifacts: dict[str, Any], base: GraphProjection) -> GraphProjection:
    """Optional Haiku structured extraction over artifact text fields."""
    settings = get_settings()
    if not settings.KG_LLM_EXTRACTION_ENABLED:
        return base

    payload = {
        "analysis/extracted": artifacts.get("analysis/extracted"),
        "analysis/report": artifacts.get("analysis/report"),
        "extracted_data": {
            k: v
            for k, v in artifacts.items()
            if k.startswith("extracted_data/") and isinstance(v, dict)
        },
    }
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > 12000:
        text = text[:12000] + "…"

    try:
        from legalbot.agents.models import init_stage_model

        model = init_stage_model("kg_extraction")
        structured = model.with_structured_output(LlmGraphExtraction)
        result: LlmGraphExtraction = await structured.ainvoke(
            [
                {
                    "role": "user",
                    "content": (
                        "Extract legal entities (Person, Organization, Contract, Case) "
                        "from this pipeline artifact bundle. Only include explicit mentions.\n\n"
                        f"{text}"
                    ),
                }
            ]
        )
    except Exception as exc:
        log.warning("graph.llm_extraction_failed", error=str(exc))
        return base

    llm_projection = GraphProjection()
    for raw in result.entities:
        add_ent = ProjectionEntity(
            entity_type=raw.entity_type,
            canonical_key=_entity_key(raw.entity_type, raw.name, raw.attributes),
            canonical_name=raw.name.strip(),
            attributes=raw.attributes,
            aliases=list(raw.aliases),
            confidence=raw.confidence,
            origin="llm",
        )
        llm_projection.entities.append(add_ent)
    return merge_projections(base, llm_projection)


def qa_acme_nda_artifact_bundle() -> dict[str, Any]:
    """Fake artifact bundle for QA / tests: NDA request involving Acme Corp and Jane Doe."""
    return {
        "analysis/extracted": {
            "subject": "Please draft NDA for Acme Corp and Jane Doe",
            "from_addr": "partner@firm.test",
            "body_summary": (
                "Partner asks the firm to prepare a mutual NDA between Acme Corp "
                "and Jane Doe (jane@acme.com) using the same terms as the executed "
                "agreement in the email thread and prior_nda.docx template."
            ),
            "action_requested": "review_document",
            "referenced_documents": [
                "executed_nda.docx (parent thread)",
                "prior_nda.docx (attached template)",
            ],
            "attachments": [
                {
                    "name": "prior_nda.docx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "data_artifact_key": "extracted_data/prior_nda.docx",
                    "text_artifact_key": "extracted_text/prior_nda.docx",
                    "processed": True,
                    "error": None,
                },
                {
                    "name": "acme_logo.png",
                    "mime_type": "image/png",
                    "data_artifact_key": "image_analysis/acme_logo.png",
                    "text_artifact_key": None,
                    "processed": True,
                    "error": None,
                },
            ],
        },
        "analysis/report": {
            "user_input": {
                "subject": "Please draft NDA for Acme Corp and Jane Doe",
                "from_addr": "partner@firm.test",
                "body_summary": (
                    "Partner asks the firm to prepare a mutual NDA between Acme Corp "
                    "and Jane Doe (jane@acme.com) using the same terms as the executed "
                    "agreement in the email thread and prior_nda.docx template."
                ),
                "action_requested": "review_document",
                "referenced_documents": [
                    "executed_nda.docx (parent thread)",
                    "prior_nda.docx (attached template)",
                ],
                "attachments": [
                    {
                        "name": "prior_nda.docx",
                        "mime_type": (
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        ),
                        "data_artifact_key": "extracted_data/prior_nda.docx",
                        "text_artifact_key": "extracted_text/prior_nda.docx",
                        "processed": True,
                        "error": None,
                    },
                    {
                        "name": "acme_logo.png",
                        "mime_type": "image/png",
                        "data_artifact_key": "image_analysis/acme_logo.png",
                        "text_artifact_key": None,
                        "processed": True,
                        "error": None,
                    },
                ],
            },
            "intention": "Draft mutual NDA for Acme Corp and Jane Doe",
            "research": [],
            "relevant_resources": [
                {
                    "type": "artifact",
                    "ref": "extracted_data/prior_nda.docx",
                    "relevance": "NDA template with parties and terms",
                }
            ],
            "action": "create_contract",
            "action_payload": {
                "contract_type_hint": "NDA",
                "provided_fields": {
                    "disclosing_party": "Acme Corp",
                    "receiving_party": "Jane Doe",
                    "effective_date": "January 1, 2026",
                    "purpose": (
                        "Evaluating a potential business partnership and joint "
                        "product discussions between Acme Corp and Jane Doe"
                    ),
                    "governing_law": "State of Delaware",
                    "confidentiality_term": "24 months from the Effective Date",
                },
                "missing_fields": [],
                "evidence_refs": ["extracted_data/prior_nda.docx"],
            },
            "confidence": "high",
            "blockers": [],
            "missing_information": [],
        },
        "extracted_data/prior_nda.docx": {
            "source_file": "prior_nda.docx",
            "summary": (
                "Mutual NDA template between Acme Corp and Jane Doe with 24-month "
                "confidentiality term and Delaware governing law."
            ),
            "entities": [
                {"name": "Acme Corp", "type": "Organization"},
                {"name": "Jane Doe", "type": "Person", "email": "jane@acme.com"},
            ],
            "key_findings": [
                "Effective date: January 1, 2026",
                "Purpose: business partnership and joint product discussions",
                "Confidentiality term: 24 months from the Effective Date",
                "Governing law: State of Delaware",
                "Contract type: mutual NDA (not unilateral)",
            ],
        },
        "image_analysis/acme_logo.png": {
            "source_file": "acme_logo.png",
            "summary": "Acme Corp corporate logo (decorative; no contract terms).",
            "entities": [{"name": "Acme Corp", "type": "Organization"}],
            "key_findings": ["Image is a logo only — not a legal document"],
        },
    }
