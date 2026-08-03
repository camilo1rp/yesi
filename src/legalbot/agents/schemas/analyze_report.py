"""Pydantic models for the analyze-stage investigation report."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class AnalyzeAction(str, Enum):
    draft_response = "draft_response"
    create_contract = "create_contract"
    other = "other"


class ResearchStep(BaseModel):
    tool: str
    target: str
    finding: str


class ResourceRef(BaseModel):
    type: Literal["artifact", "kg_hit", "ingestion_item", "thread"]
    ref: str
    relevance: str


class MissingInformation(BaseModel):
    field: str
    question: str
    why_needed: str


class DraftResponsePayload(BaseModel):
    response_outline: str
    tone: str | None = None


class CreateContractPayload(BaseModel):
    contract_type_hint: str
    provided_fields: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class AnalyzeDecision(BaseModel):
    intention: str
    confidence: Literal["high", "medium", "low"]
    information_sufficient: bool
    action: AnalyzeAction | None = None
    missing_information: list[MissingInformation] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    draft_response: DraftResponsePayload | None = None
    create_contract: CreateContractPayload | None = None
    other_reason: str | None = None


class AnalyzeReport(BaseModel):
    user_input: dict
    intention: str
    research: list[ResearchStep]
    relevant_resources: list[ResourceRef]
    action: AnalyzeAction | None
    action_payload: dict
    confidence: str
    blockers: list[str]
    missing_information: list[MissingInformation]
