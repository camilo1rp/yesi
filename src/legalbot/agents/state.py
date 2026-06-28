"""Shared agent state TypedDict used by the main graph and all subagents.

Plan + filesystem + memory live in state (not messages) so they survive
summarization and subagent calls (§4.3 of the blueprint).

State schema hierarchy for independent graphs with input/output projection:
- InputState: What graphs receive
- OutputState variants: What each graph exposes (API-visible)
- OverallState: Full internal state (saved to checkpointer)
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import add_messages

from legalbot.artifacts.models import ArtifactRef, artifact_index_reducer
from legalbot.providers.base import Draft


# Placeholder types for business outputs (to be refined based on artifact schema)
class ExtractedInfo(TypedDict, total=False):
    """Structured facts extracted from email and attachments."""

    key_parties: list[str]
    deadlines: list[dict[str, Any]]
    obligations: list[str]
    monetary_amounts: list[dict[str, Any]]
    attachments_summary: list[dict[str, Any]]


class LegalAnalysis(TypedDict, total=False):
    """Legal analysis of the email."""

    intent_classification: str
    urgency: str
    required_action: str | None
    risk_level: str
    applicable_law_areas: list[str]


class ActionTaken(TypedDict, total=False):
    """Action taken by the agent."""

    action_type: str  # "draft_sent", "followup_scheduled", "no_action", etc.
    timestamp: str
    details: dict[str, Any]


class Reflection(TypedDict, total=False):
    """Post-session reflection for long-term memory."""

    summary: str
    lessons_learned: list[str]
    follow_up_needed: bool


# Input to all graphs
class InputState(TypedDict, total=False):
    email_metadata: dict[str, Any]
    job_id: str
    session_id: str
    run_id: str
    user_id: str
    graph_thread_id: str
    email_id: str
    # Set by run_pipeline before the first graph invocation so every subagent
    # can branch on attachments without re-fetching the email.
    has_attachments: bool


# Structured handoff from each stage to the orchestrator. Artifacts remain the
# durable source of truth; this is a compact routing/projection envelope.
class StageResult(TypedDict, total=False):
    stage: str
    status: str
    summary: str
    primary_artifact_key: str | None
    artifact_keys: list[str]
    needs_human: bool
    next_stage: str | None


class StageOutputState(TypedDict, total=False):
    stage_result: StageResult
    messages: Annotated[list[Any], add_messages]


# Output from each stage (API-visible)
class ExtractOutputState(StageOutputState, total=False):
    pass


class AnalyzeOutputState(StageOutputState, total=False):
    pass


class ActOutputState(StageOutputState, total=False):
    pass


class ContractOutputState(StageOutputState, total=False):
    pass


class ContractValidationOutputState(StageOutputState, total=False):
    pass


class ReflectOutputState(StageOutputState, total=False):
    pass


# Overall state (union of all channels, saved to checkpointer)
class LegalEmailState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    plan: Annotated[list[dict[str, Any]], operator.add]
    files: Annotated[dict[str, str], lambda a, b: {**a, **b}]
    artifact_index: Annotated[dict[str, ArtifactRef], artifact_index_reducer]
    job_id: str
    session_id: str
    run_id: str
    user_id: str
    graph_thread_id: str
    email_id: str
    # True when the ingestion_item has ≥1 IngestionAttachment row. Written once
    # by run_pipeline so every subagent can branch without a DB round-trip.
    has_attachments: bool
    ask_human_count: int
    stage_result: StageResult
    # Internal-only fields (not exposed via output_schema)
    raw_llm_reasoning: list[str]
    intermediate_tool_outputs: dict[str, Any]
    scratchpad: dict[str, Any]
    # Business outputs (exposed via respective output schemas)
    extracted_info: ExtractedInfo | None
    analysis: LegalAnalysis | None
    action: ActionTaken | None
    draft: Draft | None
    reflection: Reflection | None


# Backward compatibility alias
AgentState = LegalEmailState
