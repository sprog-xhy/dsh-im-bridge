"""Tests for the QQ official open-platform bot channel.

Covers the parts testable without a real QQ bot app: token fetch + caching,
C2C_MESSAGE_CREATE -> InboundMessage mapping, dedupe, the official send API
call shape (msg_id passive reply, err_code failure detection), allow-list
filtering, and the gateway handshake / resume / heartbeat over a fake
WebSocket server.
"""

import asyncio
import json
import time

import pytest
import websockets

from dsh_im_bridge.channels.qq_official import (
    INTENT_GROUP_AND_C2C,
    PROD_WS_URL,
    SANDBOX_WS_URL,
    QqOfficialChannel,
    QqOfficialError,
    _extract_c2c_text,
)


class _FakeHub:
    def __init__(self):
        self.inbound = []

    def enqueue_inbound(self, message):
        self.inbound.append(message)


def _make_channel(**overrides):
    cfg = {
        "appId": "1234567890",  # placeholder (never use a real AppSecret in tests)
        "appSecret": "placeholder-secret-do-not-commit",
    }
    cfg.update(overrides)
    return QqOfficialChannel(cfg)


class _FakeResp:
    def __init__(self, data, status=200, content=None):
        self._data = data
        self.status_code = status
        self.content = content if content is not None else (json.dumps(data).encode() if data is not None else b"")

    def json(self):
        return self._data


# -- text extraction (receive-side message_type) ------------------------
def test_extract_text_plain():
    assert _extract_c2c_text({"message_type": 0, "content": "hello 你好"}) == "hello 你好"
    assert _extract_c2c_text({"content": "no type"}) == "no type"
    assert _extract_c2c_text({"message_type": 3, "content": "card"}) == "[卡片消息]"
    assert _extract_c2c_text({"message_type": 101}) == "[并行消息]"
    assert _extract_c2c_text({"message_type": 102}) == "[聊天记录]"
    assert _extract_c2c_text({"message_type": 103}) == "[引用消息]"
    assert _extract_c2c_text({"message_type": 99}) == "[消息类型99]"


# -- api / ws urls ------------------------------------------------------
def test_api_and_ws_urls_sandbox():
    ch = _make_channel(sandbox=True)
    assert ch.api_root() == "https://sandbox.api.bot.qq.com"
    assert ch.ws_url() == SANDBOX_WS_URL


def test_api_and_ws_urls_prod():
    ch = _make_channel(sandbox=False)
    assert ch.api_root() == "https://api.bot.qq.com"
    assert ch.ws_url() == PROD_WS_URL


def test_api_and_ws_urls_custom_base():
    ch = _make_channel(baseUrl="https://example.test")
    assert ch.api_root() == "https://example.test"
    assert ch.ws_url() == "wss://example.test/websocket"


def test_gateway_url_discovery(monkeypatch):
    """Official flow fetches the WSS url from /gateway/bot; custom base skips it."""
    ch = _make_channel()
    got = {}

    def fake_get(url, headers=None, timeout=None):
        got["url"] = url
        got["headers"] = headers
        return _FakeResp({"url": "wss://gateway.example/websocket", "shards": 1})

    monkeypatch.setattr("dsh_im_bridge.channels.qq_official.requests.get", fake_get)
    assert ch._gateway_url("tok") == "wss://gateway.example/websocket"
    assert got["url"].endswith("/gateway/bot")
    assert got["headers"]["Authorization"] == "QQBot tok"
    assert got["headers"]["X-Union-Appid"] == "1234567890"


def test_gateway_url_fallback_on_failure(monkeypatch):
    """If discovery fails, fall back to the fixed default URL."""
    ch = _make_channel(sandbox=False)

    def fake_get(url, headers=None, timeout=None):
        raise RuntimeError("network down")

    monkeypatch.setattr("dsh_im_bridge.channels.qq_official.requests.get", fake_get)
    assert ch._gateway_url("tok") == PROD_WS_URL


def test_gateway_url_skipped_for_custom_base():
    ch = _make_channel(baseUrl="https://example.test")
    # custom base: discovery is skipped, derived URL returned directly
    assert ch._gateway_url("tok") == "wss://example.test/websocket"


# -- token --------------------------------------------------------------
def test_access_token_cached(monkeypatch):
    ch = _make_channel()
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResp({"access_token": "tok-1", "expires_in": "7200"})

    monkeypatch.setattr("dsh_im_bridge.channels.qq_official.requests.post", fake_post)
    assert ch._access_token() == "tok-1"
    assert ch._access_token() == "tok-1"  # cached
    assert calls["n"] == 1


