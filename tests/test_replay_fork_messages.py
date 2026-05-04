from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from legalbot.services.replay import _replay_fork_messages


def test_replay_fork_messages_strips_orphan_assistant_before_operator_note() -> None:
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

    out = _replay_fork_messages([orphan, task], "operator: focus on deadlines")

    assert isinstance(out[0], RemoveMessage)
    assert out[0].id == "__remove_all__"
    assert out[1] is task
    assert out[2].type == "human"
    assert out[2].content == "operator: focus on deadlines"


def test_replay_fork_messages_keeps_completed_tool_round_trip() -> None:
    human = HumanMessage(content="task")
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

    out = _replay_fork_messages([human, ai, tool], "operator note")

    assert isinstance(out[0], RemoveMessage)
    assert [m.type for m in out[1:]] == ["human", "ai", "tool", "human"]
    assert out[-1].content == "operator note"
