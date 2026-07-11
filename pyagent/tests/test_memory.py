"""Tests for the conversation memory model."""

from __future__ import annotations

from agent.memory import Conversation


def test_add_messages():
    conv = Conversation("system prompt")
    conv.add_user("hi")
    conv.add_assistant("hello")
    assert conv.messages[0]["role"] == "system"
    assert conv.messages[1]["content"] == "hi"
    assert conv.messages[2]["content"] == "hello"


def test_reset():
    conv = Conversation("sys")
    conv.add_user("x")
    conv.reset("new sys")
    assert len(conv.messages) == 1
    assert conv.messages[0]["content"] == "new sys"


def test_tool_result_message():
    conv = Conversation("sys")
    conv.add_tool_result("id1", "run_shell", "output")
    last = conv.messages[-1]
    assert last["role"] == "tool"
    assert last["tool_call_id"] == "id1"


def test_token_estimate():
    conv = Conversation("hello world")
    assert conv.token_estimate() >= 0