def test_access_token_nested_data(monkeypatch):
    ch = _make_channel()

    def fake_post(url, json=None, timeout=None):
        return _FakeResp({"code": 0, "data": {"access_token": "nested", "expires_in": "3600"}})

    monkeypatch.setattr("dsh_im_bridge.channels.qq_official.requests.post", fake_post)
    assert ch._access_token() == "nested"


def test_access_token_missing_creds():
    ch = _make_channel(appId="", appSecret="")
    with pytest.raises(QqOfficialError):
        ch._access_token()


def test_access_token_error_response(monkeypatch):
    ch = _make_channel()

    def fake_post(url, json=None, timeout=None):
        return _FakeResp({"code": 100007, "message": "appid invalid"})

    monkeypatch.setattr("dsh_im_bridge.channels.qq_official.requests.post", fake_post)
    with pytest.raises(QqOfficialError):
        ch._access_token()


# -- send ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_c2c_calls_official_api(monkeypatch):
    ch = _make_channel()
    ch._last_msg_id["openid_123"] = "ROBOT1.0_msg"
    sent = {}

    def capture(url, json=None, headers=None, timeout=None):
        if "/v2/users/" in url:
            sent["url"] = url
            sent["json"] = json
            sent["headers"] = headers
            return _FakeResp({"id": "ROBOT1.0_ok", "timestamp": "2026-01-01T00:00:00+08:00"})
        return _FakeResp({"access_token": "tok", "expires_in": "7200"})

    monkeypatch.setattr("dsh_im_bridge.channels.qq_official.requests.post", capture)
    await ch.send("openid_123", "hi back")
    await ch.send("openid_123", "second")  # msg_seq must increment
    assert sent["url"].endswith("/v2/users/openid_123/messages")
    assert sent["json"]["content"] == "second"
    assert sent["json"]["msg_type"] == 0
    assert sent["json"]["msg_id"] == "ROBOT1.0_msg"  # passive reply id attached
    assert sent["json"]["msg_seq"] == 2  # monotonic
    assert sent["headers"]["Authorization"] == "QQBot tok"
    assert sent["headers"]["X-Union-Appid"] == "1234567890"


@pytest.mark.asyncio
async def test_send_c2c_error_on_err_code(monkeypatch):
    ch = _make_channel()

    def capture(url, json=None, headers=None, timeout=None):
        if "/v2/users/" in url:
            return _FakeResp(
                {"err_code": 40034005, "message": "回复消息msg_id已过期", "trace_id": "t"},
                status=400,
            )
        return _FakeResp({"access_token": "tok", "expires_in": "7200"})

    monkeypatch.setattr("dsh_im_bridge.channels.qq_official.requests.post", capture)
    with pytest.raises(QqOfficialError):
        await ch.send("openid_x", "hi")


@pytest.mark.asyncio
async def test_send_c2c_ok_without_code_field(monkeypatch):
    """A successful reply returns bare business data (no code/err_code) — must not raise."""
    ch = _make_channel()

    def capture(url, json=None, headers=None, timeout=None):
        if "/v2/users/" in url:
            return _FakeResp({"id": "ROBOT1.0_ok", "timestamp": "..."})
        return _FakeResp({"access_token": "tok", "expires_in": "7200"})

    monkeypatch.setattr("dsh_im_bridge.channels.qq_official.requests.post", capture)
    await ch.send("openid_x", "hi")  # must not raise


# -- receive ------------------------------------------------------------
@pytest.mark.asyncio
async def test_c2c_event_mapping():
    hub = _FakeHub()
    ch = _make_channel()
    ch.bind(hub)
    await ch._on_c2c_message(
        {
            "id": "ROBOT1.0_m1",
            "author": {"user_openid": "openid_a", "bot_appid": "1234567890"},
            "content": "你好 dsh",
            "message_type": 0,
        }
    )
    assert len(hub.inbound) == 1
    msg = hub.inbound[0]
    assert msg.channel == "qq_official"
    assert msg.conversation_id == "openid_a"
    assert msg.text == "你好 dsh"
    assert msg.sender == "openid_a"


@pytest.mark.asyncio
async def test_c2c_event_dedup_repeated_msg_id():
    hub = _FakeHub()
    ch = _make_channel()
    ch.bind(hub)
    ev = {
        "id": "ROBOT1.0_dup",
        "author": {"user_openid": "openid_a"},
        "content": "hi",
        "message_type": 0,
    }
    await ch._on_c2c_message(ev)
    await ch._on_c2c_message(ev)  # same msg_id re-delivered -> dropped
    assert len(hub.inbound) == 1


