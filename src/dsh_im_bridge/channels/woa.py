"""WPS 协作 (WOA / WPS365) channel.

Implements the WPS open-platform bot protocol (verified against the community
``wps-xiezuo-sdk`` and open-platform docs):

* **Send text**: ``POST {apiUrl}/v7/messages/create`` — OAuth access token
  (``/oauth2/token``, client_credentials) + KSO-1 HMAC-SHA256 signature.
  ``receiver.type`` is ``user`` for p2p (receiver_id = the other user) and
  ``chat`` for group (receiver_id = the chat id).

* **Receive**: HTTP webhook.  ``GET ?challenge=...`` answers the URL
  verification; ``POST`` with ``topic=kso.app_chat.message`` is verified with a
  base64url HMAC-SHA256 signature (``appId:topic:nonce:time:encryptedData``,
  300s timestamp tolerance), decrypted with AES-256-CBC (key = the 32-char hex
  ``md5(secretKey)`` used as UTF-8 bytes, IV = ``nonce`` as UTF-8), then parsed
  into an InboundMessage.

Credentials/config: ``appId``, ``secretKey``, ``encryptKey`` (when the event is
encrypted), ``apiUrl`` (default ``https://openapi.wps.cn``; the JP platform's
base differs — user to confirm), ``webhookHost/Port/Path`` (must be reachable by
WPS servers — see deployment note in INTEGRATION.md).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

from ..events import InboundMessage
from ..httpx import MinimalHttpServer
from .base import Channel

log = logging.getLogger("dsh_im_bridge.channel.woa")

DEFAULT_API = "https://openapi.wps.cn"
MESSAGE_TOPIC = "kso.app_chat.message"


class WoaError(RuntimeError):
    pass


# -- crypto ----------------------------------------------------------------

def _md5_hex(data: str) -> str:
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _hmac_sha256(key: str, data: str, *, hexdigest: bool = True) -> str | bytes:
    mac = hmac.new(key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest() if hexdigest else mac.digest()


def _rfc1123_date() -> str:
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())


def _kso1_signature(app_id: str, secret_key: str, method: str, path: str,
                    content_type: str, date: str, body: str) -> str:
    """KSO-1 Authorization header value: ``KSO-1 {appId}:{hmac}``."""
    sha256 = _sha256_hex(body) if body else ""
    sign_content = f"KSO-1{method}{path}{content_type}{date}{sha256}"
    signature = _hmac_sha256(secret_key, sign_content)
    return f"KSO-1 {app_id}:{signature}"


def _event_signature(app_id: str, secret_key: str, topic: str, nonce: str,
                     ts: int, encrypted_data: str) -> str:
    """base64url HMAC-SHA256 over ``appId:topic:nonce:time:encryptedData``."""
    content = f"{app_id}:{topic}:{nonce}:{ts}:{encrypted_data}"
    raw = _hmac_sha256(secret_key, content, hexdigest=False)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def verify_event_signature(app_id: str, secret_key: str, topic: str, nonce: str,
                           ts: int, encrypted_data: str, received: str) -> bool:
    if abs(int(time.time()) - ts) > 300:
        log.warning("woa event timestamp too old/new: diff=%s", abs(int(time.time()) - ts))
        return False
    expected = _event_signature(app_id, secret_key, topic, nonce, ts, encrypted_data)
    return hmac.compare_digest(expected, received)


def decrypt_event_data(secret_key: str, encrypted_data: str, nonce: str) -> str:
    """AES-256-CBC decrypt; key = md5(secretKey) hex as UTF-8 bytes, IV = nonce UTF-8."""
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - optional dep
        raise WoaError("WPS 事件解密需要可选依赖 'cryptography' (pip install .[feishu] 或 [woa])") from exc

    cipher_hex = _md5_hex(secret_key)          # 32 hex chars
    key = cipher_hex.encode("utf-8")           # 32 bytes -> AES-256
    iv = nonce.encode("utf-8")
    try:
        raw = base64.b64decode(encrypted_data)
    except Exception as exc:  # noqa: BLE001
        raise WoaError(f"woa: bad base64 encrypted_data: {exc}") from exc
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    dec = cipher.decryptor().update(raw) + cipher.decryptor().finalize()
    try:
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(dec) + unpadder.finalize()).decode("utf-8")
    except Exception:  # noqa: BLE001
        # PKCS7 validation can fail if the sample is plaintext; fall back raw
        return dec.decode("utf-8", "replace")


# -- channel ----------------------------------------------------------------

class WoaChannel(Channel):
    """WPS 协作 (WOA) channel: send via v7/messages/create, receive via webhook."""

    name = "woa"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.app_id = config.get("appId") or ""
        self.secret_key = config.get("secretKey") or ""
        self.encrypt_key = config.get("encryptKey") or ""  # not directly used; secretKey decrypts
        self.api_url = str(config.get("apiUrl") or DEFAULT_API).rstrip("/")
        self.webhook_host = config.get("webhookHost", "0.0.0.0")
        self.webhook_port = int(config.get("webhookPort", 8766))
        self.webhook_path = str(config.get("webhookPath", "/webhook"))
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._server: Optional[MinimalHttpServer] = None
        # message-id dedup (WPS may retry webhooks)
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._dedup_max = 2000
        self._dedup_ttl = 3600.0

    # -- tokens ------------------------------------------------------------
    def _access_token(self) -> str:
        now = time.time()
        if self._token and self._token_expires_at > now + 60:
            return self._token
        resp = requests.post(
            f"{self.api_url}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.app_id,
                "client_secret": self.secret_key,
            },
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise WoaError(f"woa: oauth token error: {data}")
        self._token = token
        self._token_expires_at = now + float(data.get("expires_in", 7200)) - 300
        return token

    # -- send --------------------------------------------------------------
    async def send(self, conversation_id: str, text: str, kind: str = "notify") -> None:
        ctype, _, cid = conversation_id.partition(":")
        if ctype == "group":
            receiver_type = "chat"
        elif ctype == "p2p":
            receiver_type = "user"
        else:
            # bare id: guess chat (works for both in WPS as "chat" is group only,
            # so require an explicit prefix for p2p)
            receiver_type = "chat"
        await asyncio.to_thread(self._send_text, cid, receiver_type, text)

    def _send_text(self, receiver_id: str, receiver_type: str, text: str) -> None:
        if not receiver_id:
            raise WoaError("woa: empty receiver id")
        path = "/v7/messages/create"
        body = {
            "type": "text",
            "receiver": {"receiver_id": receiver_id, "type": receiver_type},
            "content": {"text": {"content": text, "type": "markdown"}},
        }
        body_string = json.dumps(body, ensure_ascii=False)
        body_bytes = body_string.encode("utf-8")  # deterministic across platforms
        token = self._access_token()
        kso_date = _rfc1123_date()
        auth = _kso1_signature(self.app_id, self.secret_key, "POST", path,
                               "application/json", kso_date, body_string)
        resp = requests.post(
            f"{self.api_url}{path}",
            data=body_bytes,
            headers={
                "content-type": "application/json; charset=utf-8",
                "X-Kso-Date": kso_date,
                "X-Kso-Authorization": auth,
                "Authorization": f"Bearer {token}",
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise WoaError(f"woa: send failed code={data.get('code')} msg={data.get('msg')}")

    # -- receive -----------------------------------------------------------
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._server = MinimalHttpServer(self.webhook_host, self.webhook_port, loop, self._route)
        await asyncio.to_thread(self._server.start)
        self.log.info("woa webhook listening on %s:%d%s (set this URL in the WPS open platform)",
                      self.webhook_host, self._server.bound_port, self.webhook_path)
        await self.start_ok()

    async def stop(self) -> None:
        if self._server is not None:
            await asyncio.to_thread(self._server.stop)
            self._server = None
        await self.stop_ok()

    async def _route(self, path: str, method: str, payload: dict,
                     headers: dict | None = None, raw_body: str = "", **kwargs) -> dict:
        route_path = urlparse(path).path
        if route_path != self.webhook_path:
            return {"code": -1, "msg": "not found"}
        if method == "GET":
            qs = parse_qs(urlparse(path).query)
            challenge = (qs.get("challenge") or [""])[0]
            if challenge:
                return {"challenge": challenge}
            return {"code": 0, "msg": "ok"}
        if method != "POST":
            return {"code": -1, "msg": "method not allowed"}
        try:
            self._handle_message_event(payload, raw_body, headers or {})
        except Exception as exc:  # noqa: BLE001
            log.exception("woa webhook processing failed: %s", exc)
        return {"code": 0, "msg": "success"}

    def _handle_message_event(self, body: dict, raw_body: str, headers: dict) -> None:
        topic = body.get("topic")
        if topic != MESSAGE_TOPIC:
            self.log.debug("ignoring non-message topic %r", topic)
            return
        # verify signature (if present)
        signature = body.get("signature")
        nonce = str(body.get("nonce") or "")
        try:
            ts = int(body.get("time") or 0)
        except (TypeError, ValueError):
            ts = 0
        encrypted = body.get("encrypted_data") or ""
        if signature and not verify_event_signature(self.app_id, self.secret_key,
                                                    topic, nonce, ts, encrypted, signature):
            self.log.warning("woa webhook signature verification failed; dropping")
            return
        # decrypt
        if encrypted:
            decrypted = decrypt_event_data(self.secret_key, encrypted, nonce)
            try:
                event_data = json.loads(decrypted)
            except json.JSONDecodeError:
                self.log.warning("woa: decrypted payload is not JSON; treating as text")
                event_data = {"message": {"type": "text", "content": {"text": {"content": decrypted}}},
                              "chat": {}, "sender": {}}
        else:
            event_data = body.get("data") or body
        # dedup by message id (WPS may retry a webhook if the response is slow)
        msg_id = (event_data.get("message") or {}).get("id") or ""
        if msg_id and self._is_seen(msg_id):
            self.log.debug("woa: duplicate message %s skipped", msg_id)
            return
        message = self._parse_message(event_data)
        if message is None:
            return
        self.deliver(message)

    # -- dedup -------------------------------------------------------------
    def _is_seen(self, message_id: str) -> bool:
        now = time.time()
        # drop stale entries
        while self._seen and next(iter(self._seen.values())) < now - self._dedup_ttl:
            self._seen.popitem(last=False)
        if message_id in self._seen:
            return True
        self._seen[message_id] = now
        if len(self._seen) > self._dedup_max:
            self._seen.popitem(last=False)
        return False

    def _parse_message(self, event: dict) -> Optional[InboundMessage]:
        msg = event.get("message") or {}
        chat = event.get("chat") or {}
        sender = event.get("sender") or {}
        chat_type = chat.get("type")  # "p2p" | "group"
        chat_id = chat.get("id") or ""
        sender_id = sender.get("id") or ""
        message_id = msg.get("id") or ""

        if chat_type == "group":
            conversation_id = f"group:{chat_id}"
            # require @bot in groups (match SDK default requireMention=true)
            mentions = msg.get("mentions") or []
            if not any(m.get("identity", {}).get("type") == "app" for m in mentions):
                self.log.debug("woa group message without @bot; ignored")
                return None
        else:
            # p2p: conversation keyed by the other party (sender)
            conversation_id = f"p2p:{sender_id or chat_id}"

        text = self._extract_text(msg)
        if not text.strip():
            self.log.debug("woa message with no text; ignored")
            return None
        return InboundMessage(
            channel=self.name,
            conversation_id=conversation_id,
            text=text.strip(),
            sender=sender_id or None,
            raw=event,
        )

    @staticmethod
    def _extract_text(msg: dict) -> str:
        mtype = msg.get("type")
        content = msg.get("content") or {}
        if mtype == "text":
            text = (content.get("text") or {}).get("content") or ""
            # strip <at> tags to plain text
            import re

            text = re.sub(r"<at[^>]*>.*?</at>", "", text)
            return text
        if mtype == "file":
            file = content.get("file") or {}
            if file.get("type") == "local":
                return f"[文件: {(file.get('local') or {}).get('name', '未知')}]"
            if file.get("type") == "cloud":
                return f"[云文档: {(file.get('cloud') or {}).get('link_url', '')}]"
            return "[文件]"
        if mtype in ("image", "audio", "video", "sticker"):
            return f"[{mtype}]"
        if mtype == "rich_text":
            parts = []
            for el in (content.get("rich_text") or {}).get("elements") or []:
                for item in el.get("elements") or []:
                    if item.get("text_content", {}).get("content"):
                        parts.append(item["text_content"]["content"])
                    elif item.get("style_text_content", {}).get("text"):
                        parts.append(item["style_text_content"]["text"])
            return " ".join(parts)
        return f"[不支持的消息类型: {mtype}]"
