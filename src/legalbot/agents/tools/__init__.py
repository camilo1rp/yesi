"""Native domain tools exposed to the agent + subagents."""

from legalbot.agents.tools.artifact_tools import (
    list_artifacts,
    read_artifact,
    update_artifact,
    write_artifact,
)
from legalbot.agents.tools.contract_tools import (
    classify_contract,
    fill_contract_template,
    get_contract_requirements,
    list_contract_types,
    search_contract_examples,
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
    "classify_contract",
    "fetch_email",
    "fill_contract_template",
    "get_contract_requirements",
    "list_artifacts",
    "list_contract_types",
    "read_artifact",
    "run_attachment_extraction",
    "schedule_followup",
    "search_contract_examples",
    "send_draft",
    "update_artifact",
    "write_artifact",
    "write_draft",
]
