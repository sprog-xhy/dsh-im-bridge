"""Integration test: a REAL channel + REAL hub together.

Channels are unit-tested against a stub hub, and the hub against a fake
channel; this test wires the REAL FeishuChannel / QQOneBotChannel into a REAL
BridgeHub with a fake dsh, proving the inbound seam
(channel.deliver -> hub.enqueue_inbound -> auto-bind -> prompt) works.
"""

import asyncio

import pytest

from dsh_im_bridge.channels.feishu import FeishuChannel
from dsh_im_bridge.channels.qq import QQOneBotChannel
from dsh_im_bridge.events import InboundMessage
from dsh_im_bridge.hub import BridgeHub


class FakeDsh:
    def __init__(self):
        self.prompts = []
        self.created = []

    def prompt(self, session_id, text, mode="queue"):
        self.prompts.append((session_id, text, mode))

    def create_session(self, **payload):
        self.created.append(payload)
        return {"sessionId": "s-auto"}

    def history(self, session_id, max_messages=50, before_seq=None):
        return {"events": [], "hasMore": False}

    def cancel(self, session_id):
        pass

    async def stream(self, on_frame, *, stop=None, backoff=None):
        if stop is not None:
            await stop.wait()


@pytest.mark.asyncio
async def test_real_feishu_channel_delivers_to_real_hub():
    dsh = FakeDsh()
    hub = BridgeHub(dsh, catch_up=False, notify_on_start=False)
    ch = FeishuChannel({"receiveChatTypes": ["p2p", "group"]})
    hub.register(ch)
    await hub.start()
    try:
        ch.deliver(InboundMessage(channel="feishu", conversation_id="oc-1", text="帮我查天气"))
        await asyncio.sleep(0.2)
        assert dsh.created          # auto-bound a session
        assert dsh.prompts and dsh.prompts[0][1] == "帮我查天气"
    finally:
        await hub.stop()


@pytest.mark.asyncio
async def test_real_qq_channel_delivers_to_real_hub():
    dsh = FakeDsh()
    hub = BridgeHub(dsh, catch_up=False, notify_on_start=False)
    ch = QQOneBotChannel({})
    hub.register(ch)
    await hub.start()
    try:
        ch.deliver(InboundMessage(channel="qq", conversation_id="group:123", text="开始任务"))
        await asyncio.sleep(0.2)
        assert dsh.prompts and dsh.prompts[0][1] == "开始任务"
    finally:
        await hub.stop()
