"""Parse raw wire JSON from the dsh `/api` bridge into typed event models.

The wire shapes here were reverse-engineered from the shipped
``dsh-client-connection`` bundle and verified against a live ``dsh web``
instance. A ``server-request`` frame over ``/api/events.mux`` looks like:

.. code-block:: json

    {
      "type": "server-request",
      "rpcId": "<uuid>",
      "method": "question/requested",
      "payload": {"type": "question/requested", "sessionId": "...", "questions": [...]}
    }

``session/event`` frames carry ``event`` = a SessionEvent; the rest carry the
frame fields directly in ``payload``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .events import (
    ApprovalRequest,
    QuestionRequest,
    SessionEvent,
)


class FrameError(ValueError):
    """Raised when a frame cannot be understood."""


def _session_event(session_id: str, raw_event: Any) -> Optional[SessionEvent]:
    if not isinstance(raw_event, dict):
        return None
    return SessionEvent(
        type=str(raw_event.get("type", "")),
        seq=int(raw_event.get("seq", 0)),
        time=float(raw_event.get("time", 0.0)),
        data=raw_event.get("data") if isinstance(raw_event.get("data"), dict) else {},
        session_id=session_id,
        source_event_seqs=tuple(raw_event.get("sourceEventSeqs") or ()),
        raw=raw_event,
    )


def parse_mux_frame(raw: Any) -> dict:
    """Turn one decoded mux frame (a ``server-request`` envelope) into a dict.

    Returns ``{"kind": <frame type>, "rpc_id": ..., "payload": ..., "event": ...}``
    where ``event`` is a parsed :class:`SessionEvent` for ``session/event``
    frames (else None).
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FrameError(f"bad JSON frame: {exc}") from exc
    if not isinstance(raw, dict):
        raise FrameError("frame is not an object")

    env_type = raw.get("type")
    if env_type == "server-request":
        rpc_id = str(raw.get("rpcId", ""))
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            raise FrameError("server-request payload is not an object")
        method = str(raw.get("method", payload.get("type", "")))
        kind = str(payload.get("type", method))
        return {
            "kind": kind,
            "rpc_id": rpc_id,
            "method": method,
            "payload": payload,
            "event": _session_event(
                str(payload.get("sessionId", "")), payload.get("event")
            ),
        }
    if env_type == "server-response":
        # Mux is downlink-only from the server; treat a response as informational.
        return {"kind": "server-response", "rpc_id": raw.get("rpcId"), "payload": raw}
    raise FrameError(f"unknown envelope type {env_type!r}")


def question_from_frame(parsed: dict) -> Optional[QuestionRequest]:
    if parsed["kind"] == "question/requested":
        return QuestionRequest.from_frame(parsed["rpc_id"], parsed["payload"])
    return None


def approval_from_frame(parsed: dict) -> Optional[ApprovalRequest]:
    if parsed["kind"] == "approval/requested":
        return ApprovalRequest.from_frame(parsed["payload"])
    return None
