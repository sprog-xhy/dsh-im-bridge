"""Tests for event rendering to IM-friendly text."""

from dsh_im_bridge.events import ApprovalRequest, QuestionRequest, SessionEvent
from dsh_im_bridge import formatter


def _ev(type_, text="", seq=1, extra=None):
    data = dict(extra or {})
    if text:
        data["content"] = [{"type": "text", "text": text}]
    return SessionEvent(type=type_, seq=seq, time=1787849519604.0, data=data, session_id="s-1")


def test_render_assistant_message():
    text = formatter.render_session_event(_ev("assistant/message", "done!"))
    assert "助手" in text
    assert "done!" in text


def test_render_assistant_message_wrapped():
    # dsh wraps assistant content as data.message.content
    ev = SessionEvent(
        type="assistant/message",
        seq=2,
        time=1787849519604.0,
        data={
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "reasoning", "text": "think"},
                    {"type": "text", "text": "OK"},
                ],
                "source": {},
            }
        },
        session_id="s-1",
    )
    assert ev.text == "think\nOK"
    assert "OK" in formatter.render_session_event(ev)


def test_render_tool_result_error():
    text = formatter.render_session_event(
        _ev("tool/result", "boom", extra={"isError": True})
    )
    assert "工具结果" in text
    assert "⚠️" in text


def test_render_question():
    q = QuestionRequest.from_frame(
        "r-1",
        {
            "sessionId": "s",
            "questions": [
                {
                    "id": "q1",
                    "question": "继续?",
                    "header": "确认",
                    "options": [{"label": "是", "description": "继续"}, {"label": "否"}],
                }
            ],
        },
    )
    text = formatter.render_question(q)
    assert "继续?" in text
    assert "是" in text
    assert "否" in text
    assert "直接回复" in text
    assert "/answer" not in text


def test_render_approval():
    a = ApprovalRequest.from_frame(
        {"sessionId": "s", "approvalId": "a", "toolName": "bash", "reason": "run"}
    )
    text = formatter.render_approval(a)
    assert "bash" in text
    assert "允许" in text


def test_truncate():
    assert formatter.truncate("abc", 10) == "abc"
    out = formatter.truncate("x" * 100, 20)
    assert len(out) <= 40
    assert "截断" in out


def test_split_message_short_unchanged():
    assert formatter.split_message("short", 2000) == ["short"]
    assert formatter.split_message("", 2000) == [""]


def test_split_message_splits_and_numbered():
    long_text = "第1行\n" + "x" * 5000 + "\n最后"
    parts = formatter.split_message(long_text, 100)
    assert len(parts) > 1
    # every part is within the limit (marker included)
    for p in parts:
        assert len(p) <= 100
    # numbered markers [i/N]
    assert parts[0].startswith("[1/")
    assert parts[-1].startswith("[")
    # join of de-markered parts equals the original text (nothing lost)
    import re

    joined = "".join(re.sub(r"^\[\d+/\d+\] ", "", p) for p in parts)
    assert joined == long_text


def test_split_message_single_line_hard_split():
    blob = "A" * 5000
    parts = formatter.split_message(blob, 100)
    assert len(parts) > 1
    for p in parts:
        assert len(p) <= 100  # marker included stays within the limit
    import re

    joined = "".join(re.sub(r"^\[\d+/\d+\] ", "", p) for p in parts)
    assert joined == blob  # nothing lost


def test_split_message_prefers_line_breaks():
    # 5 lines of 40 chars each: with max_chars=60 (budget 48) each split lands
    # right after a line's newline, so we get exactly 5 well-formed parts.
    lines = ["b" * 40] * 5
    text = "\n".join(lines)
    parts = formatter.split_message(text, 60)
    assert len(parts) == 5
    for p in parts:
        assert len(p) <= 60
    # the first N-1 parts are cut exactly after a line's newline
    assert all(parts[i].endswith("\n") for i in range(len(parts) - 1))


def test_events_model_text_of_content_list():
    ev = _ev("user/message", "", extra={"content": [{"type": "text", "text": "hi"}]})
    assert ev.text == "hi"


def test_extract_attachments():
    from dsh_im_bridge.events import extract_attachments

    content = [
        {"type": "text", "text": "hi"},
        {"type": "image", "attachmentId": "att-1", "mediaType": "image/png", "name": "plot.png"},
        {"type": "image", "attachment": {"attachmentId": "att-2"}},
        {"type": "image"},  # no id -> skipped
    ]
    refs = extract_attachments(content)
    assert [r.attachment_id for r in refs] == ["att-1", "att-2"]
    assert refs[0].name == "plot.png"
    assert refs[0].media_type == "image/png"


def test_image_block_rendering():
    ev = _ev(
        "assistant/message",
        "",
        extra={
            "message": {
                "content": [
                    {"type": "text", "text": "这是结果"},
                    {"type": "image", "attachmentId": "att-9", "name": "chart.png"},
                ]
            }
        },
    )
    assert ev.text == "这是结果\n[图片: chart.png]"


def test_attachment_hint():
    from dsh_im_bridge.formatter import attachment_hint

    ev = _ev(
        "assistant/message",
        "",
        extra={
            "message": {
                "content": [
                    {"type": "text", "text": "结果"},
                    {"type": "image", "attachmentId": "att-9", "name": "chart.png"},
                ]
            }
        },
    )
    hint = attachment_hint(ev)
    assert "chart.png" in hint
    assert "att-9" in hint

    plain = _ev("assistant/message", "no image")
    assert attachment_hint(plain) == ""
