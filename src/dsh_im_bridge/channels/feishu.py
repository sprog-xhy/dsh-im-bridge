"""Feishu (飞书 / Lark) channel.

Two integration modes are supported:

* **Custom bot webhook** (``webhookUrl``): send-only. Posts text to a
  ``https://open.feishu.cn/open-apis/bot/v2/hook/<token>`` URL. This needs no
  credentials and is the fastest way to get dsh notifications into Feishu, but
  custom bots cannot *receive* messages.

* **App bot** (``appId`` + ``appSecret``): full two-way. Sends via
  ``im/v1/messages`` with a cached tenant access token, and receives message
  events over the Feishu event **long connection** (WebSocket). Requires an
  app with the ``im:message`` / ``im:message:send_as_bot`` scopes and an event
  subscription for ``im.message.receive_v1`` (long connection mode).

The implementation follows the public Feishu open-platform protocol. It has not
been exercised against a real app (no credentials available during
development) — see REPORT.md. Encryption (``encryptKey``) uses AES-256-CBC and
requires the optional ``cryptography`` package.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Optional

import requests

from ..events import InboundMessage
from .base import Channel

log = logging.getLogger("dsh_im_bridge.channel.feishu")

FEISHU_BASE = "https://open.feishu.cn"


class FeishuError(RuntimeError):
    pass


# -- Feishu long-connection (WebSocket) protobuf framing ----------------------
# The current official Feishu long-connection protocol (verified against
# lark-oapi 1.7.3) is:
#   1. POST {base}/callback/ws/endpoint  body {"AppID", "AppSecret"} -> data.URL
#   2. connect to URL (auth is baked into the URL)
#   3. frames are a small protobuf envelope ("pbbp2.Frame"):
#        field 1 SeqID  (uint64, varint)
#        field 2 LogID  (uint64, varint)
#        field 3 service(int32, varint)
#        field 4 method (int32, varint)   0=CONTROL 1=DATA
#        field 5 headers(repeated {key,value}, length-delimited)
#        field 8 payload(bytes, length-delimited)
#   4. CONTROL frames carry type header ping/pong (no reply needed for ping);
#      DATA frames carry type header event/card; EVENT payload is JSON
#        {"schema":"2.0","header":{"event_type":"..."},"event":{...}}
#      and must be ACKed by sending the same frame back with
#      payload = JSON {"code":200}.
#   5. the client sends a PING control frame every ~120s.
# We implement the wire format directly (no protobuf dependency).

def _pb_varint(n: int) -> bytes:
    n &= 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _pb_len(data: bytes) -> bytes:
    return _pb_varint(len(data)) + data


def _encode_header(key: str, value: str) -> bytes:
    return _pb_varint(1 << 3 | 2) + _pb_len(key.encode("utf-8")) + \
        _pb_varint(2 << 3 | 2) + _pb_len(value.encode("utf-8"))


def encode_frame(seq: int = 0, log: int = 0, service: int = 0, method: int = 0,
                 headers: Optional[list] = None, payload: bytes = b"") -> bytes:
    out = b""
    if seq:
        out += _pb_varint(1 << 3 | 0) + _pb_varint(seq)
    if log:
        out += _pb_varint(2 << 3 | 0) + _pb_varint(log)
    out += _pb_varint(3 << 3 | 0) + _pb_varint(service)
    out += _pb_varint(4 << 3 | 0) + _pb_varint(method)
    for key, value in (headers or []):
        out += _pb_varint(5 << 3 | 2) + _pb_len(_encode_header(key, value))
    if payload:
        out += _pb_varint(8 << 3 | 2) + _pb_len(payload)
    return out


def decode_frame(data: bytes) -> dict:
    """Decode a pbbp2.Frame. Returns {seq, log, service, method, headers, payload}."""
    result = {"seq": 0, "log": 0, "service": 0, "method": 0, "headers": [], "payload": b""}
    i, n = 0, len(data)

    def _varint():
        nonlocal i
        val, shift = 0, 0
        while i < n:
            b = data[i]
            i += 1
            val |= (b & 0x7F) << shift
            if not (b & 0x80):
                return val
            shift += 7
        raise ValueError("truncated varint")

    while i < n:
        key = _varint()
        field, wire = key >> 3, key & 7
        if wire == 0:
            val = _varint()
            if field == 1:
                result["seq"] = val
            elif field == 2:
                result["log"] = val
            elif field == 3:
                result["service"] = val
            elif field == 4:
                result["method"] = val
        elif wire == 2:
            ln = _varint()
            chunk = data[i:i + ln]
            i += ln
            if field == 5:
                # Header submessage: key=1(value), value=2(value)
                hk = hv = ""
                j, m = 0, len(chunk)

                def h_varint():
                    nonlocal j
                    v, s = 0, 0
                    while j < m:
                        b2 = chunk[j]
                        j += 1
                        v |= (b2 & 0x7F) << s
                        if not (b2 & 0x80):
                            return v
                        s += 7
                    raise ValueError("truncated header")

                while j < m:
                    k2 = h_varint()
                    f2 = k2 >> 3
                    l2 = h_varint()
                    s2 = chunk[j:j + l2].decode("utf-8", "replace")
                    j += l2
                    if f2 == 1:
                        hk = s2
                    elif f2 == 2:
                        hv = s2
                result["headers"].append((hk, hv))
            elif field == 8:
                result["payload"] = chunk
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
    return result


def _aes_decrypt(encrypt_key: str, payload: str) -> str:
    """Decrypt a Feishu-encrypted event payload (AES-256-CBC, PKCS7, IV=token[:16])."""
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise FeishuError("Feishu encryptKey requires the 'cryptography' package") from exc

    import base64

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = payload[:16].encode("utf-8")
    raw = base64.b64decode(payload[16:])
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    dec = cipher.decryptor().update(raw) + cipher.decryptor().finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(dec) + unpadder.finalize()).decode("utf-8")


class FeishuChannel(Channel):
    name = "feishu"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.webhook_url = config.get("webhookUrl")
        self.app_id = config.get("appId")
        self.app_secret = config.get("appSecret")
        self.encrypt_key = config.get("encryptKey")
        self.receive_chat_types = config.get("receiveChatTypes") or ["p2p", "group"]
        # baseUrl is overridable so tests can point at a fake Feishu API
        self.base_url = str(config.get("baseUrl") or FEISHU_BASE).rstrip("/")
        self.token_url = f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal"
        self.send_url = f"{self.base_url}/open-apis/im/v1/messages?receive_id_type=chat_id"
        self.ws_endpoint_url = f"{self.base_url}/callback/ws/endpoint"
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._ws = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ping_interval = 120.0

    # -- tokens ------------------------------------------------------------
    def _tenant_access_token(self) -> str:
        now = time.time()
        if self._token and self._token_expires_at > now + 60:
            return self._token
        if not self.app_id or not self.app_secret:
            raise FeishuError("appId and appSecret required to obtain tenant access token")
        resp = requests.post(
            self.token_url,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"tenant token error: {data}")
        self._token = data["tenant_access_token"]
        self._token_expires_at = now + float(data.get("expire", 7200)) - 60
        return self._token

    # -- send --------------------------------------------------------------
    async def send(self, conversation_id: str, text: str, kind: str = "notify") -> None:
        if self.webhook_url:
            await asyncio.to_thread(self._send_webhook, conversation_id, text)
        elif self.app_id:
            await asyncio.to_thread(self._send_im, conversation_id, text)
        else:
            raise FeishuError("no send path configured (webhookUrl or appId)")

    def _send_webhook(self, conversation_id: str, text: str) -> None:
        # Custom-bot webhooks ignore conversation_id (they always post to the chat
        # the bot was added to). Keep it in the text for visibility.
        payload = {
            "msg_type": "text",
            "content": {"text": text},
        }
        resp = requests.post(self.webhook_url, json=payload, timeout=15)
        data = resp.json()
        if data.get("code") not in (0, None):
            raise FeishuError(f"feishu webhook error: {data}")

    def _send_im(self, chat_id: str, text: str) -> None:
        token = self._tenant_access_token()
        resp = requests.post(
            f"{self.send_url}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"feishu im send error: {data}")

    # -- receive -----------------------------------------------------------
    async def start(self) -> None:
        if self.app_id:
            self._ws_task = asyncio.create_task(self._long_conn_loop(), name="feishu-longconn")
            self.log.info("feishu app-bot receive loop started")
        elif self.webhook_url:
            self.log.warning(
                "feishu configured with only a webhook (send-only): inbound messages are not available; "
                "use an app bot (appId/appSecret) for two-way interaction"
            )
        await self.start_ok()

    async def stop(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._ws_task = None
        await self.stop_ok()

    async def _long_conn_loop(self) -> None:
        import websockets

        backoff = 1.0
        while True:
            try:
                endpoint = await asyncio.to_thread(self._request_endpoint)
                self.log.info("feishu ws endpoint: %s", endpoint)
                async with websockets.connect(endpoint, open_timeout=15) as ws:
                    self._ws = ws
                    backoff = 1.0
                    ping_task = asyncio.create_task(self._ping_loop(ws), name="feishu-ping")
                    try:
                        async for raw in ws:
                            if isinstance(raw, bytes):
                                await self._handle_ws_frame(raw, ws)
                            else:
                                self.log.debug("non-binary feishu ws frame ignored")
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log.warning("feishu long-connection error: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _ping_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)
            try:
                await ws.send(encode_frame(method=0, headers=[("type", "ping")]))
            except Exception:  # noqa: BLE001
                return

    def _request_endpoint(self) -> str:
        """POST {base}/callback/ws/endpoint with AppID/AppSecret -> data.URL."""
        if not self.app_id or not self.app_secret:
            raise FeishuError("feishu: appId/appSecret 未配置（在 config 或 .env 里填）")
        resp = requests.post(
            self.ws_endpoint_url,
            json={"AppID": self.app_id, "AppSecret": self.app_secret},
            headers={"locale": "zh", "User-Agent": "dsh-im-bridge"},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"feishu ws endpoint error: {data}")
        url = (data.get("data") or {}).get("URL")
        if not url:
            raise FeishuError("feishu ws endpoint returned no URL")
        return url

    async def _handle_ws_frame(self, raw: bytes, ws) -> None:
        try:
            frame = decode_frame(raw)
        except ValueError as exc:  # noqa: BLE001
            self.log.warning("feishu ws bad frame: %s", exc)
            return
        method = frame["method"]  # 0=CONTROL, 1=DATA
        headers = dict(frame["headers"])
        mtype = headers.get("type")
        if method == 0:  # CONTROL (ping/pong) — nothing to reply per official SDK
            return
        if method != 1:  # DATA
            return
        if mtype == "event":
            payload = frame["payload"]
            if not payload:
                return
            try:
                event = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:  # noqa: BLE001
                self.log.warning("feishu event payload not JSON: %s", exc)
                return
            await self._handle_event(event)
            # ACK: send the same frame back with payload = {"code": 200}
            ack = encode_frame(
                seq=frame["seq"], log=frame["log"], service=frame["service"],
                method=method, headers=frame["headers"],
                payload=json.dumps({"code": 200}).encode("utf-8"),
            )
            try:
                await ws.send(ack)
            except Exception:  # noqa: BLE001
                pass
        elif mtype == "card":
            self.log.debug("feishu card callback ignored")
        else:
            self.log.debug("feishu ws data frame type %r ignored", mtype)

    async def _handle_event(self, event: dict) -> None:
        header = event.get("header") or {}
        event_type = header.get("event_type")
        if event_type != "im.message.receive_v1":
            return
        message = event.get("event", {}).get("message") or {}
        sender = event.get("event", {}).get("sender") or {}
        chat = message.get("chat") or {}
        chat_type = chat.get("chat_type") or message.get("chat_type")
        if chat_type not in self.receive_chat_types:
            return
        chat_id = chat.get("chat_id") or message.get("chat_id") or chat.get("id") or ""
        content = message.get("content") or "{}"
        try:
            content_obj = json.loads(content)
        except json.JSONDecodeError:
            content_obj = {"text": str(content)}
        text = content_obj.get("text") or content_obj.get("content") or ""
        # strip feishu <at> markup to plain text
        text = text.replace("@_user_1", "")
        if not text.strip():
            return
        sender_id = (sender.get("sender_id") or {}).get("open_id") or sender.get("id")
        self.deliver(
            InboundMessage(
                channel=self.name,
                conversation_id=chat_id,
                text=text.strip(),
                sender=sender_id,
                raw=event,
            )
        )
