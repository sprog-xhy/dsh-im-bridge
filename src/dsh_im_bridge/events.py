"""Event models shared by the dsh client, the hub and the channels.

These are plain dataclasses; parsing from raw wire JSON lives in
:mod:`dsh_im_bridge.dsh_client` / :mod:`dsh_im_bridge.parser`.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional


@dataclasses.dataclass(frozen=True)
class ContentBlock:
    """One content block inside a message (currently only ``text`` is modeled)."""

    type: str
    text: Optional[str] = None
    # raw dict passthrough for blocks we do not model (image, tool_use, ...)
    raw: Optional[dict] = dataclasses.field(default=None, compare=False)

    @classmethod
    def from_raw(cls, raw: Any) -> "ContentBlock":
        if isinstance(raw, dict):
            return cls(type=str(raw.get("type", "unknown")), text=raw.get("text"), raw=raw)
        return cls(type="unknown", text=str(raw))


def text_of(content: Any, *, include_reasoning: bool = True) -> str:
    """Join the textual content of a message content value.

    Handles the wire shapes seen in dsh session events: a content block list,
    a ``{message: {...}}`` wrapper (assistant messages), ``{content: [...],
    source: ...}`` (user messages), and a ``{text: str}`` block. Reasoning
    blocks are included only when ``include_reasoning`` is true (notifications
    usually want just the answer, not the thinking).
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("message"), (dict, list, str)):
            return text_of(content["message"], include_reasoning=include_reasoning)
        if "content" in content:
            return text_of(content["content"], include_reasoning=include_reasoning)
        if isinstance(content.get("text"), str):
            return content["text"]
        return ""
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "reasoning" and include_reasoning:
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    parts.append(f"[tool {block.get('name', '?')}]")
                elif block.get("type") == "tool_result":
                    inner = text_of(block.get("content"), include_reasoning=include_reasoning)
                    if inner:
                        parts.append(inner)
                elif block.get("type") == "image":
                    parts.append("[image]")
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return str(content)


@dataclasses.dataclass(frozen=True)
class SessionEvent:
    """A single session log event delivered over the mux stream."""

    type: str
    seq: int
    time: float
    data: dict
    session_id: str
    source_event_seqs: tuple = ()
    raw: dict = dataclasses.field(default=None, compare=False)

    @property
    def text(self) -> str:
        """Best-effort text rendering of the event data."""
        return text_of(self.data)


@dataclasses.dataclass(frozen=True)
class QuestionItem:
    id: str
    question: str
    header: Optional[str] = None
    detail: Optional[str] = None
    options: tuple = ()
    multi_select: bool = False

    @classmethod
    def from_raw(cls, raw: dict) -> "QuestionItem":
        return cls(
            id=str(raw.get("id", "")),
            question=str(raw.get("question", "")),
            header=raw.get("header"),
            detail=raw.get("detail"),
            options=tuple(raw.get("options") or []),
            multi_select=bool(raw.get("multiSelect")),
        )


@dataclasses.dataclass(frozen=True)
class QuestionRequest:
    """A ``question/requested`` frame; answering requires the server-request rpc id."""

    rpc_id: str
    session_id: str
    questions: tuple
    raw: dict = dataclasses.field(default=None, compare=False)

    @classmethod
    def from_frame(cls, rpc_id: str, payload: dict) -> "QuestionRequest":
        session_id = str(payload.get("sessionId", ""))
        questions = tuple(
            QuestionItem.from_raw(q) for q in (payload.get("questions") or [])
        )
        return cls(rpc_id=rpc_id, session_id=session_id, questions=questions, raw=payload)


@dataclasses.dataclass(frozen=True)
class ApprovalRequest:
    """An ``approval/requested`` frame (a tool call waiting on human approval)."""

    session_id: str
    approval_id: str
    tool_name: str
    call_id: Optional[str] = None
    reason: Optional[str] = None
    raw: dict = dataclasses.field(default=None, compare=False)

    @classmethod
    def from_frame(cls, payload: dict) -> "ApprovalRequest":
        return cls(
            session_id=str(payload.get("sessionId", "")),
            approval_id=str(payload.get("approvalId", "")),
            tool_name=str(payload.get("toolName", "")),
            call_id=payload.get("callId"),
            reason=payload.get("reason"),
            raw=payload,
        )


@dataclasses.dataclass(frozen=True)
class InboundMessage:
    """A user message arriving from an IM channel, destined for a dsh session."""

    channel: str            # e.g. "feishu", "qq", "console", "webhook"
    conversation_id: str    # e.g. Feishu chat_id / QQ group+user / console fixed id
    text: str
    sender: Optional[str] = None
    raw: Optional[dict] = None


@dataclasses.dataclass(frozen=True)
class OutboundMessage:
    """A message the hub wants delivered to a channel conversation."""

    channel: str
    conversation_id: str
    text: str
    kind: str = "notify"    # notify | answer | question | approval | error
    raw: Optional[dict] = None
