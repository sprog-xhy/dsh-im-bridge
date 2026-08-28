"""Tests for the WPS 协作 (WOA) channel: crypto, send, receive.

The send path runs against a fake WPS API server; the receive path builds real
encrypted+signed webhook payloads and verifies the channel's signature check,
decryption and delivery — all without needing a real WPS app.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dsh_im_bridge.channels.woa import (
    WoaChannel,
    _kso1_signature,
    _event_signature,
    _rfc1123_date,
    decrypt_event_data,
    verify_event_signature,
)
from dsh_im_bridge.events import InboundMessage

APP_ID = "app-1"
SECRET = "secret-key-1"


class _FakeHub:
    def __init__(self):
        self.inbound = []

    def enqueue_inbound(self, message):
        self.inbound.append(message)


def _encrypt(secret_key: str, nonce: str, plaintext: str) -> str:
    """Mirror the WPS scheme: AES-256-CBC, key=md5(secretKey) hex as UTF-8,
    IV=nonce as UTF-8, PKCS7, base64."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = hashlib.md5(secret_key.encode()).hexdigest().encode("utf-8")
    iv = nonce.encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    ct = cipher.encryptor().update(padded) + cipher.encryptor().finalize()
    return base64.b64encode(ct).decode("ascii")


def _signed_event_body(secret_key: str, event_data: dict, nonce: str = "abcdefgh12345678") -> dict:
    import time

    ts = int(time.time())
    encrypted = _encrypt(secret_key, nonce, json.dumps(event_data, ensure_ascii=False))
    sig = _event_signature(APP_ID, secret_key, "kso.app_chat.message", nonce, ts, encrypted)
    return {
        "topic": "kso.app_chat.message",
        "nonce": nonce,
        "time": ts,
        "signature": sig,
        "encrypted_data": encrypted,
    }


def _text_event(text, chat_type="p2p", chat_id="oc-1", sender_id="u-1", mention_bot=True):
    msg = {
        "id": f"m-{chat_id}-{sender_id}",
        "type": "text",
        "content": {"text": {"content": text}},
    }
    if chat_type == "group":
        msg["mentions"] = [{"identity": {"type": "app"}}] if mention_bot else []
    return {"message": msg, "chat": {"id": chat_id, "type": chat_type}, "sender": {"id": sender_id}}


# -- crypto unit tests -----------------------------------------------------

def test_event_signature_stable_and_verifiable():
    body = _signed_event_body(SECRET, {"a": 1}, nonce="abcdefgh12345678")
    assert verify_event_signature(
        APP_ID, SECRET, body["topic"], body["nonce"], body["time"],
        body["encrypted_data"], body["signature"],
    ) is True


def test_event_signature_wrong_key_fails():
    body = _signed_event_body(SECRET, {"a": 1}, nonce="abcdefgh12345678")
    assert verify_event_signature(
        APP_ID, "wrong-secret", body["topic"], body["nonce"], body["time"],
        body["encrypted_data"], body["signature"],
    ) is False


def test_decrypt_roundtrip():
    nonce = "abcdefgh12345678"
    plain = '{"message":{"id":"1"}}'
    encrypted = _encrypt(SECRET, nonce, plain)
    assert decrypt_event_data(SECRET, encrypted, nonce) == plain


def test_kso1_signature_deterministic():
    sig1 = _kso1_signature(APP_ID, SECRET, "POST", "/v7/messages/create",
                           "application/json", "date", '{"a":1}')
    sig2 = _kso1_signature(APP_ID, SECRET, "POST", "/v7/messages/create",
                           "application/json", "date", '{"a":1}')
    assert sig1 == sig2
    assert sig1.startswith(f"KSO-1 {APP_ID}:")


# -- send path against a fake WPS API --------------------------------------

class _FakeWpsHandler(BaseHTTPRequestHandler):
    recorded = []
    create_code = 0
    create_msg = "ok"

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
        raw = self.rfile.read(n)
        self.recorded.append((self.path, dict(self.headers), raw))
        if self.path == "/oauth2/token":
            self._json({"access_token": "tok-1", "expires_in": 7200})
        elif self.path == "/v7/messages/create":
            self._json({"code": type(self).create_code, "msg": type(self).create_msg,
                        "data": {"message_id": "mid-1"}})
        else:
            self._json({"code": 404}, status=404)


@pytest.fixture()
def fake_wps():
    handler = _FakeWpsHandler
    handler.recorded = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.mark.asyncio
