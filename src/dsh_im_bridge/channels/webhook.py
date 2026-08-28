"""Webhook channel: a local HTTP endpoint any tool/script can POST to.

This is the "everything else" adapter: any external tool that can issue an HTTP
POST can inject a user message into the bound dsh session, without needing a
native IM integration. Useful for WOA or other collaboration tools until a
dedicated adapter is written.

Endpoints (JSON):

* ``POST /message``  body ``{"text": "...", "sender": "?", "conversation_id": "?"}``
  -> delivers an InboundMessage (conversation_id defaults to ``webhook``).
* ``GET /health``   -> ``{"ok": true}``

The conversation id can be used to have several callers bind to different dsh
sessions.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from ..events import InboundMessage
from ..httpx import MinimalHttpServer
from .base import Channel


class WebhookChannel(Channel):
    name = "webhook"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.host = config.get("host", "127.0.0.1")
        self.port = int(config.get("port", 8765))
        self._server: Optional[MinimalHttpServer] = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._server = MinimalHttpServer(self.host, self.port, loop, self._route)
        await asyncio.to_thread(self._server.start)
        self.log.info("webhook channel listening on %s:%d", self.host, self._server.bound_port)
        await self.start_ok()

    async def stop(self) -> None:
        if self._server is not None:
            await asyncio.to_thread(self._server.stop)
            self._server = None
        await self.stop_ok()

    async def send(self, conversation_id: str, text: str, kind: str = "notify") -> None:
        # A webhook endpoint has no push channel back; outbound is only visible
        # to whoever polls. No-op by design.
        self.log.debug("webhook outbound (no push target): %s", text[:80])

    async def _route(self, path: str, method: str, payload: dict, **kwargs) -> dict:
        if path == "/health" and method == "GET":
            return {"ok": True}
        if path == "/message" and method == "POST":
            text = str(payload.get("text") or "")
            if not text.strip():
                return {"error": "empty text", "accepted": False}
            conversation_id = str(payload.get("conversation_id") or "webhook")
            self.deliver(
                InboundMessage(
                    channel=self.name,
                    conversation_id=conversation_id,
                    text=text.strip(),
                    sender=payload.get("sender"),
                    raw=payload,
                )
            )
            return {"accepted": True}
        return {"error": "not found", "accepted": False}
