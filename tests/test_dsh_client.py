"""Tests for DshClient against a fake dsh wire (HTTP + WebSocket)."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import websockets

from dsh_im_bridge.dsh_client import DshClient, DshError


class _FakeApiHandler(BaseHTTPRequestHandler):
    """Responds to /api/<method> and /api/respond with canned data."""

    responses = {}
    recorded = []

    def log_message(self, *a):
        pass

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):  # noqa: N802
        body = self._read_body()
        self.recorded.append((self.path, body))
        if self.path == "/api/respond":
            self._json({"accepted": True})
            return
        method = self.path[len("/api/") :]
        canned = self.responses.get(method)
        if canned is None:
            self._json(
                {
                    "type": "server-response",
                    "rpcId": body.get("rpcId"),
                    "result": {"ok": False, "error": {"code": "unknown", "message": f"no canned response for {method}"}},
                }
            )
            return
        self._json(
            {
                "type": "server-response",
                "rpcId": body.get("rpcId"),
                "result": {"ok": True, "value": canned},
            }
        )


@pytest.fixture()
def fake_api():
    handler = _FakeApiHandler
    handler.responses = {
        "host.describe": {"version": "0.0.1", "cwd": "/tmp", "provider": "wps"},
        "session.list": {"items": [{"sessionId": "s-1", "running": False}]},
        "session.create": {"sessionId": "s-new"},
        "session.prompt": {"accepted": True},
        "session.cancel": {"accepted": True},
        "session.history": {"events": [], "hasMore": False},
        "session.attachment": {
            "attachment": {
                "attachmentId": "att-1",
                "mediaType": "image/png",
                "bytes": 4,
                "width": 2,
                "height": 2,
                "name": "plot.png",
            },
            "data": "iVBORw0KGgo=",
        },
        "workspace.list": {
            "items": [{"workspaceId": "w-1", "path": "/tmp", "sessionIds": ["s-1"]}],
            "archivedSessionIds": ["s-arch"],
        },
    }
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_call_unary(fake_api):
    client = DshClient(base_url=f"http://127.0.0.1:{fake_api}")
    assert client.describe()["version"] == "0.0.1"
    assert client.list_sessions()[0]["sessionId"] == "s-1"
    assert client.create_session()["sessionId"] == "s-new"


def test_archived_session_ids(fake_api):
    client = DshClient(base_url=f"http://127.0.0.1:{fake_api}")
    assert client.list_archived_session_ids() == ["s-arch"]
    # session.list does NOT filter archived sessions (caller must do so)
    assert [i["sessionId"] for i in client.list_sessions()] == ["s-1"]


def test_attachment(fake_api):
    client = DshClient(base_url=f"http://127.0.0.1:{fake_api}")
    value = client.attachment("s-1", "att-1")
    assert value["attachment"]["name"] == "plot.png"
    assert value["data"] == "iVBORw0KGgo="


def test_prompt_payload(fake_api):
    handler = _FakeApiHandler
    handler.recorded = []
    client = DshClient(base_url=f"http://127.0.0.1:{fake_api}")
    client.prompt("s-1", "hello", mode="steer")

    path, env = [r for r in handler.recorded if r[0] == "/api/session.prompt"][0]
    assert path == "/api/session.prompt"
    assert env["type"] == "client-request"
    assert env["method"] == "session.prompt"
    assert env["payload"]["sessionId"] == "s-1"
    assert env["payload"]["mode"] == "steer"
    assert env["payload"]["content"] == [{"type": "text", "text": "hello"}]


def test_respond(fake_api):
    client = DshClient(base_url=f"http://127.0.0.1:{fake_api}")
    ok = client.answer_question("r-1", "s-1", [{"id": "q1", "selected": ["是"]}])
    assert ok is True


def test_error_raises(fake_api):
    client = DshClient(base_url=f"http://127.0.0.1:{fake_api}")
    with pytest.raises(DshError):
        client.call("no.such.method", {})


@pytest.mark.asyncio
async def test_mux_stream(fake_api):
    received = []

    async def server(ws):
        frames = [
            {
                "type": "server-request",
                "rpcId": "r1",
                "method": "session/subscribed",
                "payload": {"type": "session/subscribed", "sessionId": "s-1", "lastSeq": 0},
            },
            {
                "type": "server-request",
                "rpcId": "r2",
                "method": "session/event",
                "payload": {
                    "type": "session/event",
                    "sessionId": "s-1",
                    "event": {
                        "type": "assistant/message",
                        "seq": 1,
                        "time": 123.0,
                        "data": {"content": [{"type": "text", "text": "hi"}]},
                    },
                },
            },
        ]
        for f in frames:
            await ws.send(json.dumps(f))
        await ws.close()

    stop_ws = websockets.serve(server, "127.0.0.1", 0)
    async with stop_ws as srv:
        port = srv.sockets[0].getsockname()[1]
        client = DshClient(
            base_url=f"http://127.0.0.1:{fake_api}", ws_base=f"ws://127.0.0.1:{port}"
        )
        stop = asyncio.Event()

        async def on_frame(parsed):
            received.append(parsed)
            if len(received) >= 2:
                stop.set()

        await asyncio.wait_for(client.stream(on_frame, stop=stop), timeout=5)

    assert received[0]["kind"] == "session/subscribed"
    assert received[1]["kind"] == "session/event"
    assert received[1]["event"].text == "hi"


@pytest.mark.asyncio
async def test_mux_reconnect(fake_api):
    """Stream should reconnect after the server drops the socket."""
    received = []
    count = {"n": 0}

    async def server(ws):
        count["n"] += 1
        if count["n"] == 1:
            await ws.close()  # first connection dies immediately
            return
        await ws.send(
            json.dumps(
                {
                    "type": "server-request",
                    "rpcId": "r3",
                    "method": "session/subscribed",
                    "payload": {"type": "session/subscribed", "sessionId": "s-1", "lastSeq": 1},
                }
            )
        )
        await ws.close()

    stop_ws = websockets.serve(server, "127.0.0.1", 0)
    async with stop_ws as srv:
        port = srv.sockets[0].getsockname()[1]
        client = DshClient(
            base_url=f"http://127.0.0.1:{fake_api}",
            ws_base=f"ws://127.0.0.1:{port}",
            backoff_base=0.05,
        )
        stop = asyncio.Event()

        async def on_frame(parsed):
            received.append(parsed)
            stop.set()

        await asyncio.wait_for(client.stream(on_frame, stop=stop), timeout=5)

    assert received and received[0]["kind"] == "session/subscribed"
    assert count["n"] >= 2
