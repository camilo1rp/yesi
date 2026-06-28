"""Deterministic registry of supported contract types, fields, and templates.

No LLM and no DB: this is the authoritative, code-level source of truth that the
contract subagents consult to decide whether a request is supported, which fields
are required, and how to render the final document. Templates and reference
examples are plain Markdown files colocated in this package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """A single required field the requester must supply (or the agent must find)."""

    name: str
    description: str
    example: str


@dataclass(frozen=True, slots=True)
class ContractType:
    """A supported contract type: its fields, template file, and reference examples."""

    type_id: str
    display_name: str
    aliases: tuple[str, ...]
    required_fields: tuple[FieldSpec, ...]
    template_file: str
    example_files: tuple[str, ...] = field(default_factory=tuple)

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.required_fields]


class UnknownContractType(KeyError):
    """Raised when a contract type id is not in the registry."""


class TemplateRenderError(ValueError):
    """Raised when a template cannot be filled (unfilled or unknown placeholder)."""


CONTRACT_TYPES: dict[str, ContractType] = {
    "nda": ContractType(
        type_id="nda",
        display_name="Non-Disclosure Agreement",
        aliases=(
            "nda",
            "non-disclosure",
            "non disclosure",
            "nondisclosure",
            "confidentiality agreement",
            "acuerdo de confidencialidad",
        ),
        required_fields=(
            FieldSpec(
                "disclosing_party", "Legal name of the party disclosing information", "Acme Corp."
            ),
            FieldSpec(
                "receiving_party", "Legal name of the party receiving information", "Beta LLC"
            ),
            FieldSpec("effective_date", "Date the agreement takes effect", "January 1, 2026"),
            FieldSpec(
                "purpose",
                "Purpose for which confidential information is shared",
                "evaluating a potential partnership",
            ),
            FieldSpec("term_months", "Duration of confidentiality obligations in months", "24"),
            FieldSpec("governing_law", "Governing law jurisdiction", "State of Delaware"),
        ),
        template_file="nda.md",
        example_files=("nda_example.md",),
    ),
    "service_agreement": ContractType(
        type_id="service_agreement",
        display_name="Services Agreement",
        aliases=(
            "service agreement",
            "services agreement",
            "master services agreement",
            "msa",
            "consulting agreement",
            "contrato de servicios",
        ),
        required_fields=(
            FieldSpec("client_name", "Legal name of the client", "Acme Corp."),
            FieldSpec("provider_name", "Legal name of the service provider", "Beta LLC"),
            FieldSpec("effective_date", "Date the agreement takes effect", "January 1, 2026"),
            FieldSpec(
                "services_description",
                "Description of the services to be provided",
                "monthly bookkeeping and tax filing",
            ),
            FieldSpec(
                "fee_amount", "Total or recurring fee, including currency", "USD 5,000 per month"
            ),
            FieldSpec(
                "payment_terms", "When and how the provider is paid", "net 30 from invoice date"
            ),
            FieldSpec("term_months", "Duration of the agreement in months", "12"),
            FieldSpec("governing_law", "Governing law jurisdiction", "State of Delaware"),
        ),
        template_file="service_agreement.md",
        example_files=("service_agreement_example.md",),
    ),
}


def list_contract_types() -> list[dict]:
    """Return a serializable summary of every supported contract type."""
    return [
        {
            "type_id": ct.type_id,
            "display_name": ct.display_name,
            "aliases": list(ct.aliases),
            "required_fields": [
                {"name": f.name, "description": f.description, "example": f.example}
                for f in ct.required_fields
            ],
        }
        for ct in CONTRACT_TYPES.values()
    ]


def get_contract_type(type_id: str) -> ContractType | None:
    """Return the contract type for `type_id`, or None if unsupported."""
    return CONTRACT_TYPES.get((type_id or "").strip().lower())


def classify_contract_type(hint: str) -> dict:
    """Map a free-text hint to a supported type id, deterministically.

    Returns ``{"type_id": <id|None>, "candidates": [<id>, ...]}``. More than one
    candidate signals an ambiguous request that the caller must disambiguate.
    """
    text = (hint or "").strip().lower()
    if not text:
        return {"type_id": None, "candidates": []}

    candidates: list[str] = []
    for ct in CONTRACT_TYPES.values():
        if ct.type_id in text:
            candidates.append(ct.type_id)
            continue
        if any(alias in text for alias in ct.aliases):
            candidates.append(ct.type_id)

    candidates = list(dict.fromkeys(candidates))
    return {
        "type_id": candidates[0] if len(candidates) == 1 else None,
        "candidates": candidates,
    }


@cache
def load_template(type_id: str) -> str:
    """Read the raw Markdown template text for a contract type."""
    ct = get_contract_type(type_id)
    if ct is None:
        raise UnknownContractType(type_id)
    return (_TEMPLATES_DIR / ct.template_file).read_text(encoding="utf-8")


def template_placeholders(type_id: str) -> list[str]:
    """Return the ordered, de-duplicated placeholder names used by a template."""
    found = _PLACEHOLDER_RE.findall(load_template(type_id))
    return list(dict.fromkeys(found))


def missing_fields(type_id: str, provided: dict) -> list[str]:
    """Return required field names that are absent or blank in `provided`."""
    ct = get_contract_type(type_id)
    if ct is None:
        raise UnknownContractType(type_id)
    provided = provided or {}
    return [name for name in ct.field_names if not str(provided.get(name, "")).strip()]


def render_template(type_id: str, values: dict) -> str:
    """Strictly substitute every `{{placeholder}}` with its value.

    Raises ``TemplateRenderError`` if any placeholder has no non-blank value,
    which also catches placeholders that are not backed by a declared field.
    """
    template = load_template(type_id)
    values = values or {}

    unfilled = [
        name for name in template_placeholders(type_id) if not str(values.get(name, "")).strip()
    ]
    if unfilled:
        raise TemplateRenderError(f"cannot render {type_id!r}: unfilled placeholders {unfilled}")

    def _replace(match: re.Match[str]) -> str:
        return str(values[match.group(1)]).strip()

    return _PLACEHOLDER_RE.sub(_replace, template)