@pytest.mark.asyncio
async def test_c2c_event_allowlist_filter():
    hub = _FakeHub()
    ch = _make_channel(allowUsers=["openid_ok"])
    ch.bind(hub)
    await ch._on_c2c_message(
        {"id": "m1", "author": {"user_openid": "openid_ok"}, "content": "allowed", "message_type": 0}
    )
    await ch._on_c2c_message(
        {"id": "m2", "author": {"user_openid": "openid_no"}, "content": "denied", "message_type": 0}
    )
    assert len(hub.inbound) == 1
    assert hub.inbound[0].conversation_id == "openid_ok"


@pytest.mark.asyncio
async def test_c2c_event_missing_openid_ignored():
    hub = _FakeHub()
    ch = _make_channel()
    ch.bind(hub)
    await ch._on_c2c_message({"id": "m1", "content": "no author", "message_type": 0})
    assert hub.inbound == []


@pytest.mark.asyncio
async def test_dispatch_routes_c2c_only():
    hub = _FakeHub()
    ch = _make_channel()
    ch.bind(hub)
    await ch._on_dispatch({"t": "C2C_MESSAGE_CREATE", "d": {"id": "m1", "author": {"user_openid": "u1"}, "content": "hi", "message_type": 0}})
    await ch._on_dispatch({"t": "GROUP_AT_MESSAGE_CREATE", "d": {"author": {"member_openid": "g1"}, "content": "group"}})
    assert len(hub.inbound) == 1
    assert hub.inbound[0].text == "hi"


# -- WS handshake / resume / heartbeat ----------------------------------
@pytest.mark.asyncio
async def test_ws_loop_handshake_and_delivery():
    """Full loop over a fake gateway: HELLO -> IDENTIFY -> READY -> C2C event
    -> delivery."""
    hub = _FakeHub()
    ch = _make_channel()
    ch.bind(hub)
    sent_frames = []

    async def fake_gateway(ws):
        await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 41250}}))
        ident = json.loads(await ws.recv())
        sent_frames.append(ident)
        assert ident["op"] == 2
        assert ident["d"]["token"].startswith("QQBot ")
        assert ident["d"]["intents"] & INTENT_GROUP_AND_C2C
        await ws.send(json.dumps({"op": 0, "t": "READY", "s": 0, "d": {"session_id": "sess-1"}}))
        await ws.send(
            json.dumps(
                {
                    "op": 0,
                    "t": "C2C_MESSAGE_CREATE",
                    "s": 3,
                    "d": {"id": "ROBOT1.0_ws", "author": {"user_openid": "openid_ws"}, "content": "hello from ws", "message_type": 0},
                }
            )
        )
        try:
            hb = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.5))
            sent_frames.append(hb)
        except Exception:  # noqa: BLE001
            pass
        await ws.close()

    srv = websockets.serve(fake_gateway, "127.0.0.1", 0)
    async with srv as server:
        port = server.sockets[0].getsockname()[1]
        ch.base_url = f"http://127.0.0.1:{port}"  # custom base -> wss://.../websocket
        ch._token = "tok"
        ch._token_expires_at = time.time() + 3600

        task = asyncio.create_task(ch._ws_loop())

        async def wait_delivered():
            for _ in range(100):
                if hub.inbound:
                    return
                await asyncio.sleep(0.05)

        try:
            await asyncio.wait_for(wait_delivered(), timeout=6)
            assert hub.inbound[0].text == "hello from ws"
            assert hub.inbound[0].conversation_id == "openid_ws"
            assert any(f.get("op") == 2 for f in sent_frames)  # identified
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


@pytest.mark.asyncio
async def test_ws_loop_resume_on_reconnect():
    """When a session_id is already held, the client resumes (op 6) instead of
    re-identifying."""
    ch = _make_channel()
    ch._session_id = "sess-old"
    ch._last_seq = 42
    sent_frames = []

    async def fake_gateway(ws):
        await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 41250}}))
        resume = json.loads(await ws.recv())
        sent_frames.append(resume)
        assert resume["op"] == 6
        assert resume["d"]["session_id"] == "sess-old"
        assert resume["d"]["seq"] == 42
        await ws.send(json.dumps({"op": 0, "t": "RESUMED", "s": 0}))
        await ws.close()

    srv = websockets.serve(fake_gateway, "127.0.0.1", 0)
    async with srv as server:
        port = server.sockets[0].getsockname()[1]
        ch.base_url = f"http://127.0.0.1:{port}"
        ch._token = "tok"
        ch._token_expires_at = time.time() + 3600

        task = asyncio.create_task(ch._ws_loop())
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        assert any(f.get("op") == 6 for f in sent_frames)  # resumed, not identified
