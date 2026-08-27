"""Local management HTTP API for the bridge.

Exposed on loopback (127.0.0.1) by default. Any external tool / script / WOA
integration can drive the bridge from here:

* ``GET  /health``          -> ``{"ok": true}``
* ``GET  /status``          -> dsh + hub status, bindings, pending items
* ``POST /prompt``          -> send text straight into a dsh session
      body ``{"sessionId": "...", "text": "..."}``
* ``POST /message``         -> inject an inbound IM message via a channel
      body ``{"channel": "webhook", "conversation_id": "x", "text": "..."}``
* ``POST /answer``          -> answer the current pending question for a channel
      body ``{"channel": "...", "conversation_id": "...", "text": "1:选项A"}``
* ``POST /approval``        -> resolve the current pending approval
      body ``{"channel": "...", "conversation_id": "...", "outcome": "allow|reject"}``
* ``POST /bind``            -> bind a conversation key to a session
      body ``{"channel": "...", "conversation_id": "...", "session_id": "..."}``
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .dsh_client import DshError
from .events import InboundMessage
from .httpx import MinimalHttpServer
from .hub import BridgeHub, parse_answer_arg

log = logging.getLogger("dsh_im_bridge.server")


class BridgeServer:
    def __init__(self, hub: BridgeHub, host: str = "127.0.0.1", port: int = 8764):
        self.hub = hub
        self.host = host
        self.port = port
        self._server: Optional[MinimalHttpServer] = None

    @property
    def bound_port(self) -> int:
        return self._server.bound_port if self._server else self.port

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._server = MinimalHttpServer(self.host, self.port, loop, self._route)
        await asyncio.to_thread(self._server.start)
        log.info("bridge API listening on %s:%d", self.host, self.bound_port)

    async def stop(self) -> None:
        if self._server is not None:
            await asyncio.to_thread(self._server.stop)
            self._server = None

    async def _route(self, path: str, method: str, payload: dict) -> dict:
        from urllib.parse import urlparse

        route_path = urlparse(path).path  # strip query string for matching
        try:
            if route_path == "/health" and method == "GET":
                return {"ok": True}
            if route_path == "/status" and method == "GET":
                return await self._status()
            if route_path == "/attachment" and method == "GET":
                return await self._attachment(path)
            if route_path == "/prompt" and method == "POST":
                return await self._prompt(payload)
            if route_path == "/message" and method == "POST":
                return await self._message(payload)
            if route_path == "/answer" and method == "POST":
                return await self._answer(payload)
            if route_path == "/approval" and method == "POST":
                return await self._approval(payload)
            if route_path == "/bind" and method == "POST":
                return await self._bind(payload)
            return {"error": f"unknown route {method} {path}", "accepted": False}
        except DshError as exc:
            return {"accepted": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            log.exception("route error: %s", exc)
            return {"accepted": False, "error": str(exc)}

    async def _status(self) -> dict:
        desc = None
        try:
            desc = self.hub.client.describe()
        except DshError:
            pass
        def _pending_view(rid: str, e: dict) -> dict:
            view = {"rpcId": rid, "kind": e["kind"], "sessionId": e["session_id"]}
            if e.get("kind") == "question":
                qs = e.get("questions") or ()
                view["question"] = qs[0].question if qs else None
                view["questionCount"] = len(qs)
            return view

        return {
            "ok": True,
            "dsh": desc,
            "channels": list(self.hub.channels),
            "bindings": [b.to_dict() for b in self.hub.bindings.values()],
            "pending": [_pending_view(rid, e) for rid, e in self.hub.pending.items()],
        }

    async def _attachment(self, path: str) -> dict:
        """GET /attachment?sessionId=...&attachmentId=... -> image payload.

        Returns the attachment metadata plus the image bytes base64-encoded, so
        any channel adapter / external tool can fetch and forward the image
        (Feishu/QQ upload is a per-channel step once credentials exist).
        """
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(path).query)
        session_id = (qs.get("sessionId") or [""])[0]
        attachment_id = (qs.get("attachmentId") or [""])[0]
        if not session_id or not attachment_id:
            return {"ok": False, "error": "sessionId and attachmentId query params required"}
        value = self.hub.client.attachment(session_id, attachment_id)
        attachment = value.get("attachment") or {}
        data = value.get("data") or ""
        return {
            "ok": True,
            "sessionId": session_id,
            "attachment": {
                "attachmentId": attachment.get("attachmentId", attachment_id),
                "mediaType": attachment.get("mediaType", "image/png"),
                "bytes": attachment.get("bytes"),
                "width": attachment.get("width"),
                "height": attachment.get("height"),
                "name": attachment.get("name", ""),
                "dataBase64": data,
            },
        }

    async def _prompt(self, payload: dict) -> dict:
        session_id = str(payload.get("sessionId") or "")
        text = str(payload.get("text") or "")
        if not session_id or not text.strip():
            return {"accepted": False, "error": "sessionId and text required"}
        self.hub.client.prompt(session_id, text.strip(), mode=str(payload.get("mode", "queue")))
        return {"accepted": True, "sessionId": session_id}

    async def _message(self, payload: dict) -> dict:
        channel_name = str(payload.get("channel") or "webhook")
        conversation_id = str(payload.get("conversation_id") or "default")
        text = str(payload.get("text") or "")
        if not text.strip():
            return {"accepted": False, "error": "text required"}
        self.hub.enqueue_inbound(
            InboundMessage(
                channel=channel_name,
                conversation_id=conversation_id,
                text=text.strip(),
                sender=payload.get("sender"),
                raw=payload,
            )
        )
        return {"accepted": True}

    async def _answer(self, payload: dict) -> dict:
        key = f"{payload.get('channel', 'webhook')}:{payload.get('conversation_id', 'default')}"
        pending = self.hub._first_pending(key, "question")
        if pending is None:
            return {"accepted": False, "error": "no pending question for this conversation"}
        text = str(payload.get("text") or "")
        answers = parse_answer_arg(text, questions=pending.get("questions"))
        if answers is None:
            return {"accepted": False, "error": "answer format: 1:选项A,2:自定义文本"}
        ok = self.hub.client.answer_question(pending["rpc_id"], pending["session_id"], answers)
        if ok:
            self.hub.pending.pop(pending["rpc_id"], None)
        return {"accepted": ok}

    async def _approval(self, payload: dict) -> dict:
        key = f"{payload.get('channel', 'webhook')}:{payload.get('conversation_id', 'default')}"
        pending = self.hub._first_pending(key, "approval")
        if pending is None:
            return {"accepted": False, "error": "no pending approval for this conversation"}
        outcome = str(payload.get("outcome") or "reject").lower()
        wire = "allowed-once" if outcome.startswith("allow") else "rejected"
        ok = self.hub.client.resolve_approval(
            pending["rpc_id"], pending["session_id"], pending["approval_id"], wire
        )
        if ok:
            self.hub.pending.pop(pending["rpc_id"], None)
        return {"accepted": ok, "outcome": wire}

    async def _bind(self, payload: dict) -> dict:
        channel_name = str(payload.get("channel") or "webhook")
        conversation_id = str(payload.get("conversation_id") or "default")
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            return {"accepted": False, "error": "session_id required"}
        key = f"{channel_name}:{conversation_id}"
        from .hub import SessionBinding

        self.hub._add_binding(SessionBinding(key, session_id))
        self.hub._save_state()
        return {"accepted": True, "binding": key}
