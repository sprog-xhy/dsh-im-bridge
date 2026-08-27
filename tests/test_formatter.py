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
    assert "/answer" in text


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
