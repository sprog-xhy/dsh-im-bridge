"""Tests for wire-frame parsing."""

import json

import pytest

from dsh_im_bridge import parser
from dsh_im_bridge.events import QuestionRequest


def _envelope(rpc_id, method, payload):
    return {"type": "server-request", "rpcId": rpc_id, "method": method, "payload": payload}


def test_session_subscribed_frame():
    frame = _envelope(
        "r-1",
        "session/subscribed",
        {"type": "session/subscribed", "sessionId": "s-1", "lastSeq": 42},
    )
    parsed = parser.parse_mux_frame(frame)
    assert parsed["kind"] == "session/subscribed"
    assert parsed["rpc_id"] == "r-1"
    assert parsed["event"] is None


def test_session_event_frame():
    event = {
        "type": "assistant/message",
        "seq": 7,
        "time": 1787849519604.0,
        "data": {"content": [{"type": "text", "text": "hello world"}]},
    }
    frame = _envelope(
        "r-2",
        "session/event",
        {"type": "session/event", "sessionId": "s-9", "event": event},
    )
    parsed = parser.parse_mux_frame(frame)
    assert parsed["kind"] == "session/event"
    ev = parsed["event"]
    assert ev.session_id == "s-9"
    assert ev.type == "assistant/message"
    assert ev.seq == 7
    assert ev.text == "hello world"


def test_question_requested_frame():
    payload = {
        "type": "question/requested",
        "sessionId": "s-3",
        "questions": [
            {
                "id": "q1",
                "question": "继续吗?",
                "header": "确认",
                "options": [{"label": "继续", "description": "go on"}],
            }
        ],
    }
    frame = _envelope("r-77", "question/requested", payload)
    parsed = parser.parse_mux_frame(frame)
    q = parser.question_from_frame(parsed)
    assert isinstance(q, QuestionRequest)
    assert q.rpc_id == "r-77"
    assert q.session_id == "s-3"
    assert q.questions[0].id == "q1"
    assert q.questions[0].question == "继续吗?"
    assert q.questions[0].options[0]["label"] == "继续"


def test_approval_requested_frame():
    payload = {
        "type": "approval/requested",
        "sessionId": "s-4",
        "approvalId": "a-1",
        "toolName": "write",
        "reason": "wide write",
    }
    parsed = parser.parse_mux_frame(_envelope("r-9", "approval/requested", payload))
    a = parser.approval_from_frame(parsed)
    assert a is not None
    assert a.approval_id == "a-1"
    assert a.tool_name == "write"


def test_parse_json_string_frame():
    frame = _envelope(
        "r-1",
        "session/subscribed",
        {"type": "session/subscribed", "sessionId": "s", "lastSeq": 0},
    )
    parsed = parser.parse_mux_frame(json.dumps(frame))
    assert parsed["kind"] == "session/subscribed"


def test_bad_frame_raises():
    with pytest.raises(parser.FrameError):
        parser.parse_mux_frame("not json")
    with pytest.raises(parser.FrameError):
        parser.parse_mux_frame({"type": "weird", "nope": 1})