async def test_send_text_group(fake_wps):
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET, "apiUrl": fake_wps})
    await ch.send("group:g-1", "你好，世界")
    posts = _FakeWpsHandler.recorded
    assert any(p[0] == "/oauth2/token" for p in posts)
    create = [p for p in posts if p[0] == "/v7/messages/create"][0]
    headers, raw = create[1], create[2]
    body = json.loads(raw)
    assert body["receiver"] == {"receiver_id": "g-1", "type": "chat"}
    assert body["content"]["text"]["content"] == "你好，世界"
    assert headers.get("Authorization") == "Bearer tok-1"
    assert headers.get("X-Kso-Authorization", "").startswith(f"KSO-1 {APP_ID}:")
    assert "X-Kso-Date" in headers


@pytest.mark.asyncio
async def test_send_text_p2p(fake_wps):
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET, "apiUrl": fake_wps})
    await ch.send("p2p:u-1", "hi")
    create = [p for p in _FakeWpsHandler.recorded if p[0] == "/v7/messages/create"][0]
    assert json.loads(create[2])["receiver"] == {"receiver_id": "u-1", "type": "user"}


@pytest.mark.asyncio
async def test_send_failure_raises(fake_wps):
    _FakeWpsHandler.create_code = 190001
    _FakeWpsHandler.create_msg = "invalid app"
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET, "apiUrl": fake_wps})
    with pytest.raises(Exception):
        await ch.send("group:g-1", "x")


# -- receive path (webhook) ------------------------------------------------

@pytest.mark.asyncio
async def test_receive_p2p_text():
    hub = _FakeHub()
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET})
    ch.bind(hub)
    event = _text_event("你好", chat_type="p2p", chat_id="oc-1", sender_id="u-1")
    body = _signed_event_body(SECRET, event, nonce="abcdefgh12345678")
    ch._handle_message_event(body, json.dumps(body), {})
    assert len(hub.inbound) == 1
    msg = hub.inbound[0]
    assert msg.conversation_id == "p2p:u-1"
    assert msg.text == "你好"
    assert msg.sender == "u-1"


@pytest.mark.asyncio
async def test_receive_group_requires_at_bot():
    hub = _FakeHub()
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET})
    ch.bind(hub)
    # with @bot -> delivered
    event = _text_event("hi", chat_type="group", chat_id="g-1", mention_bot=True)
    ch._handle_message_event(_signed_event_body(SECRET, event), "", {})
    assert hub.inbound and hub.inbound[0].conversation_id == "group:g-1"
    # without @bot -> ignored
    hub.inbound.clear()
    event = _text_event("hi", chat_type="group", chat_id="g-1", mention_bot=False)
    ch._handle_message_event(_signed_event_body(SECRET, event), "", {})
    assert hub.inbound == []


@pytest.mark.asyncio
async def test_receive_bad_signature_dropped():
    hub = _FakeHub()
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET})
    ch.bind(hub)
    body = _signed_event_body(SECRET, _text_event("hi"), nonce="abcdefgh12345678")
    body["signature"] = "invalid"
    ch._handle_message_event(body, "", {})
    assert hub.inbound == []


@pytest.mark.asyncio
async def test_receive_strips_at_tags():
    hub = _FakeHub()
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET})
    ch.bind(hub)
    event = _text_event('<at id="1">@bot</at> 你好')
    ch._handle_message_event(_signed_event_body(SECRET, event), "", {})
    assert hub.inbound[0].text == "你好"


@pytest.mark.asyncio
async def test_receive_dedup_same_message_id():
    hub = _FakeHub()
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET})
    ch.bind(hub)
    event = _text_event("你好", chat_type="p2p", sender_id="u-1")
    body = _signed_event_body(SECRET, event, nonce="abcdefgh12345678")
    ch._handle_message_event(body, "", {})          # first -> delivered
    ch._handle_message_event(body, "", {})          # duplicate -> skipped
    assert len(hub.inbound) == 1


@pytest.mark.asyncio
async def test_challenge_get():
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET})
    res = await ch._route("/webhook?challenge=verify-me", "GET", {})
    assert res == {"challenge": "verify-me"}


@pytest.mark.asyncio
async def test_full_webhook_route_delivers():
    """End-to-end through the route: GET challenge + POST event -> deliver."""
    hub = _FakeHub()
    ch = WoaChannel({"appId": APP_ID, "secretKey": SECRET, "webhookPath": "/webhook"})
    ch.bind(hub)

    chall = await ch._route("/webhook?challenge=abc", "GET", {})
    assert chall["challenge"] == "abc"

    event = _text_event("你好世界", chat_type="p2p", sender_id="u-9")
    body = _signed_event_body(SECRET, event, nonce="abcdefgh12345678")
    await ch._route("/webhook", "POST", body, raw_body=json.dumps(body))
    assert hub.inbound and hub.inbound[0].text == "你好世界"
