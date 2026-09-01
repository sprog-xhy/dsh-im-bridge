"""Turn dsh session events into IM-friendly text.

The mux stream delivers every session's events. The hub decides *which* events
to forward; this module decides *how they look*. Text is rendered plain (IM
platforms like Feishu/QQ support basic markdown-ish text but we keep it simple
and safe), with optional truncation.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from .events import ApprovalRequest, QuestionRequest, SessionEvent, extract_attachments, text_of


def _ts(time_ms: float) -> str:
    try:
        dt = _dt.datetime.fromtimestamp(time_ms / 1000.0)
    except (OverflowError, OSError, ValueError):
        return "?"
    return dt.strftime("%H:%M:%S")


def render_session_event(
    event: SessionEvent,
    *,
    include_time: bool = True,
    include_reasoning: bool = False,
) -> str:
    """Render one session/event to text for a channel.

    ``include_reasoning`` controls whether the agent's internal reasoning is
    shown alongside its answer (default: no — notifications carry the answer).
    """
    if event.type == "assistant/message":
        text = text_of(event.data, include_reasoning=include_reasoning)
        parts = []
        if include_time:
            parts.append(f"[{_ts(event.time)}] 助手")
        if text:
            parts.append(text)
        elif event.data.get("usage"):
            parts.append(f"(完成，无文本内容 usage={event.data['usage']})")
        else:
            parts.append("(空消息)")
        return "\n".join(parts)
    text = event.text
    if event.type == "user/message":
        src = event.data.get("source") or {}
        who = "用户"
        if isinstance(src, dict) and src.get("kind") == "tool":
            who = "工具结果"
        return f"[{_ts(event.time)}] {who}: {text}" if text else f"[{_ts(event.time)}] {who}"
    if event.type == "tool/result":
        is_err = bool(event.data.get("isError"))
        body = text
        if not body:
            body = "(无文本输出)"
        return f"[{_ts(event.time)}] 🔧 工具结果{' ⚠️' if is_err else ''}: {body}"
    if event.type == "turn/end":
        return f"[{_ts(event.time)}] ✅ 回合结束"
    if event.type == "step/end":
        return f"[{_ts(event.time)}] 🟰 步骤结束"
    if event.type == "assistant/chunk":
        return text
    # generic fallback
    body = text
    return f"[{_ts(event.time)}] {event.type}: {body}" if body else f"[{_ts(event.time)}] {event.type}"


def render_question(q: QuestionRequest, *, include_header: bool = True) -> str:
    """Render a pending question batch for a user to answer."""
    lines: list[str] = ["❓ **需要你确认**"]
    for idx, item in enumerate(q.questions, start=1):
        header = item.header or "问题"
        if len(q.questions) > 1:
            lines.append(f"\n{idx}. [{header}] {item.question}")
        else:
            lines.append(f"\n{header}: {item.question}")
        if item.detail:
            lines.append(f"   {item.detail}")
        if item.options:
            for opt in item.options:
                label = opt.get("label", "?")
                desc = opt.get("description")
                lines.append(f"   - {label}{' — ' + desc if desc else ''}")
        if item.multi_select:
            lines.append("   (可多选)")
    lines.append("\n请直接回复答案：选项序号（如 1 或 1,2）/ 选项文字 / 自定义答案，或回复「跳过」跳过、回复「取消」取消。")
    return "\n".join(lines)


def render_approval(a: ApprovalRequest) -> str:
    lines = [
        "🛡️ **需要审批工具调用**",
        f"  工具: {a.tool_name}",
    ]
    if a.reason:
        lines.append(f"  原因: {a.reason}")
    lines.append("回复「允许」或「拒绝」。")
    return "\n".join(lines)


def answer_help_text() -> str:
    return (
        "可用指令：\n"
        "  /status        查看桥接与 dsh 状态\n"
        "  /sessions      列出 dsh 会话\n"
        "  /cancel-question  取消当前待确认问题\n"
        "  /allow / /reject   允许/拒绝当前审批\n"
        "  /attach <会话ID>   把本会话绑定到指定 dsh 会话\n"
        "  /new            新建一个 dsh 会话并绑定\n"
        "  /cancel         中断当前绑定的 dsh 会话（例如它卡在等待确认上）\n"
        "  /history [N]    拉取绑定会话最近 N 条记录\n\n"
        "dsh 弹出问题/审批时，直接回复即可（如 1 / 选项文字 / 允许 / 拒绝），无需 /answer。\n"
    )


def welcome_text() -> str:
    """Welcome message sent when an IM conversation first binds to a dsh session."""
    return (
        "👋 已连接到 dsh agent，现在可以开始干活了！\n\n"
        "直接发消息 = 给绑定的 dsh agent 派任务，比如：\n"
        "  • 帮我查一下当前时间是几点\n"
        "  • 列出当前目录的文件\n"
        "  • 写一个 Python 脚本做 XX\n\n"
        "任务完成后会自动收到通知；需要你确认/审批时也会主动推给你，直接回复即可。\n\n"
        + answer_help_text()
    )


def attachment_hint(event: SessionEvent) -> str:
    """Compact note about image attachments carried by a message event.

    Real channel adapters should upload the image natively; until then this
    hint tells the user the image exists and how to fetch it (bridge API).
    """
    refs = extract_attachments(event.data)
    if not refs:
        return ""
    lines = []
    for ref in refs:
        name = ref.name or "image"
        lines.append(f"📎 {name} [attachmentId={ref.attachment_id}]")
    return "\n" + "\n".join(lines)


def truncate(text: str, max_chars: int = 2000) -> str:
    """Truncate a message to max_chars code points, appending a marker."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n…(内容过长已截断)"


def split_message(text: str, max_chars: int = 2000) -> list[str]:
    """Split long text into numbered sequential chunks ``[1/N]``, ``[2/N]``, ...

    Unlike :func:`truncate`, nothing is dropped: every part is delivered, so a
    very long agent reply can be read in full across several chat messages.
    Each returned chunk (marker included) is at most ``max_chars`` code points.

    Breaks are preferred at line boundaries; a single over-long line (e.g. a
    huge code/JSON blob) is hard-split. Returns ``[text]`` unchanged when it
    fits in one message.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    # Reserve marker width: a worst-case "[NNN/NNN] " (~12 chars) before cutting.
    budget = max(max_chars - 12, 1)
    parts: list[str] = []
    remaining = text
    while len(remaining) > budget:
        cut = budget
        # Prefer the last newline inside the budget so paragraphs stay intact.
        nl = remaining.rfind("\n", 0, cut)
        if nl >= budget // 2:
            cut = nl + 1
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        parts.append(remaining)

    if len(parts) <= 1:
        return parts
    total = len(parts)
    return [f"[{i}/{total}] {part}" for i, part in enumerate(parts, 1)]
