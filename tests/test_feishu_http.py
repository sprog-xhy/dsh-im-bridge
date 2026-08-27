"""Tests for the Feishu HTTP send paths (webhook + app-bot im/v1/messages).

Runs against a fake Feishu API server, so no real credentials are needed; the
request building / token caching / error handling are exercised.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dsh_im_bridge.channels.feishu import FeishuChannel, FeishuError


class _FakeFeishuHandler(BaseHTTPRequestHandler):
    recorded = []
    token_requests = 0
    im_code = 0
    token_code = 0

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.recorded.append((self.path, dict(self.headers), body))
        if self.path.startswith("/open-apis/auth/v3/tenant_access_token/internal"):
            type(self).token_requests += 1
            self._json({"code": type(self).token_code, "tenant_access_token": "tok-123", "expire": 7200})
        elif self.path.startswith("/open-apis/im/v1/messages"):
            self._json({"code": type(self).im_code})
        elif self.path.startswith("/bot/v2/hook/"):
            self._json({"code": 0})
        else:
            self._json({"code": 404}, status=404)


@pytest.fixture()
def fake_feishu():
    handler = _FakeFeishuHandler
    handler.recorded = []
    handler.token_requests = 0
    handler.im_code = 0
    handler.token_code = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.mark.asyncio
async def test_send_via_app_bot(fake_feishu):
    channel = FeishuChannel(
        {"appId": "cli_x", "appSecret": "sec", "baseUrl": fake_feishu}
    )
    await channel.send("oc_chat1", "你好")
    posts = _FakeFeishuHandler.recorded
    assert any(p[0].startswith("/open-apis/auth/v3/tenant_access_token/internal") for p in posts)
    im = [p for p in posts if p[0].startswith("/open-apis/im/v1/messages")][0]
    assert im[1].get("Authorization") == "Bearer tok-123"
    assert im[2]["receive_id"] == "oc_chat1"
    assert json.loads(im[2]["content"])["text"] == "你好"


@pytest.mark.asyncio
async def test_tenant_token_cached(fake_feishu):
    channel = FeishuChannel({"appId": "cli_x", "appSecret": "sec", "baseUrl": fake_feishu})
    await channel.send("oc_a", "one")
    await channel.send("oc_b", "two")
    assert _FakeFeishuHandler.token_requests == 1  # token fetched once, then cached


@pytest.mark.asyncio
async def test_send_via_webhook(fake_feishu):
    channel = FeishuChannel({"webhookUrl": f"{fake_feishu}/bot/v2/hook/token123"})
    await channel.send("ignored-chat", "hello")
    post = _FakeFeishuHandler.recorded[-1]
    assert post[0].startswith("/bot/v2/hook/")
    assert post[2] == {"msg_type": "text", "content": {"text": "hello"}}


@pytest.mark.asyncio
async def test_send_im_error_raises(fake_feishu):
    _FakeFeishuHandler.im_code = 190001  # app not found
    channel = FeishuChannel({"appId": "cli_x", "appSecret": "sec", "baseUrl": fake_feishu})
    with pytest.raises(FeishuError):
        await channel.send("oc_chat1", "hi")


@pytest.mark.asyncio
async def test_send_without_config_raises(fake_feishu):
    channel = FeishuChannel({})
    with pytest.raises(FeishuError):
        await channel.send("oc_chat1", "hi")
