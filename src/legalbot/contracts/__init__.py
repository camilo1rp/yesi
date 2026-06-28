"""Deterministic contract registry, templates, and example retrieval."""

from legalbot.contracts.examples import search_contract_examples
from legalbot.contracts.registry import (
    CONTRACT_TYPES,
    ContractType,
    FieldSpec,
    TemplateRenderError,
    UnknownContractType,
    classify_contract_type,
    get_contract_type,
    list_contract_types,
    missing_fields,
    render_template,
    template_placeholders,
)

__all__ = [
    "CONTRACT_TYPES",
    "ContractType",
    "FieldSpec",
    "TemplateRenderError",
    "UnknownContractType",
    "classify_contract_type",
    "get_contract_type",
    "list_contract_types",
    "missing_fields",
    "render_template",
    "search_contract_examples",
    "template_placeholders",
]
