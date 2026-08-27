"""Tests for QQ OneBot channel and the webhook channel."""

import asyncio
import io
import json

import pytest
import websockets

from dsh_im_bridge.channels.console import ConsoleChannel
from dsh_im_bridge.channels.qq import QQOneBotChannel, _extract_text
from dsh_im_bridge.channels.webhook import WebhookChannel
from dsh_im_bridge.events import InboundMessage


def test_extract_text():
    segs = [
        {"type": "text", "data": {"text": "你好"}},
        {"type": "at", "data": {"qq": "123"}},
        {"type": "face", "data": {"id": "1"}},
        {"type": "image"},
    ]
    out = _extract_text(segs)
    assert "你好" in out
    assert "@123" in out
    assert "[图片]" in out
    assert _extract_text("plain") == "plain"


class _FakeHub:
    def __init__(self):
        self.inbound = []

    def enqueue_inbound(self, message):
        self.inbound.append(message)


@pytest.mark.asyncio
async def test_qq_onebot_receive_and_send():
    hub = _FakeHub()
    channel = QQOneBotChannel(
        {"wsUrl": "ws://127.0.0.1:0", "selfId": "10001", "allowGroups": [123]}
    )
    channel.bind(hub)

    received_actions = []

    async def fake_server(ws):
        async for raw in ws:
            req = json.loads(raw)
            received_actions.append(req)
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "echo": req.get("echo")}))

    srv = websockets.serve(fake_server, "127.0.0.1", 0)
    async with srv as server:
        port = server.sockets[0].getsockname()[1]
        channel.ws_url = f"ws://127.0.0.1:{port}"
        await channel.start()
        await asyncio.sleep(0.2)

        # simulate an inbound group message from the bot's perspective
        msg = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123,
            "user_id": 999,
            "raw_message": "hello",
            "message": [{"type": "text", "data": {"text": "hello"}}],
            "self_id": 10001,
        }
        # push it directly through the handler (the fake server is for sends)
        await channel._on_message(msg)
        assert hub.inbound and hub.inbound[0].conversation_id == "group:123"
        assert hub.inbound[0].text == "hello"

        # send to group
        await channel.send("group:123", "hi back")
        assert received_actions and received_actions[0]["action"] == "send_group_msg"
        assert received_actions[0]["params"]["group_id"] == 123
        assert received_actions[0]["params"]["message"] == "hi back"

        await channel.stop()


@pytest.mark.asyncio
async def test_qq_ignores_self_and_filtered_groups():
    channel = QQOneBotChannel({"selfId": "10001", "allowGroups": [123]})
    hub = _FakeHub()
    channel.bind(hub)

    # self message ignored
    await channel._on_message(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123,
            "user_id": 10001,
            "message": [{"type": "text", "data": {"text": "echo"}}],
        }
    )
    assert hub.inbound == []

    # group not in allowlist ignored
    await channel._on_message(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": 456,
            "user_id": 999,
            "message": [{"type": "text", "data": {"text": "hi"}}],
        }
    )
    assert hub.inbound == []

    # private message passes by default
    await channel._on_message(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 777,
            "message": [{"type": "text", "data": {"text": "pm"}}],
        }
    )
    assert hub.inbound and hub.inbound[0].conversation_id == "private:777"


@pytest.mark.asyncio
async def test_console_feed_text_delivers_to_hub():
    """feed_text() must route into the hub (was a silent bug: it queued and never drained)."""
    hub = _FakeHub()
    channel = ConsoleChannel()
    channel.bind(hub)
    await channel.feed_text("hello console")
    assert hub.inbound and hub.inbound[0].text == "hello console"
    assert hub.inbound[0].conversation_id == "default"


def test_console_emit_utf8_safe(monkeypatch):
    """Console outbound with emoji must be written as UTF-8, never crash on GBK."""

    class _Out:
        def __init__(self):
            self.buffer = io.BytesIO()

    out = _Out()
    monkeypatch.setattr("sys.stdout", out)
    ConsoleChannel._write_stdout("❓ 需要你确认 ✅")
    data = out.buffer.getvalue()
    assert "❓ 需要你确认 ✅".encode("utf-8") in data


@pytest.mark.asyncio
async def test_webhook_channel_http():
    hub = _FakeHub()
    channel = WebhookChannel({"port": 0})
    channel.bind(hub)
    await channel.start()
    port = channel._server.bound_port

    import urllib.request

    def _post(path, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    resp = await asyncio.to_thread(
        _post, "/message", {"text": "hello from webhook", "conversation_id": "wc-1"}
    )
    assert resp["accepted"] is True

    # give the loop a tick to drain the delivery
    await asyncio.sleep(0.1)
    assert hub.inbound and hub.inbound[0].text == "hello from webhook"
    assert hub.inbound[0].conversation_id == "wc-1"

    def _get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
            return json.loads(resp.read())

    assert (await asyncio.to_thread(_get, "/health"))["ok"] is True

    await channel.stop()
