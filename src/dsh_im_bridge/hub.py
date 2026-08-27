"""BridgeHub: routes messages between IM channels and a dsh agent.

Responsibilities:

* owns the :class:`DshClient` and the mux stream loop;
* registers channels and binds them;
* maps each IM conversation to a dsh session (explicit config, ``/attach``,
  ``/new``, or auto-create on first message) and persists the map;
* routes inbound IM text to ``session.prompt``;
* forwards relevant mux events (final assistant messages, task end, pending
  questions/approvals) to the conversations bound to that session;
* answers questions / approvals on behalf of the user from slash commands.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from .channels.base import Channel
from .dsh_client import DshClient, DshError
from .events import (
    ApprovalRequest,
    InboundMessage,
    OutboundMessage,
    QuestionRequest,
    SessionEvent,
)
from .formatter import (
    FINAL_MESSAGE_EVENTS,
    STEP_END,
    TURN_END,
    answer_help_text,
    render_approval,
    render_question,
    render_session_event,
    truncate,
)
from .parser import approval_from_frame, parse_mux_frame, question_from_frame

log = logging.getLogger("dsh_im_bridge.hub")

# Event types that produce a notification when they arrive for a bound session.
DEFAULT_FORWARD_EVENTS = frozenset(
    {"user/message", "assistant/message", "tool/result", "turn/end", "step/end"}
)


class SessionBinding:
    """One IM conversation -> dsh session binding."""

    def __init__(self, conversation_key: str, session_id: str, title: str = ""):
        self.conversation_key = conversation_key
        self.session_id = session_id
        self.title = title

    def to_dict(self) -> dict:
        return {
            "conversation": self.conversation_key,
            "sessionId": self.session_id,
            "title": self.title,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionBinding":
        return cls(
            conversation_key=str(data.get("conversation", "")),
            session_id=str(data.get("sessionId", "")),
            title=str(data.get("title", "")),
        )


class BridgeHub:
    def __init__(
        self,
        client: DshClient,
        *,
        state_file: Optional[Path] = None,
        forward_events: Optional[frozenset] = None,
        max_message_chars: int = 2000,
        default_workspace_id: Optional[str] = None,
        default_cwd: Optional[str] = None,
        agent_preset: Optional[str] = None,
        catch_up: bool = True,
        catch_up_max_events: int = 200,
    ):
        self.client = client
        self.state_file = state_file
        self.forward_events = (
            forward_events if forward_events is not None else DEFAULT_FORWARD_EVENTS
        )
        self.max_message_chars = max_message_chars
        self.default_workspace_id = default_workspace_id
        self.default_cwd = default_cwd
        self.agent_preset = agent_preset
        self.catch_up = catch_up
        self.catch_up_max_events = catch_up_max_events

        self.channels: dict[str, Channel] = {}
        # conversation_key -> binding
        self.bindings: dict[str, SessionBinding] = {}
        # session_id -> set(conversation_key)
        self._by_session: dict[str, set] = {}
        # pending server-requests: rpc_id -> {kind, session_id, ...}
        self.pending: dict[str, dict] = {}
        # session_id -> last event seq we have forwarded (for restart catch-up)
        self._notified_seq: dict[str, int] = {}
        self._queue: "asyncio.Queue[InboundMessage]" = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    # -- lifecycle ---------------------------------------------------------
    def register(self, channel: Channel) -> None:
        channel.bind(self)
        self.channels[channel.name] = channel

    async def start(self) -> None:
        self._load_state()
        for channel in self.channels.values():
            await channel.start()
        self._tasks.append(asyncio.create_task(self._consume_inbound(), name="inbound"))
        self._tasks.append(
            asyncio.create_task(self._run_mux(), name="mux")
        )
        if self.catch_up:
            self._tasks.append(
                asyncio.create_task(self._catch_up(), name="catch-up")
            )
        log.info("hub started with channels: %s", ", ".join(self.channels))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        for channel in self.channels.values():
            try:
                await channel.stop()
            except Exception:  # noqa: BLE001
                log.exception("error stopping channel %s", channel.name)
        self._save_state()
        log.info("hub stopped")

    # -- inbound routing ---------------------------------------------------
    def enqueue_inbound(self, message: InboundMessage) -> None:
        self._queue.put_nowait(message)

    async def _consume_inbound(self) -> None:
        while True:
            message = await self._queue.get()
            try:
                await self._handle_inbound(message)
            except Exception as exc:  # noqa: BLE001
                log.exception("inbound handler failed: %s", exc)
                await self._send(
                    message.channel,
                    message.conversation_id,
                    f"处理消息失败: {exc}",
                    kind="error",
                )

    async def _handle_inbound(self, message: InboundMessage) -> None:
        text = (message.text or "").strip()
        if not text:
            return
        key = self._conv_key(message.channel, message.conversation_id)

        # slash commands
        if text.startswith("/"):
            await self._handle_command(message, key, text)
            return

        binding = self.bindings.get(key)
        if binding is None:
            try:
                binding = await self._auto_bind(message.channel, message.conversation_id)
            except DshError as exc:
                await self._send(
                    message.channel,
                    message.conversation_id,
                    f"无法自动创建 dsh 会话: {exc}",
                    kind="error",
                )
                return
            await self._send(
                message.channel,
                message.conversation_id,
                f"已绑定到 dsh 会话 {binding.session_id}，现在开始干活 🚀",
                kind="notify",
            )
        try:
            self.client.prompt(binding.session_id, text, mode="queue")
            log.info("prompt -> %s: %s", binding.session_id, text[:120])
        except DshError as exc:
            await self._send(
                message.channel,
                message.conversation_id,
                f"发送给 dsh 失败: {exc}",
                kind="error",
            )

    async def _auto_bind(self, channel: str, conversation_id: str) -> SessionBinding:
        payload: dict = {}
        if self.default_workspace_id:
            payload["workspaceId"] = self.default_workspace_id
        elif self.default_cwd:
            payload["cwd"] = self.default_cwd
        if self.agent_preset:
            payload["agentPreset"] = self.agent_preset
        created = self.client.create_session(**payload)
        session_id = created["sessionId"]
        binding = SessionBinding(
            self._conv_key(channel, conversation_id), session_id
        )
        self._add_binding(binding)
        self._save_state()
        return binding

    # -- mux stream --------------------------------------------------------
    async def _run_mux(self) -> None:
        await self.client.stream(self._on_frame, stop=self._stop)

    async def _catch_up(self) -> None:
        """Backfill notifications missed while the bridge was down.

        Only sessions that have a known notified-seq watermark are replayed
        (i.e. the bridge ran before and left off somewhere); brand-new bindings
        are left to go live from now on, so a first start never spams history.
        """
        try:
            sessions = {b.session_id for b in self.bindings.values()}
            for session_id in sorted(sessions):
                watermark = self._notified_seq.get(session_id)
                if watermark is None:
                    continue
                try:
                    page = self.client.history(
                        session_id, max_messages=self.catch_up_max_events
                    )
                except DshError as exc:
                    log.warning("catch-up: cannot read history for %s: %s", session_id, exc)
                    continue
                events = page.get("events") or []
                for entry in events:
                    raw = entry.get("event") or {}
                    if not isinstance(raw, dict):
                        continue
                    seq = int(raw.get("seq", 0))
                    if seq <= watermark:
                        continue
                    event = SessionEvent(
                        type=str(raw.get("type", "")),
                        seq=seq,
                        time=float(raw.get("time", 0.0)),
                        data=raw.get("data") if isinstance(raw.get("data"), dict) else {},
                        session_id=session_id,
                        raw=raw,
                    )
                    await self._forward_session_event(event)
                if events:
                    last = max(int((e.get("event") or {}).get("seq", 0)) for e in events)
                    if last > self._notified_seq.get(session_id, -1):
                        self._notified_seq[session_id] = last
            if self._notified_seq:
                self._save_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("catch-up failed: %s", exc)

    async def _on_frame(self, parsed: dict) -> None:
        kind = parsed.get("kind")
        if kind == "session/event":
            await self._on_session_event(parsed)
        elif kind == "question/requested":
            question = question_from_frame(parsed)
            if question is not None:
                await self._on_question(question)
        elif kind == "approval/requested":
            approval = approval_from_frame(parsed)
            if approval is not None:
                await self._on_approval(approval, parsed.get("rpc_id", ""))
        elif kind in ("question/resolved", "approval/resolved"):
            await self._on_resolved(kind, parsed["payload"])
        elif kind == "session/subscribed":
            pass  # baseline marker, nothing to do
        elif kind == "stream/error":
            log.warning("dsh mux stream error: %s", parsed.get("payload", {}).get("error"))
        else:
            log.debug("ignoring mux frame %s", kind)

    async def _on_session_event(self, parsed: dict) -> None:
        event = parsed.get("event")
        if event is None:
            return
        await self._forward_session_event(event)

    async def _forward_session_event(self, event) -> None:
        """Forward one session event to the conversations bound to its session.

        Applies the forward-policy filter, updates the per-session notified-seq
        watermark (used for restart catch-up), and sends to each bound channel.
        """
        session_id = event.session_id
        if not session_id:
            return
        keys = self._by_session.get(session_id)
        if not keys:
            return
        if event.type not in self.forward_events:
            return
        # tool/result: forward only when it is an error or carries text
        if event.type == "tool/result":
            data = event.data or {}
            if not data.get("isError") and not (data.get("content") or data.get("message")):
                return
        text = render_session_event(event)
        if not text:
            return
        text = truncate(text, self.max_message_chars)
        for key in list(keys):
            channel_name, conversation_id = self._split_key(key)
            await self._send(channel_name, conversation_id, text, kind="event")
        self._notified_seq[session_id] = max(self._notified_seq.get(session_id, -1), event.seq)

    async def _on_question(self, question: QuestionRequest) -> None:
        keys = self._by_session.get(question.session_id) or set()
        text = truncate(render_question(question), self.max_message_chars)
        self.pending[question.rpc_id] = {
            "kind": "question",
            "session_id": question.session_id,
            "conversations": list(keys),
        }
        if not keys:
            log.info("question for unbound session %s; nobody to notify", question.session_id)
            return
        for key in list(keys):
            channel_name, conversation_id = self._split_key(key)
            await self._send(channel_name, conversation_id, text, kind="question")

    async def _on_approval(self, approval: ApprovalRequest, rpc_id: str) -> None:
        keys = self._by_session.get(approval.session_id) or set()
        self.pending[rpc_id] = {
            "kind": "approval",
            "session_id": approval.session_id,
            "approval_id": approval.approval_id,
            "conversations": list(keys),
        }
        text = truncate(render_approval(approval), self.max_message_chars)
        if not keys:
            log.info("approval for unbound session %s; nobody to notify", approval.session_id)
            return
        for key in list(keys):
            channel_name, conversation_id = self._split_key(key)
            await self._send(channel_name, conversation_id, text, kind="approval")

    async def _on_resolved(self, kind: str, payload: dict) -> None:
        if kind == "question/resolved":
            rpc_id = payload.get("questionRpcId")
        else:
            rpc_id = next(
                (k for k, v in self.pending.items() if v.get("approval_id") == payload.get("approvalId")),
                None,
            )
        if rpc_id and rpc_id in self.pending:
            entry = self.pending.pop(rpc_id)
            outcome = payload.get("outcome", "resolved")
            for key in entry.get("conversations", []):
                channel_name, conversation_id = self._split_key(key)
                await self._send(
                    channel_name,
                    conversation_id,
                    f"✅ 已处理: {outcome}",
                    kind="notify",
                )

    # -- slash commands ----------------------------------------------------
    async def _handle_command(self, message: InboundMessage, key: str, text: str) -> None:
        parts = text[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("help", "h"):
            await self._send(message.channel, message.conversation_id, answer_help_text())
        elif cmd in ("status", "s"):
            await self._cmd_status(message)
        elif cmd in ("sessions", "ls"):
            await self._cmd_sessions(message)
        elif cmd in ("attach", "bind"):
            await self._cmd_attach(message, key, arg)
        elif cmd in ("new",):
            await self._cmd_new(message)
        elif cmd == "answer":
            await self._cmd_answer(message, key, arg)
        elif cmd in ("cancel-question", "cancel"):
            await self._cmd_cancel_question(message, key)
        elif cmd in ("allow", "approve"):
            await self._cmd_approval(message, key, "allowed-once")
        elif cmd in ("reject", "deny"):
            await self._cmd_approval(message, key, "rejected")
        else:
            await self._send(
                message.channel,
                message.conversation_id,
                f"未知指令 /{cmd}，输入 /help 查看可用指令。",
                kind="error",
            )

    async def _cmd_status(self, message: InboundMessage) -> None:
        try:
            desc = self.client.describe()
            lines = [
                "**dsh 状态**",
                f"  version : {desc.get('version')}",
                f"  cwd     : {desc.get('cwd')}",
                f"  provider: {desc.get('provider')}",
                f"  model   : {desc.get('model')}",
                f"  已绑定会话数: {len(set(b.session_id for b in self.bindings.values()))}",
                f"  待确认数: {len(self.pending)}",
            ]
            await self._send(message.channel, message.conversation_id, "\n".join(lines))
        except DshError as exc:
            await self._send(message.channel, message.conversation_id, f"查询状态失败: {exc}", kind="error")

    async def _cmd_sessions(self, message: InboundMessage) -> None:
        try:
            items = self.client.list_sessions()
            if not items:
                await self._send(message.channel, message.conversation_id, "暂无 dsh 会话")
                return
            lines = ["**dsh 会话**"]
            for item in items[:20]:
                flag = "🟢" if item.get("running") else "⚪"
                lines.append(f"  {flag} {item.get('sessionId')}  cwd={item.get('cwd')}")
            await self._send(
                message.channel,
                message.conversation_id,
                truncate("\n".join(lines), self.max_message_chars),
            )
        except DshError as exc:
            await self._send(message.channel, message.conversation_id, f"列出会话失败: {exc}", kind="error")

    async def _cmd_attach(self, message: InboundMessage, key: str, arg: str) -> None:
        if not arg:
            await self._send(message.channel, message.conversation_id, "用法: /attach <会话ID>")
            return
        try:
            self.client.history(arg, max_messages=1)  # validate session exists
        except DshError as exc:
            await self._send(message.channel, message.conversation_id, f"会话不存在: {exc}", kind="error")
            return
        binding = SessionBinding(key, arg)
        self._add_binding(binding)
        self._save_state()
        await self._send(message.channel, message.conversation_id, f"已绑定到会话 {arg}")

    async def _cmd_new(self, message: InboundMessage) -> None:
        try:
            binding = await self._auto_bind(message.channel, message.conversation_id)
        except DshError as exc:
            await self._send(message.channel, message.conversation_id, f"新建会话失败: {exc}", kind="error")
            return
        await self._send(message.channel, message.conversation_id, f"已新建并绑定会话 {binding.session_id}")

    async def _cmd_answer(self, message: InboundMessage, key: str, arg: str) -> None:
        pending = self._first_pending(key, "question")
        if pending is None:
            await self._send(message.channel, message.conversation_id, "当前没有待确认的问题。", kind="error")
            return
        answers = parse_answer_arg(arg)
        if answers is None:
            await self._send(
                message.channel,
                message.conversation_id,
                "格式: /answer 1:选项A,2:自定义文本 （用逗号分隔多个问题的答案）",
                kind="error",
            )
            return
        ok = self.client.answer_question(pending["rpc_id"], pending["session_id"], answers)
        await self._send(
            message.channel,
            message.conversation_id,
            "答案已提交 ✅" if ok else "答案提交失败（可能已过期）",
        )
        if ok:
            self.pending.pop(pending["rpc_id"], None)

    async def _cmd_cancel_question(self, message: InboundMessage, key: str) -> None:
        pending = self._first_pending(key, "question")
        if pending is None:
            await self._send(message.channel, message.conversation_id, "当前没有待确认的问题。", kind="error")
            return
        ok = self.client.cancel_question(pending["rpc_id"], pending["session_id"])
        await self._send(
            message.channel,
            message.conversation_id,
            "已取消问题 ✅" if ok else "取消失败（可能已过期）",
        )
        if ok:
            self.pending.pop(pending["rpc_id"], None)

    async def _cmd_approval(self, message: InboundMessage, key: str, outcome: str) -> None:
        pending = self._first_pending(key, "approval")
        if pending is None:
            await self._send(message.channel, message.conversation_id, "当前没有待审批的工具调用。", kind="error")
            return
        ok = self.client.resolve_approval(
            pending["rpc_id"], pending["session_id"], pending["approval_id"], outcome
        )
        await self._send(
            message.channel,
            message.conversation_id,
            "已允许 ✅" if ok and outcome == "allowed-once" else ("已拒绝 ✅" if ok else "审批失败（可能已过期）"),
        )
        if ok:
            self.pending.pop(pending["rpc_id"], None)

    def _first_pending(self, key: str, kind: str) -> Optional[dict]:
        for rpc_id, entry in self.pending.items():
            if entry["kind"] == kind and key in entry.get("conversations", []):
                return {"rpc_id": rpc_id, **entry}
        return None

    # -- outbound ----------------------------------------------------------
    async def _send(self, channel_name: str, conversation_id: str, text: str, kind: str = "notify") -> None:
        channel = self.channels.get(channel_name)
        if channel is None:
            log.warning("no channel %r to deliver message", channel_name)
            return
        try:
            await channel.send(conversation_id, text, kind=kind)
        except Exception as exc:  # noqa: BLE001
            log.exception("channel %s send failed: %s", channel_name, exc)

    # -- binding helpers ---------------------------------------------------
    @staticmethod
    def _conv_key(channel: str, conversation_id: str) -> str:
        return f"{channel}:{conversation_id}"

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        channel, _, conversation = key.partition(":")
        return channel, conversation

    def _add_binding(self, binding: SessionBinding) -> None:
        self.bindings[binding.conversation_key] = binding
        self._by_session.setdefault(binding.session_id, set()).add(binding.conversation_key)

    # -- persistence -------------------------------------------------------
    def _state_path(self) -> Optional[Path]:
        return self.state_file

    def _load_state(self) -> None:
        path = self._state_path()
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for row in data.get("bindings", []):
                binding = SessionBinding.from_dict(row)
                if binding.conversation_key and binding.session_id:
                    self._add_binding(binding)
            notified = data.get("notifiedSeq") or {}
            for sid, seq in notified.items():
                try:
                    self._notified_seq[str(sid)] = int(seq)
                except (TypeError, ValueError):
                    pass
            log.info("loaded %d bindings, %d watermarks from %s", len(self.bindings), len(self._notified_seq), path)
        except Exception:  # noqa: BLE001
            log.exception("failed to load bridge state from %s", path)

    def _save_state(self) -> None:
        path = self._state_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "bindings": [b.to_dict() for b in self.bindings.values()],
                "notifiedSeq": dict(self._notified_seq),
            }
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            log.exception("failed to save bridge state to %s", path)


def parse_answer_arg(arg: str) -> Optional[list]:
    """Parse ``/answer 1:选项A,2:自定义文本`` into answer item dicts.

    Returns None when the argument is malformed. Items reference question order
    by 1-based index, or by question id when the segment is an exact id.
    """
    if not arg or not arg.strip():
        return None
    answers: list[dict] = []
    for segment in arg.split(","):
        segment = segment.strip()
        if not segment:
            continue
        if ":" in segment:
            ref, _, value = segment.partition(":")
            ref = ref.strip()
            value = value.strip()
        else:
            ref, value = str(len(answers) + 1), segment.strip()
        if not ref or not value:
            return None
        if ref.isdigit():
            answers.append({"id": str(int(ref)), "selected": [], "custom": value})
        else:
            answers.append({"id": ref, "selected": [value], "custom": ""})
    return answers or None
