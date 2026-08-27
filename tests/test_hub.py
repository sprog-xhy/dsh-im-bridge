"""Tests for BridgeHub routing with a fake dsh client and a fake channel."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from dsh_im_bridge.dsh_client import DshError
from dsh_im_bridge.hub import BridgeHub, find_missed_questions, parse_answer_arg
from dsh_im_bridge.parser import parse_mux_frame
from dsh_im_bridge.events import InboundMessage
from dsh_im_bridge.channels.base import Channel


class FakeDsh:
    def __init__(self):
        self.prompts = []
        self.created = []
        self.answers = []
        self.approvals = []
        self.describes = 0
        self.fail_prompt = False

    def prompt(self, session_id, text, mode="queue"):
        if self.fail_prompt:
            raise DshError("session.prompt", "boom")
        self.prompts.append((session_id, text, mode))

    def create_session(self, **payload):
        sid = payload.get("sessionId") or f"s-{len(self.created) + 1}"
        self.created.append(payload)
        return {"sessionId": sid}

    def answer_question(self, rpc_id, session_id, answers):
        self.answers.append((rpc_id, session_id, answers))
        return True

    def cancel_question(self, rpc_id, session_id):
        return True

    def resolve_approval(self, rpc_id, session_id, approval_id, outcome):
        self.approvals.append((rpc_id, session_id, approval_id, outcome))
        return True

    def describe(self):
        self.describes += 1
        return {"version": "0.0.1", "cwd": "/tmp", "provider": "wps", "model": "m"}

    def list_sessions(self):
        return [{"sessionId": "s-1", "running": False}]

    def history(self, session_id, max_messages=50, before_seq=None):
        if session_id == "s-999":
            raise DshError("session.history", "no such session")
        return {"events": [], "hasMore": False}

    def cancel(self, session_id):
        self.cancelled = getattr(self, "cancelled", []) + [session_id]


class RecordingChannel(Channel):
    name = "rec"

    def __init__(self, config=None):
        super().__init__(config)
        self.sent = []
        self.started = False

    async def start(self):
        self.started = True

    async def stop(self):
        pass

    async def send(self, conversation_id, text, kind="notify"):
        self.sent.append((conversation_id, text, kind))


@pytest.fixture()
def hub():
    dsh = FakeDsh()
    h = BridgeHub(dsh)
    return h, dsh


def _frame_payload(payload):
    """Build a raw server-request envelope for the hub's on_frame."""
    return parse_mux_frame(
        {
            "type": "server-request",
            "rpcId": payload.get("rpcId", "r-1"),
            "method": payload.get("type", ""),
            "payload": payload,
        }
    )


async def _inject(hub, text, channel="rec", conv="c1"):
    await hub._handle_inbound(InboundMessage(channel=channel, conversation_id=conv, text=text))


@pytest.mark.asyncio
async def test_auto_bind_and_prompt(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    await h._handle_inbound(InboundMessage(channel="rec", conversation_id="c1", text="do the thing"))
    assert len(dsh.created) == 1
    assert dsh.prompts == [("s-1", "do the thing", "queue")]
    assert h.bindings["rec:c1"].session_id == "s-1"
    assert any("已绑定" in t for _, t, k in chan.sent)


@pytest.mark.asyncio
async def test_existing_binding_prompts(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))
    await h._handle_inbound(InboundMessage(channel="rec", conversation_id="c1", text="hello"))
    assert dsh.prompts == [("s-9", "hello", "queue")]
    assert not dsh.created


@pytest.mark.asyncio
async def test_prompt_failure_notifies(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    dsh.fail_prompt = True
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))
    await h._handle_inbound(InboundMessage(channel="rec", conversation_id="c1", text="hello"))
    assert any("失败" in t for _, t, k in chan.sent)


@pytest.mark.asyncio
async def test_event_forwarding_only_bound(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))
    payload = {
        "type": "session/event",
        "sessionId": "s-9",
        "event": {
            "type": "assistant/message",
            "seq": 1,
            "time": 100.0,
            "data": {"content": [{"type": "text", "text": "final answer"}]},
        },
    }
    await h._on_frame(_frame_payload(payload))
    assert any("final answer" in t for _, t, k in chan.sent)

    # unbound session events are dropped
    chan.sent.clear()
    payload["sessionId"] = "s-other"
    await h._on_frame(_frame_payload(payload))
    assert chan.sent == []


@pytest.mark.asyncio
async def test_question_forward_and_answer(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))
    payload = {
        "type": "question/requested",
        "rpcId": "rq-1",
        "sessionId": "s-9",
        "questions": [{"id": "q1", "question": "继续?", "options": [{"label": "是"}]}],
    }
    await h._on_frame(_frame_payload(payload))
    assert any("继续?" in t for _, t, k in chan.sent)
    assert "rq-1" in h.pending
    # index 1 resolves to the real question id "q1", and "是" matches an option
    await _inject(h, "/answer 1:是")
    assert dsh.answers == [("rq-1", "s-9", [{"id": "q1", "selected": ["是"], "custom": ""}])]
    assert "rq-1" not in h.pending


