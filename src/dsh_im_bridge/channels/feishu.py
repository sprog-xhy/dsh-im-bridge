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
        self.long_conn_url = f"{self.base_url}/open-apis/event/v1/long_connection"
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._ws = None
        self._ws_task: Optional[asyncio.Task] = None

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
                self.log.info("feishu long-connection endpoint: %s", endpoint)
                async with websockets.connect(endpoint, open_timeout=15) as ws:
                    self._ws = ws
                    backoff = 1.0
                    # first frame: challenge handshake
                    raw = await ws.recv()
                    await self._handle_frame(raw, ws)
                    async for raw in ws:
                        await self._handle_frame(raw, ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log.warning("feishu long-connection error: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def _request_endpoint(self) -> str:
        token = self._tenant_access_token()
        resp = requests.post(
            self.long_conn_url,
            headers={"Authorization": f"Bearer {token}"},
            json={},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"long_connection error: {data}")
        endpoint = data.get("data", {}).get("endpoint")
        if not endpoint:
            raise FeishuError("long_connection returned no endpoint")
        return endpoint

    async def _handle_frame(self, raw, ws) -> None:
        frame = json.loads(raw)
        frame_type = frame.get("type")
        if frame_type == "Challenge":
            # reply with challenge + ack (and, if an encryptKey is configured,
            # send a fake challenge to finalize the handshake)
            reply = {"type": "ChallengeResponse", "challenge": frame.get("challenge")}
            if self.encrypt_key:
                reply["fake_challenge"] = frame.get("challenge")
            await ws.send(json.dumps(reply))
            return
        if frame_type == "Event":
            await self._handle_event(frame.get("event") or frame.get("data") or {})
        elif frame_type == "Pong":
            pass
        else:
            self.log.debug("feishu frame type %r ignored", frame_type)

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
