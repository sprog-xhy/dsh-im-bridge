"""Tests for the bridge management HTTP API (server.py)."""

import asyncio
import json
import urllib.request

import pytest

from dsh_im_bridge.dsh_client import DshError
from dsh_im_bridge.events import InboundMessage, QuestionItem
from dsh_im_bridge.hub import BridgeHub
from dsh_im_bridge.server import BridgeServer
from dsh_im_bridge.channels.base import Channel


class FakeDsh:
    def __init__(self):
        self.prompts = []
        self.answers = []
        self.created = []
        self.approvals = []

    def prompt(self, session_id, text, mode="queue"):
        self.prompts.append((session_id, text, mode))

    def create_session(self, **payload):
        self.created.append(payload)
        return {"sessionId": "s-new"}

    def answer_question(self, rpc_id, session_id, answers):
        self.answers.append((rpc_id, session_id, answers))
        return True

    def cancel_question(self, rpc_id, session_id):
        return True

    def resolve_approval(self, rpc_id, session_id, approval_id, outcome):
        self.approvals.append((rpc_id, session_id, approval_id, outcome))
        return True

    def describe(self):
        return {"version": "0.0.1", "cwd": "/tmp", "provider": "wps", "model": "m"}

    def list_sessions(self):
        return [{"sessionId": "s-1", "running": False}]

    def history(self, session_id, max_messages=50, before_seq=None):
        return {"events": [], "hasMore": False}

    def cancel(self, session_id):
        pass

    async def stream(self, on_frame, *, stop=None, backoff=None):
        if stop is not None:
            await stop.wait()


class FakeChannel(Channel):
    name = "fake"

    def __init__(self, config=None):
        super().__init__(config)
        self.sent = []

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send(self, conversation_id, text, kind="notify"):
        self.sent.append((conversation_id, text, kind))


def _http(port, path, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"content-type": "application/json"} if data else {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _http_in_thread(port, path, method="GET", payload=None):
    return asyncio.to_thread(_http, port, path, method, payload)


@pytest.mark.asyncio
async def test_server_status_and_prompt():
    dsh = FakeDsh()
    hub = BridgeHub(dsh, catch_up=False)
    chan = FakeChannel()
    hub.register(chan)
    server = BridgeServer(hub, port=0)
    await server.start()
    port = server.bound_port
    try:
        status = await _http_in_thread(port, "/status")
        assert status["ok"] is True
        assert status["dsh"]["version"] == "0.0.1"
        assert status["channels"] == ["fake"]

        res = await _http_in_thread(port, "/prompt", "POST", {"sessionId": "s-1", "text": "hi"})
        assert res["accepted"] is True
        assert dsh.prompts == [("s-1", "hi", "queue")]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_server_message_binds_and_prompts():
    dsh = FakeDsh()
    hub = BridgeHub(dsh, catch_up=False)
    chan = FakeChannel()
    hub.register(chan)
    await hub.start()
    server = BridgeServer(hub, port=0)
    await server.start()
    port = server.bound_port
    try:
        res = await _http_in_thread(
            port, "/message", "POST", {"channel": "fake", "conversation_id": "c1", "text": "hello"}
        )
        assert res["accepted"] is True
        await asyncio.sleep(0.1)
        assert dsh.created  # auto-bound
        assert dsh.prompts and dsh.prompts[0][1] == "hello"
    finally:
        await server.stop()
        await hub.stop()


@pytest.mark.asyncio
async def test_server_answer_and_approval():
    dsh = FakeDsh()
    hub = BridgeHub(dsh, catch_up=False)
    chan = FakeChannel()
    hub.register(chan)
    from dsh_im_bridge.hub import SessionBinding

    hub._add_binding(SessionBinding("fake:c1", "s-1"))
    server = BridgeServer(hub, port=0)
    await server.start()
    port = server.bound_port
    try:
        # prime a pending question (with its real question list)
        hub.pending["rq-1"] = {
            "kind": "question",
            "session_id": "s-1",
            "conversations": ["fake:c1"],
            "questions": [QuestionItem(id="uuid-q", question="继续?", options=({"label": "是"},))],
        }
        res = await _http_in_thread(
            port, "/answer", "POST", {"channel": "fake", "conversation_id": "c1", "text": "1:是"}
        )
        assert res["accepted"] is True
        assert dsh.answers == [("rq-1", "s-1", [{"id": "uuid-q", "selected": ["是"], "custom": ""}])]

        # prime a pending approval
        hub.pending["ra-1"] = {
            "kind": "approval",
            "session_id": "s-1",
            "approval_id": "a-1",
            "conversations": ["fake:c1"],
        }
        res = await _http_in_thread(
            port, "/approval", "POST", {"channel": "fake", "conversation_id": "c1", "outcome": "allow"}
        )
        assert res["accepted"] is True
        assert dsh.approvals == [("ra-1", "s-1", "a-1", "allowed-once")]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_server_bind():
    dsh = FakeDsh()
    hub = BridgeHub(dsh, catch_up=False)
    server = BridgeServer(hub, port=0)
    await server.start()
    port = server.bound_port
    try:
        res = await _http_in_thread(
            port, "/bind", "POST", {"channel": "fake", "conversation_id": "c9", "session_id": "s-9"}
        )
        assert res["accepted"] is True
        assert hub.bindings["fake:c9"].session_id == "s-9"
    finally:
        await server.stop()
