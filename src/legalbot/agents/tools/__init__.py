"""Native domain tools exposed to the agent + subagents."""

from legalbot.agents.tools.artifact_tools import (
    list_artifacts,
    read_artifact,
    update_artifact,
    write_artifact,
)
from legalbot.agents.tools.domain import (
    analyze_image,
    fetch_email,
    run_attachment_extraction,
    schedule_followup,
    send_draft,
    write_draft,
)

__all__ = [
    "analyze_image",
    "fetch_email",
    "list_artifacts",
    "read_artifact",
    "run_attachment_extraction",
    "schedule_followup",
    "send_draft",
    "update_artifact",
    "write_artifact",
    "write_draft",
]
