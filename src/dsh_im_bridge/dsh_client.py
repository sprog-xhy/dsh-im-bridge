"""Minimal client for the dsh web loopback ``/api`` bridge.

Wire protocol (verified against a live ``dsh web`` on 2026-08-27):

* Unary RPC:  ``POST {base}/api/{method}``
  body ``{"type":"client-request","rpcId":"<uuid>","method":m,"payload":p}``
  response ``{"type":"server-response","rpcId":..., "result":{"ok":true,"value":...}}``
  or ``{"result":{"ok":false,"error":{"code","message","details"}}}``

* Event stream: WebSocket ``{ws_base}/api/events.mux`` (downlink only).
  Each text frame is a ``server-request`` envelope whose ``payload`` is a mux
  frame (``session/event``, ``question/requested``, ``approval/requested``,
  ``session/subscribed``, ...). The envelope ``rpcId`` answers pending
  questions/approvals via ``/api/respond``.

* Respond: ``POST {base}/api/respond``
  body ``{"type":"client-response","rpcId":<pending rpcId>,"result":{...}}``
  response ``{"accepted":true}``.

Only the loopback host is expected: the bridge must run on the same machine as
``dsh web`` (or use a ``trustedHosts`` declaration).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import urllib.error
import urllib.request
import uuid
from typing import Any, Awaitable, Callable, Optional

import websockets

from . import parser

log = logging.getLogger("dsh_im_bridge.dsh")

FrameHandler = Callable[[dict], Awaitable[None]]


class DshError(RuntimeError):
    """A dsh RPC returned ok:false or the transport failed."""

    def __init__(self, method: str, message: str, code: Optional[str] = None):
        super().__init__(f"{method}: {message}")
        self.method = method
        self.code = code


def _uuid() -> str:
    return str(uuid.uuid4())


class DshClient:
    """Tiny async client for the dsh web loopback API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:10010",
        ws_base: Optional[str] = None,
        timeout: float = 20.0,
        connect_timeout: float = 10.0,
        max_retries: int = 5,
        backoff_base: float = 1.0,
    ):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._ws_base = (
            ws_base
            if ws_base is not None
            else self.base.replace("http://", "ws://").replace("https://", "wss://")
        )

    # -- unary RPC ---------------------------------------------------------
    def _post_json(self, path: str, body: dict) -> Any:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise DshError(path, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc
        except urllib.error.URLError as exc:
            raise DshError(path, f"cannot reach {self.base}{path}: {exc.reason}") from exc

    def call(self, method: str, payload: Any) -> Any:
        """Synchronous unary RPC. Returns ``result.value`` or raises DshError."""
        env = {"type": "client-request", "rpcId": _uuid(), "method": method, "payload": payload}
        resp = self._post_json(f"/api/{method}", env)
        if resp.get("type") != "server-response":
            raise DshError(method, f"unexpected envelope type {resp.get('type')!r}")
        result = resp.get("result") or {}
        if not result.get("ok"):
            err = result.get("error") or {}
            raise DshError(method, str(err.get("message", "unknown error")), err.get("code"))
        return result.get("value")

    def respond(self, rpc_id: str, result: dict) -> bool:
        """Answer a pending server-request (question/approval). Returns accepted."""
        env = {"type": "client-response", "rpcId": rpc_id, "result": result}
        resp = self._post_json("/api/respond", env)
        return bool(resp.get("accepted"))

    # -- convenience methods ----------------------------------------------
    def describe(self) -> dict:
        return self.call("host.describe", {})

    def list_sessions(self) -> list:
        return (self.call("session.list", {}) or {}).get("items", [])

    def list_workspaces(self) -> list:
        return (self.call("workspace.list", {}) or {}).get("items", [])

    def list_archived_session_ids(self) -> list:
        """Registry-global archive set: session ids hidden from grouping surfaces.

        ``workspace.list`` returns this in addition to the workspace tree; the
        dsh GUI uses it to hide archived sessions. ``session.list`` does NOT
        filter archived sessions, so callers must exclude these themselves.
        """
        return (self.call("workspace.list", {}) or {}).get("archivedSessionIds", [])

    def create_session(
        self,
        cwd: Optional[str] = None,
        workspace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        agent_preset: Optional[str] = None,
    ) -> dict:
        payload: dict = {}
        if workspace_id is not None:
            payload["workspaceId"] = workspace_id
        if cwd is not None:
            payload["cwd"] = cwd
        if session_id is not None:
            payload["sessionId"] = session_id
        if agent_preset is not None:
            payload["agentPreset"] = agent_preset
        return self.call("session.create", payload)

    def prompt(self, session_id: str, text: str, mode: str = "queue") -> dict:
        return self.call(
            "session.prompt",
            {
                "sessionId": session_id,
                "mode": mode,
                "content": [{"type": "text", "text": text}],
            },
        )

    def cancel(self, session_id: str) -> dict:
        return self.call("session.cancel", {"sessionId": session_id})

    def attachment(self, session_id: str, attachment_id: str) -> dict:
        """Download one session attachment.

        Returns ``{"attachment": {attachmentId, mediaType, bytes, width, height, name?},
        "data": <base64>}`` — see the dsh ``session.attachment`` API.
        """
        return self.call(
            "session.attachment",
            {"sessionId": session_id, "attachmentId": attachment_id},
        )

    def history(self, session_id: str, max_messages: int = 50, before_seq: Optional[int] = None) -> dict:
        payload: dict = {"sessionId": session_id, "maxMessages": max_messages}
        if before_seq is not None:
            payload["beforeSeq"] = before_seq
        return self.call("session.history", payload)

    def answer_question(self, rpc_id: str, session_id: str, answers: list) -> bool:
        """answers: list of {"id": ..., "selected": [...], "custom": ...}"""
        return self.respond(
            rpc_id,
            {"ok": True, "value": {"sessionId": session_id, "answer": {"answers": answers}}},
        )

    def cancel_question(self, rpc_id: str, session_id: str) -> bool:
        return self.respond(
            rpc_id,
            {
                "ok": False,
                "error": {"code": "cancelled", "message": "the user closed this question request", "details": {}},
            },
        )

    def resolve_approval(self, rpc_id: str, session_id: str, approval_id: str, outcome: str) -> bool:
        if outcome not in ("allowed-once", "rejected"):
            raise ValueError(f"invalid approval outcome {outcome!r}")
        return self.respond(
            rpc_id,
            {
                "ok": True,
                "value": {"sessionId": session_id, "approvalId": approval_id, "outcome": outcome},
            },
        )

    # -- mux stream --------------------------------------------------------
    async def stream(
        self,
        on_frame: FrameHandler,
        *,
        stop: Optional[asyncio.Event] = None,
        backoff: Optional[float] = None,
    ) -> None:
        """Connect to ``/api/events.mux`` and deliver parsed frames forever.

        Reconnects with exponential backoff on failure. Set ``stop`` to break
        out cleanly. ``on_frame`` receives the parsed frame dict (see
        :func:`dsh_im_bridge.parser.parse_mux_frame`).
        """
        uri = f"{self._ws_base}/api/events.mux"
        retry = 0
        while True:
            if stop is not None and stop.is_set():
                return
            try:
                async with websockets.connect(uri, open_timeout=self.connect_timeout) as ws:
                    log.info("mux connected: %s", uri)
                    retry = 0
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        try:
                            parsed = parser.parse_mux_frame(raw)
                        except parser.FrameError as exc:
                            log.warning("dropping malformed mux frame: %s", exc)
                            continue
                        await self._safe(on_frame, parsed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("mux stream error: %s", exc)
            if stop is not None and stop.is_set():
                return
            delay = backoff if backoff is not None else min(self.backoff_base * (2 ** retry), 60)
            retry = min(retry + 1, self.max_retries)
            log.info("reconnecting mux in %.1fs ...", delay)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    @staticmethod
    async def _safe(handler: FrameHandler, parsed: dict) -> None:
        try:
            await handler(parsed)
        except Exception as exc:  # noqa: BLE001
            log.exception("frame handler failed: %s", exc)
