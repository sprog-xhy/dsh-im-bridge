"""QQ official open-platform bot channel (q.qq.com / bot.q.qq.com).

This is the *official* QQ bot API — completely different from the OneBot11
protocol channel in :mod:`dsh_im_bridge.channels.qq`. It needs no third-party
protocol daemon (NapCat / Lagrange / ...): you create a bot app on the QQ 开放
平台 (q.qq.com), and the bridge itself:

* exchanges ``AppID + AppSecret`` for an ``access_token`` (cached, refreshed
  ~1 minute before expiry);
* connects to the official **WebSocket long connection**, identifies with the
  token, keeps a heartbeat alive, and receives **C2C (private) messages** via
  the ``C2C_MESSAGE_CREATE`` dispatch event;
* replies through the official send API (``POST /v2/users/{openid}/messages``).

Protocol details are aligned with the official wiki (bot.q.qq.com, api-v2) and
the official ``tencent-connect/botpy`` SDK; the gateway handshake, token
endpoint and send endpoint were verified live against the real service.

Conversation ids on this channel are the user's ``openid`` (from the event's
``author.user_openid``), so a conversation key looks like ``qq_official:<openid>``.

Config keys (under ``channels.qq_official``):

* ``appId`` (int) / ``appSecret`` (str) — required
* ``sandbox`` (bool, default false) — sandbox vs. production API root
* ``allowUsers`` (list of openid, optional) — only allow these private chats
* ``baseUrl`` (str, optional) — override the API root (testing)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Optional

import requests

from ..events import InboundMessage
from .base import Channel

log = logging.getLogger("dsh_im_bridge.channel.qq_official")

# -- official QQ bot open-platform endpoints -------------------------------
# Token endpoint is shared between sandbox and production (verified live on
# both api.bot.qq.com and bots.qq.com; botpy hard-codes the latter).
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
PROD_API_ROOT = "https://api.bot.qq.com"
SANDBOX_API_ROOT = "https://sandbox.api.bot.qq.com"
PROD_WS_URL = "wss://api.bot.qq.com/websocket"
SANDBOX_WS_URL = "wss://sandbox.api.bot.qq.com/websocket"

# Intent bits (api-v2). C2C private messages need GROUP_AND_C2C_EVENT.
INTENT_GROUP_AND_C2C = 1 << 25

# WebSocket gateway op codes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# C2C_MESSAGE_CREATE receive-side message_type values (the receive side uses
# `message_type`, which differs from the send-side `msg_type`).
MT_TEXT = 0
MT_CARD = 3
MT_PARALLEL = 101
MT_CHAT_HISTORY = 102
MT_QUOTE = 103


class QqOfficialError(RuntimeError):
    pass


class QqOfficialInvalidSession(QqOfficialError):
    """The gateway told us the session is invalid — re-identify from scratch."""


class QqOfficialChannel(Channel):
    name = "qq_official"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.app_id = str(config.get("appId") or "")
        self.app_secret = str(config.get("appSecret") or "")
        self.sandbox = bool(config.get("sandbox", False))
        self.allow_users = config.get("allowUsers")  # None = all
        self.base_url = str(config.get("baseUrl") or "").rstrip("/")

        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._ws = None
        self._ws_task: Optional[asyncio.Task] = None
        self._session_id: Optional[str] = None
        self._last_seq = 0
        self._next_msg_seq = 0
        # dedupe repeated pushes of the same message (same msg_id is re-delivered)
        self._recent_msg_ids: deque = deque(maxlen=200)
        # last received message id per openid, used as msg_id for passive reply
        self._last_msg_id: dict[str, str] = {}

    # -- endpoint helpers --------------------------------------------------
    def api_root(self) -> str:
        if self.base_url:
            return self.base_url
        return SANDBOX_API_ROOT if self.sandbox else PROD_API_ROOT

    def ws_url(self) -> str:
        if self.base_url:
            # For a custom baseUrl, the WS gateway is host/websocket
            return f"{self.base_url}/websocket".replace("https://", "wss://").replace("http://", "ws://")
        return SANDBOX_WS_URL if self.sandbox else PROD_WS_URL

    def _gateway_url(self, token: str) -> str:
        """Resolve the WSS gateway URL.

        The official flow fetches it from ``GET /gateway/bot`` (returns
        ``{url, shards, session_start_limit}``). We prefer that and fall back to
        the fixed default on any failure. A custom ``baseUrl`` (tests / proxies)
        skips discovery and uses the derived URL directly.
        """
        if self.base_url:
            return self.ws_url()
        try:
            resp = requests.get(
                f"{self.api_root()}/gateway/bot",
                headers={
                    "Authorization": f"QQBot {token}",
                    "X-Union-Appid": self.app_id,
                },
                timeout=10,
            )
            data = resp.json()
            url = data.get("url") or (data.get("data") or {}).get("url")
            if url:
                self.log.debug("qq_official gateway url from discovery: %s", url)
                return str(url)
        except Exception:  # noqa: BLE001
            pass
        return self.ws_url()

    # -- tokens ------------------------------------------------------------
    def _access_token(self) -> str:
        """Return a cached, still-valid access_token (fetch + cache otherwise)."""
        now = time.time()
        if self._token and self._token_expires_at > now + 60:
            return self._token
        if not self.app_id or not self.app_secret:
            raise QqOfficialError("qq_official: appId/appSecret 未配置（在 config 或 .env 里填）")
        resp = requests.post(
            TOKEN_URL,
            json={"appId": self.app_id, "clientSecret": self.app_secret},
            timeout=15,
        )
        data = resp.json()
        token = (
            data.get("access_token")
            or (data.get("data") or {}).get("access_token")
        )
        if not token:
            raise QqOfficialError(f"qq_official: access_token 获取失败: {data}")
        self._token = token
        expires_in = int(
            data.get("expires_in")
            or (data.get("data") or {}).get("expires_in")
            or 7200
        )
        self._token_expires_at = now + expires_in - 60
        return self._token

    # -- send --------------------------------------------------------------
    async def send(self, conversation_id: str, text: str, kind: str = "notify") -> None:
        await asyncio.to_thread(self._send_c2c, conversation_id, text)

    def _send_c2c(self, openid: str, text: str) -> None:
        token = self._access_token()
        url = f"{self.api_root()}/v2/users/{openid}/messages"
        self._next_msg_seq += 1
        payload = {
            "content": text,
            "msg_type": 0,
            "msg_seq": self._next_msg_seq,  # 与 msg_id 联合去重（官方默认从 1 递增）
        }
        msg_id = self._last_msg_id.get(openid)
        if msg_id:
            payload["msg_id"] = msg_id  # passive reply window (helps dedupe/delivery)
        resp = requests.post(
            url,
            headers={
                "Authorization": f"QQBot {token}",
                "X-Union-Appid": self.app_id,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        data = resp.json() if resp.content else {}
        # Success: HTTP 2xx and no err_code (success returns {"id": ...}).
        err_code = data.get("err_code", 0)
        if resp.status_code >= 300 or err_code:
            raise QqOfficialError(
                f"qq_official: 发送 C2C 消息失败 (HTTP {resp.status_code}): {data}"
            )

    # -- receive (WebSocket long connection) -------------------------------
    async def start(self) -> None:
        if not self.app_id or not self.app_secret:
            self.log.warning(
                "qq_official: appId/appSecret 未配置 — 无法连接官方长连接，通道将不接收消息"
            )
        else:
            self._ws_task = asyncio.create_task(self._ws_loop(), name="qq-official-ws")
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

    async def _ws_loop(self) -> None:
        import websockets

        backoff = 1.0
        while True:
            try:
                token = await asyncio.to_thread(self._access_token)
                gateway = await asyncio.to_thread(self._gateway_url, token)
                async with websockets.connect(gateway, open_timeout=15) as ws:
                    self._ws = ws
                    backoff = 1.0
                    heartbeat_interval = await self._handshake(ws, token)
                    self.log.info(
                        "qq_official WS connected (%s) via %s, heartbeat %.0f ms",
                        "sandbox" if self.sandbox else "prod",
                        gateway,
                        heartbeat_interval,
                    )
                    await self._run(ws, heartbeat_interval, token)
            except QqOfficialInvalidSession:
                # gateway invalidated our session: re-identify from scratch
                self._session_id = None
                self._last_seq = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log.warning("qq_official WS error: %s", exc)
            self._ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _handshake(self, ws, token: str) -> float:
        """Complete the gateway handshake and return the heartbeat interval (ms).

        Flow (api-v2): server sends HELLO(op 10) with d.heartbeat_interval;
        if we hold a session_id we try RESUME(op 6) to catch up on missed
        events; otherwise we send IDENTIFY(op 2) and await READY(op 0).
        """
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        if hello.get("op") != OP_HELLO:
            raise QqOfficialError(f"qq_official: 期望 HELLO，收到 {hello.get('op')}: {hello}")
        interval = float((hello.get("d") or {}).get("heartbeat_interval", 41250))
        if interval <= 0:
            interval = 41250.0

        if self._session_id:
            await ws.send(
                json.dumps(
                    {
                        "op": OP_RESUME,
                        "d": {
                            "token": f"QQBot {token}",
                            "session_id": self._session_id,
                            "seq": self._last_seq,
                        },
                    }
                )
            )
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if resp.get("op") == OP_DISPATCH and resp.get("t") == "RESUMED":
                return interval
            if resp.get("op") == OP_INVALID_SESSION:
                self._session_id = None
                self._last_seq = 0
            # otherwise fall through to a fresh IDENTIFY

        await ws.send(
            json.dumps(
                {
                    "op": OP_IDENTIFY,
                    "d": {
                        "token": f"QQBot {token}",
                        "intents": INTENT_GROUP_AND_C2C,
                        "shard": [0, 1],
                        "properties": {
                            "$os": "linux",
                            "$browser": "dsh-im-bridge",
                            "$device": "dsh-im-bridge",
                        },
                    },
                }
            )
        )
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        if ready.get("op") != OP_DISPATCH or ready.get("t") != "READY":
            raise QqOfficialError(f"qq_official: 期望 READY，收到: {ready}")
        self._session_id = (ready.get("d") or {}).get("session_id")
        return interval

    async def _run(self, ws, heartbeat_interval: float, token: str) -> None:
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws, heartbeat_interval))
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                op = frame.get("op")
                if op == OP_DISPATCH:
                    self._last_seq = int(frame.get("s") or self._last_seq or 0)
                    t = frame.get("t")
                    if t == "READY":
                        self._session_id = (frame.get("d") or {}).get("session_id")
                    elif t == "RESUMED":
                        pass
                    else:
                        await self._on_dispatch(frame)
                elif op == OP_HEARTBEAT_ACK:
                    pass
                elif op == OP_HELLO:
                    pass
                elif op == OP_RECONNECT:
                    # gateway asks us to reconnect — resume on a fresh socket
                    self.log.info("qq_official: server requested reconnect")
                    return
                elif op == OP_INVALID_SESSION:
                    raise QqOfficialInvalidSession()
                else:
                    self.log.debug("qq_official ws frame op=%s ignored", op)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _heartbeat_loop(self, ws, interval_ms: float) -> None:
        interval_s = max(interval_ms / 1000.0, 1.0)
        while True:
            await asyncio.sleep(interval_s)
            try:
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._last_seq}))
            except Exception:  # noqa: BLE001
                return

    async def _on_dispatch(self, frame: dict) -> None:
        t = frame.get("t")
        if t == "C2C_MESSAGE_CREATE":
            await self._on_c2c_message(frame.get("d") or {})
        else:
            self.log.debug("qq_official dispatch %s ignored", t)

    async def _on_c2c_message(self, d: dict) -> None:
        author = d.get("author") or {}
        openid = (
            author.get("user_openid")
            or d.get("user_openid")
            or d.get("openid")
            or ""
        )
        if not openid:
            self.log.warning("qq_official C2C event missing user openid: %s", d)
            return
        if self.allow_users and openid not in {str(u) for u in self.allow_users}:
            return
        # dedupe: the same msg_id is re-delivered; key on message id
        msg_id = str(d.get("id") or d.get("msg_id") or "")
        if msg_id and msg_id in self._recent_msg_ids:
            return
        if msg_id:
            self._recent_msg_ids.append(msg_id)
            self._last_msg_id[openid] = msg_id
        text = _extract_c2c_text(d)
        if not text.strip():
            return
        self.deliver(
            InboundMessage(
                channel=self.name,
                conversation_id=openid,
                text=text.strip(),
                sender=openid,
                raw=d,
            )
        )


def _extract_c2c_text(d: dict) -> str:
    """Extract plain text from a C2C_MESSAGE_CREATE payload.

    The receive-side type field is ``message_type``: 0 = plain text, 3 =
    structured card, 101 = parallel messages, 102 = chat history, 103 = quote.
    Non-text types are rendered as short placeholders.
    """
    mtype = d.get("message_type", d.get("msg_type", d.get("msgType")))
    content = d.get("content") or d.get("content_text") or ""
    if mtype in (None, MT_TEXT, "0", ""):
        return str(content)
    if mtype in (MT_CARD, "3"):
        return "[卡片消息]"
    if mtype in (MT_PARALLEL, "101"):
        return "[并行消息]"
    if mtype in (MT_CHAT_HISTORY, "102"):
        return "[聊天记录]"
    if mtype in (MT_QUOTE, "103"):
        return "[引用消息]"
    return f"[消息类型{mtype}]"
