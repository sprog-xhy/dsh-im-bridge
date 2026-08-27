"""QQ channel via the OneBot11 protocol (WebSocket client mode).

OneBot11 is implemented by NapCat, Lagrange.OneBot, LLOneBot, go-cqhttp and
friends. The bridge connects as a *client* to the bot's WebSocket server
(reverse-WS / ``ws`` config in NapCat), receives ``message`` events and sends
``send_group_msg`` / ``send_private_msg`` requests over the same socket.

Conversation ids on this channel use a ``type:id`` prefix:

* ``group:<group_id>``  -> group messages / sends
* ``private:<user_id>`` -> private messages / sends

Requires the optional ``websockets`` package (part of the default install).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

import websockets

from ..events import InboundMessage
from .base import Channel

log = logging.getLogger("dsh_im_bridge.channel.qq")

ONE_BOT_VERSION = "11.15.0"
SUPPORTED_IMPL = {"NapCat", "Lagrange.OneBot", "LLOneBot", "go-cqhttp"}


class QQOneBotError(RuntimeError):
    pass


class QQOneBotChannel(Channel):
    name = "qq"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.ws_url = config.get("wsUrl", "ws://127.0.0.1:3001")
        self.access_token = config.get("accessToken")
        self.allow_groups = config.get("allowGroups")  # None = all
        self.allow_users = config.get("allowUsers")    # None = all
        self.self_id = str(config.get("selfId") or "")
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._next_echo = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="qq-onebot")
        await self.start_ok()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self.stop_ok()

    async def _loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self.ws_url, open_timeout=15) as ws:
                    self._ws = ws
                    self.log.info("qq OneBot connected to %s", self.ws_url)
                    backoff = 1.0
                    async for raw in ws:
                        try:
                            await self._handle(raw)
                        except Exception as exc:  # noqa: BLE001
                            self.log.exception("qq frame handler failed: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.log.warning("qq OneBot connection error: %s", exc)
            self._ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _handle(self, raw) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        data = json.loads(raw)
        echo = data.get("echo")
        if echo is not None and echo in self._pending:
            fut = self._pending.pop(echo)
            if not fut.done():
                fut.set_result(data)
            return
        post_type = data.get("post_type")
        if post_type == "message":
            await self._on_message(data)
        elif post_type == "meta_event":
            # lifecycle / heartbeat
            self.log.debug("qq meta event: %s", data.get("meta_event_type"))
        elif post_type in ("notice", "request"):
            self.log.debug("qq %s event ignored", post_type)

    async def _on_message(self, data: dict) -> None:
        if data.get("message_type") == "group":
            group_id = str(data.get("group_id", ""))
            if self.allow_groups and group_id not in {str(g) for g in self.allow_groups}:
                return
            conversation_id = f"group:{group_id}"
        elif data.get("message_type") == "private":
            user_id = str(data.get("user_id", ""))
            if self.allow_users and user_id not in {str(u) for u in self.allow_users}:
                return
            conversation_id = f"private:{user_id}"
        else:
            return
        if str(data.get("user_id", "")) == self.self_id:
            return  # echo of our own message
        text = _extract_text(data.get("message"))
        if not text.strip():
            return
        self.deliver(
            InboundMessage(
                channel=self.name,
                conversation_id=conversation_id,
                text=text.strip(),
                sender=str(data.get("user_id", "")),
                raw=data,
            )
        )

    # -- send --------------------------------------------------------------
    async def send(self, conversation_id: str, text: str, kind: str = "notify") -> None:
        ctype, _, cid = conversation_id.partition(":")
        if ctype == "group":
            action, params = "send_group_msg", {"group_id": int(cid), "message": text}
        elif ctype == "private":
            action, params = "send_private_msg", {"user_id": int(cid), "message": text}
        else:
            raise QQOneBotError(f"unknown conversation id {conversation_id!r}")
        resp = await self._request(action, params)
        if resp.get("status") != "ok":
            raise QQOneBotError(f"qq {action} failed: {resp.get('retcode')} {resp.get('wording')}")

    async def _request(self, action: str, params: dict) -> dict:
        if self._ws is None:
            raise QQOneBotError("not connected to OneBot server")
        self._next_echo += 1
        echo = f"dsh-bridge-{self._next_echo}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        payload = {
            "action": action,
            "params": params,
            "echo": echo,
        }
        if self.access_token:
            payload["access_token"] = self.access_token
        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError as exc:
            self._pending.pop(echo, None)
            raise QQOneBotError(f"timeout waiting for {action}") from exc


def _extract_text(message) -> str:
    """Extract plain text from an OneBot11 message segment array."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for seg in message:
            if not isinstance(seg, dict):
                parts.append(str(seg))
                continue
            seg_type = seg.get("type")
            data = seg.get("data") or {}
            if seg_type == "text":
                parts.append(data.get("text", ""))
            elif seg_type == "at":
                parts.append(f"@{data.get('qq', data.get('name', '?'))}")
            elif seg_type == "face":
                parts.append(f"[表情{data.get('id', '')}]")
            elif seg_type == "image":
                parts.append("[图片]")
            elif seg_type == "reply":
                pass
            else:
                parts.append(f"[{seg_type}]")
        return "".join(parts)
    return str(message)
