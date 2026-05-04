"""Python-side enums mapped to Postgres text columns."""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    email = "email"
    slack = "slack"
    webhook = "webhook"
    upload = "upload"


class JobState(StrEnum):
    intake = "intake"
    ready = "ready"
    dispatched = "dispatched"
    processing = "processing"
    awaiting_human = "awaiting_human"
    completed = "completed"
    failed = "failed"
    archived = "archived"


class SessionKind(StrEnum):
    primary = "primary"
    replay = "replay"


class SessionStatus(StrEnum):
    idle = "idle"
    running = "running"
    awaiting_human = "awaiting_human"
    archived = "archived"
    failed = "failed"


class InterruptKind(StrEnum):
    tool_approval = "tool_approval"
    information_request = "information_request"
    review_draft = "review_draft"


class InterruptStatus(StrEnum):
    pending = "pending"
    resolved = "resolved"
    expired = "expired"
    cancelled = "cancelled"


class RunKind(StrEnum):
    pipeline = "pipeline"
    user_followup = "user_followup"
    scheduled = "scheduled"


class RunStatus(StrEnum):
    running = "running"
    completed = "completed"
    awaiting_human = "awaiting_human"
    failed = "failed"
    cancelled = "cancelled"


class StepName(StrEnum):
    extract = "extract"
    analyze = "analyze"
    act = "act"
    reflection = "reflection"
    freeform = "freeform"


class ArtifactProducer(StrEnum):
    extract = "extract"
    analyze = "analyze"
    act = "act"
    reflection = "reflection"
    user = "user"
    system = "system"


class ArtifactKind(StrEnum):
    extracted_text = "extracted_text"
    extracted_data = "extracted_data"
    analysis = "analysis"
    draft_proposal = "draft_proposal"
    schedule_proposal = "schedule_proposal"
    evidence_bundle = "evidence_bundle"
    custom = "custom"


class ArtifactStorage(StrEnum):
    inline = "inline"
    blob = "blob"


class DraftStatus(StrEnum):
    draft = "draft"
    requires_human = "requires_human"
    sent = "sent"


class ScheduledJobStatus(StrEnum):
    active = "active"
    paused = "paused"
    cancelled = "cancelled"
    expired = "expired"


class ScheduledJobScheduleKind(StrEnum):
    one_shot = "one_shot"
    cron = "cron"
    interval = "interval"
