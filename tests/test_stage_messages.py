from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from legalbot.agents.stage_messages import (
    prepare_stage_messages,
    sanitize_messages_for_openai_chat,
)


def test_prepare_stage_messages_uses_fallback_when_empty() -> None:
    messages = prepare_stage_messages(
        [],
        system_prompt="stage prompt",
        fallback_human="do the stage",
    )

    assert [message.type for message in messages] == ["system", "human"]
    assert messages[0].content == "stage prompt"
    assert messages[1].content == "do the stage"


def test_prepare_stage_messages_prepends_prompt_to_subagent_task() -> None:
    task_message = HumanMessage(content="task description from orchestrator")

    messages = prepare_stage_messages(
        [task_message],
        system_prompt="stage prompt",
        fallback_human="do the stage",
    )

    assert [message.type for message in messages] == ["system", "human"]
    assert messages[0].content == "stage prompt"
    assert messages[1] is task_message


def test_prepare_stage_messages_does_not_duplicate_existing_stage_prompt() -> None:
    system_message = SystemMessage(content="stage prompt")
    task_message = HumanMessage(content="task description from orchestrator")

    messages = prepare_stage_messages(
        [system_message, task_message],
        system_prompt="stage prompt",
        fallback_human="do the stage",
    )

    assert messages == [system_message, task_message]


def test_prepare_stage_messages_strips_orphan_prefix_before_system() -> None:
    orphan = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "fetch_email",
                "args": {},
                "id": "call_orphan",
                "type": "tool_call",
            }
        ],
    )
    task = HumanMessage(content="run extract stage")

    messages = prepare_stage_messages(
        [orphan, task],
        system_prompt="stage prompt",
        fallback_human="do the stage",
    )

    assert [m.type for m in messages] == ["system", "human"]
    assert messages[0].content == "stage prompt"
    assert messages[1] is task


def test_sanitize_messages_strips_orphan_assistant_tool_calls_prefix() -> None:
    orphan = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {},
                "id": "call_orphan",
                "type": "tool_call",
            }
        ],
    )
    task = HumanMessage(content="run analyze stage")

    out = sanitize_messages_for_openai_chat([orphan, task])
    assert out == [task]


def test_sanitize_messages_keeps_valid_tool_round_trip() -> None:
    human = HumanMessage(content="hi")
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "read_artifact",
                "args": {"key": "k"},
                "id": "call_ok",
                "type": "tool_call",
            }
        ],
    )
    tool = ToolMessage(content="{}", tool_call_id="call_ok")

    out = sanitize_messages_for_openai_chat([human, ai, tool])
    assert out == [human, ai, tool]
