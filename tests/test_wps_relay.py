"""Tests for the standalone WPS webhook relay (scripts/wps_relay.py).

A signed+encrypted WPS event posted to the relay's webhook should be verified,
decrypted, parsed, and forwarded to the bridge core's /message endpoint.
"""

import asyncio
import base64
import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from wps_relay import WpsRelay  # noqa: E402
from dsh_im_bridge.channels.woa import _event_signature  # noqa: E402

APP_ID = "app-relay"
SECRET = "secret-relay"


class _FakeCoreHandler(BaseHTTPRequestHandler):
    received = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.received.append((self.path, body))
        data = json.dumps({"accepted": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _encrypt(secret_key: str, nonce: str, plaintext: str) -> str:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = hashlib.md5(secret_key.encode()).hexdigest().encode("utf-8")
    iv = nonce.encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    ct = cipher.encryptor().update(padded) + cipher.encryptor().finalize()
    return base64.b64encode(ct).decode("ascii")


def _event_body(event: dict) -> dict:
    nonce = "abcdefgh12345678"
    ts = int(time.time())
    encrypted = _encrypt(SECRET, nonce, json.dumps(event, ensure_ascii=False))
    sig = _event_signature(APP_ID, SECRET, "kso.app_chat.message", nonce, ts, encrypted)
    return {"topic": "kso.app_chat.message", "nonce": nonce, "time": ts,
            "signature": sig, "encrypted_data": encrypted}


def _post(port, path, payload):
    import urllib.request

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _get(port, path):
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
        return json.loads(resp.read())


@pytest.fixture()
def fake_core():
    handler = _FakeCoreHandler
    handler.received = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/message"
    srv.shutdown()
    srv.server_close()


@pytest.mark.asyncio
async def test_relay_challenge(fake_core):
    relay = WpsRelay({"appId": APP_ID, "secretKey": SECRET, "apiUrl": "http://x",
                      "webhookPort": 0, "webhookHost": "127.0.0.1", "forwardUrl": fake_core})
    await relay.start()
    port = relay._server.bound_port
    try:
        assert (await asyncio.to_thread(_get, port, "/webhook?challenge=zzz"))["challenge"] == "zzz"
    finally:
        await relay.stop()


@pytest.mark.asyncio
async def test_relay_forwards_message(fake_core):
    relay = WpsRelay({"appId": APP_ID, "secretKey": SECRET, "apiUrl": "http://x",
                      "webhookPort": 0, "webhookHost": "127.0.0.1", "forwardUrl": fake_core})
    await relay.start()
    port = relay._server.bound_port
    try:
        event = {
            "message": {"id": "m-1", "type": "text",
                        "content": {"text": {"content": "你好世界"}}},
            "chat": {"id": "oc-1", "type": "p2p"},
            "sender": {"id": "u-9"},
        }
        body = _event_body(event)
        res = await asyncio.to_thread(_post, port, "/webhook", body)
        assert res["code"] == 0
        # give the relay a tick to forward
        await asyncio.sleep(0.5)
        assert len(_FakeCoreHandler.received) == 1
        path, forwarded = _FakeCoreHandler.received[0]
        assert path == "/message"
        assert forwarded["channel"] == "woa"
        assert forwarded["conversation_id"] == "p2p:u-9"
        assert forwarded["text"] == "你好世界"
    finally:
        await relay.stop()


@pytest.mark.asyncio
async def test_relay_drops_bad_signature(fake_core):
    relay = WpsRelay({"appId": APP_ID, "secretKey": SECRET, "apiUrl": "http://x",
                      "webhookPort": 0, "webhookHost": "127.0.0.1", "forwardUrl": fake_core})
    await relay.start()
    port = relay._server.bound_port
    try:
        event = {"message": {"id": "m-2", "type": "text", "content": {"text": {"content": "x"}}},
                 "chat": {"id": "oc", "type": "p2p"}, "sender": {"id": "u"}}
        body = _event_body(event)
        body["signature"] = "tampered"
        await asyncio.to_thread(_post, port, "/webhook", body)
        await asyncio.sleep(0.5)
        assert _FakeCoreHandler.received == []
    finally:
        await relay.stop()