@pytest.mark.asyncio
async def test_question_for_unbound_session_ignored(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    payload = {
        "type": "question/requested",
        "rpcId": "rq-99",
        "sessionId": "s-other",
        "questions": [{"id": "q1", "question": "?"}],
    }
    await h._on_frame(_frame_payload(payload))
    # must not accumulate pending for unbound sessions
    assert "rq-99" not in h.pending
    assert chan.sent == []


@pytest.mark.asyncio
async def test_approval_forward_and_allow(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))
    payload = {
        "type": "approval/requested",
        "rpcId": "ra-1",
        "sessionId": "s-9",
        "approvalId": "a-9",
        "toolName": "bash",
    }
    await h._on_frame(_frame_payload(payload))
    assert any("bash" in t for _, t, k in chan.sent)

    await _inject(h, "/allow")
    assert dsh.approvals == [("ra-1", "s-9", "a-9", "allowed-once")]
    assert "ra-1" not in h.pending


@pytest.mark.asyncio
async def test_status_command(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    await _inject(h, "/status")
    assert any("version" in t and "0.0.1" in t for _, t, k in chan.sent)


@pytest.mark.asyncio
async def test_sessions_command(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    await _inject(h, "/sessions")
    assert any("s-1" in t for _, t, k in chan.sent)


@pytest.mark.asyncio
async def test_attach_command(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    await _inject(h, "/attach s-9")
    assert h.bindings["rec:c1"].session_id == "s-9"
    # attaching to a nonexistent session fails
    await _inject(h, "/attach s-999")
    assert h.bindings["rec:c1"].session_id == "s-9"


@pytest.mark.asyncio
async def test_state_persistence(hub):
    h, dsh = hub
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state.json"
        h2 = BridgeHub(dsh, state_file=state)
        from dsh_im_bridge.hub import SessionBinding

        h2._add_binding(SessionBinding("rec:c1", "s-7"))
        h2._save_state()
        assert state.exists()

        h3 = BridgeHub(FakeDsh(), state_file=state)
        h3._load_state()
        assert h3.bindings["rec:c1"].session_id == "s-7"


def test_state_load_tolerates_utf8_bom():
    """PowerShell's Set-Content writes a UTF-8 BOM; loading must not crash."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state.json"
        state.write_bytes(
            b"\xef\xbb\xbf" + b'{"bindings":[{"conversation":"rec:c1","sessionId":"s-1","title":""}],"notifiedSeq":{}}'
        )
        h = BridgeHub(FakeDsh(), state_file=state)
        h._load_state()
        assert h.bindings["rec:c1"].session_id == "s-1"


@pytest.mark.asyncio
async def test_startup_notification_only_bound():
    """notifyOnStart sends a 'bridge started' note only to bound conversations."""
    h = BridgeHub(FakeDsh(), catch_up=False)
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import DEFAULT_FORWARD_EVENTS, SessionBinding

    assert "step/end" not in DEFAULT_FORWARD_EVENTS  # would spam on multi-step tasks
    h._add_binding(SessionBinding("rec:c1", "s-9"))
    await h.start()
    await h.stop()
    assert any("已启动" in t for _, t, k in chan.sent)


@pytest.mark.asyncio
async def test_step_end_not_forwarded_by_default(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))
    payload = {
        "type": "session/event",
        "sessionId": "s-9",
        "event": {"type": "step/end", "seq": 5, "time": 100.0, "data": {}},
    }
    await h._on_frame(_frame_payload(payload))
    assert chan.sent == []


def test_parse_answer_arg():
    assert parse_answer_arg("1:是") == [{"id": "1", "selected": [], "custom": "是"}]
    assert parse_answer_arg("1:是,2:随便") == [
        {"id": "1", "selected": [], "custom": "是"},
        {"id": "2", "selected": [], "custom": "随便"},
    ]
    # without question metadata, values are sent as custom answers (safe)
    assert parse_answer_arg("q1:继续") == [{"id": "q1", "selected": [], "custom": "继续"}]
    assert parse_answer_arg("") is None
    assert parse_answer_arg(":空") is None


def test_parse_answer_arg_resolves_real_question_ids():
    from dsh_im_bridge.events import QuestionItem

    questions = [
        QuestionItem(id="uuid-abc", question="方案?", options=({"label": "方案A"},)),
        QuestionItem(id="uuid-def", question="补充?", multi_select=True),
    ]
    # index 1 -> uuid-abc, "方案A" is an option label -> selected
    assert parse_answer_arg("1:方案A", questions=questions) == [
        {"id": "uuid-abc", "selected": ["方案A"], "custom": ""}
    ]
    # index 2 -> uuid-def, custom free text
    assert parse_answer_arg("2:随便补充", questions=questions) == [
        {"id": "uuid-def", "selected": [], "custom": "随便补充"}
    ]
    # exact id works too
    assert parse_answer_arg("uuid-def:再来一点", questions=questions) == [
        {"id": "uuid-def", "selected": [], "custom": "再来一点"}
    ]
    # out-of-range index is rejected
    assert parse_answer_arg("9:什么", questions=questions) is None


def test_find_missed_questions():
    """ask_user_question tool call without a matching tool result is detected."""
    def tc(seq, call_id, answered=False):
        return {
            "event": {
                "type": "tool/call",
                "seq": seq,
                "data": {
                    "callId": call_id,
                    "name": "ask_user_question",
                    "arguments": json.dumps(
                        {"questions": [{"id": "q-x", "question": "继续吗?", "options": [{"label": "是"}]}]},
                        ensure_ascii=False,
                    ),
                },
            }
        }

    def tr(seq, call_id):
        return {
            "event": {
                "type": "tool/result",
                "seq": seq,
                "data": {"callId": call_id, "message": {"content": [{"type": "tool-result", "toolCallId": call_id}]}},
            }
        }

    events = [tc(1, "c-answered"), tr(2, "c-answered"), tc(3, "c-missed"), tc(4, "c-missed2")]
    missed = find_missed_questions(events)
    assert {m["call_id"] for m in missed} == {"c-missed", "c-missed2"}
    assert "继续吗?" in missed[0]["text"]
    assert missed[0]["questions"][0].id == "q-x"


def test_find_missed_questions_ignores_non_question_tools():
    events = [
        {
            "event": {
                "type": "tool/call",
                "seq": 1,
                "data": {"callId": "c-1", "name": "bash", "arguments": "{}"},
            }
        }
    ]
    assert find_missed_questions(events) == []


@pytest.mark.asyncio
async def test_cancel_command_interrupts_session(hub):
    h, dsh = hub
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))
    await _inject(h, "/cancel")
    assert any("中断" in t for _, t, k in chan.sent)


@pytest.mark.asyncio
async def test_history_command_pulls_log():
    dsh = HistoryFakeDsh(
        events=[_history_event(1, "assistant/message", "summary line")]
    )
    h = BridgeHub(dsh, catch_up=False)
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))
    await h._handle_inbound(InboundMessage(channel="rec", conversation_id="c1", text="/history 5"))
    assert any("summary line" in t for _, t, k in chan.sent)


class HistoryFakeDsh(FakeDsh):
    """FakeDsh that returns a canned history for catch-up tests."""

    def __init__(self, events=None):
        super().__init__()
        self._events = events or []

    def history(self, session_id, max_messages=50, before_seq=None):
        if session_id == "s-999":
            raise DshError("session.history", "no such session")
        return {"events": self._events, "hasMore": False}


def _history_event(seq, type_, text):
    return {
        "event": {
            "type": type_,
            "seq": seq,
            "time": 1000.0 + seq,
            "data": {"message": {"content": [{"type": "text", "text": text}]}},
        },
        "view": None,
    }


@pytest.mark.asyncio
async def test_catch_up_backfills_after_watermark():
    """Sessions with a known watermark get missed events replayed on start."""
    events = [
        _history_event(1, "user/message", "prompt"),
        _history_event(2, "assistant/message", "done-1"),
        _history_event(3, "turn/end", ""),
        _history_event(4, "assistant/message", "done-2"),
        _history_event(5, "turn/end", ""),
    ]
    dsh = HistoryFakeDsh(events=events)
    h = BridgeHub(dsh, catch_up=True)
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))
    h._notified_seq["s-9"] = 2  # we had already notified through seq 2

    await h._catch_up()

    texts = "".join(t for _, t, k in chan.sent)
    assert "done-1" not in texts          # already notified
    assert "done-2" in texts              # missed, replayed
    assert h._notified_seq["s-9"] == 5    # watermark advanced


@pytest.mark.asyncio
async def test_catch_up_skips_unbound_and_new_sessions():
    """Brand-new sessions (no watermark) and unbound sessions get no backfill."""
    dsh = HistoryFakeDsh(events=[_history_event(2, "assistant/message", "x")])
    h = BridgeHub(dsh, catch_up=True)
    chan = RecordingChannel()
    h.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    h._add_binding(SessionBinding("rec:c1", "s-9"))  # bound but no watermark
    await h._catch_up()
    assert chan.sent == []
    assert "s-9" not in h._notified_seq
