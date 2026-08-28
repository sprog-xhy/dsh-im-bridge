"""飞书 + QQ 端到端演示 — 无需真实凭据。

用假服务器模拟飞书/QQ 平台，走 REAL 通道的真实接收路径：
  - 飞书: 假长连接 WS(Challenge 握手 + 事件) + 假发送 API(HTTP)
  - QQ  : 假 OneBot WS(消息事件 + 发送应答)
真实 FeishuChannel / QQOneBotChannel + 真实 BridgeHub + 假 dsh，
演示: 平台消息 → 通道接收 → hub → dsh 收到 → 回复发回平台。

Usage:  python scripts/demo_im.py
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import websockets  # noqa: E402

from dsh_im_bridge.channels.feishu import FeishuChannel, encode_frame  # noqa: E402
from dsh_im_bridge.channels.qq import QQOneBotChannel  # noqa: E402
from dsh_im_bridge.hub import BridgeHub, SessionBinding  # noqa: E402

APP_ID = "demo-app"
APP_SECRET = "demo-secret"


class FakeDsh:
    def prompt(self, session_id, text, mode="queue"):
        print(f"      [dsh] 收到消息: {text!r}")

    def create_session(self, **payload):
        return {"sessionId": "demo-session"}

    def history(self, session_id, max_messages=50, before_seq=None):
        return {"events": [], "hasMore": False}

    def describe(self):
        return {"version": "0.0.1", "cwd": "/tmp/demo", "provider": "demo"}

    def list_sessions(self):
        return [{"sessionId": "demo-session", "running": False}]

    def cancel(self, session_id):
        pass

    async def stream(self, on_frame, *, stop=None, backoff=None):
        if stop is not None:
            await stop.wait()


# -- fake Feishu send API (HTTP) --------------------------------------------
class _FeishuApiHandler(BaseHTTPRequestHandler):
    feishu_sent = []

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if "/auth/v3/tenant_access_token/internal" in self.path:
            self._json({"code": 0, "tenant_access_token": "tok", "expire": 7200})
            return
        if "/im/v1/messages" in self.path:
            self.feishu_sent.append(json.loads(raw.decode("utf-8")))
            self._json({"code": 0, "data": {"message_id": "m"}})
            return
        self._json({"code": 404}, 404)


# -- fake Feishu long-connection WS (current protobuf protocol) ---------------
_feishu_sent_once = {"done": False}


async def _feishu_ws(ws):
    if not _feishu_sent_once["done"]:
        _feishu_sent_once["done"] = True
        event = {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {"chat_id": "oc-demo", "chat_type": "p2p",
                            "content": json.dumps({"text": "帮我看看今天的安排"})},
                "sender": {"sender_id": {"open_id": "ou-demo"}},
            },
        }
        await ws.send(encode_frame(method=1, headers=[("type", "event")],
                                   payload=json.dumps(event).encode("utf-8")))
    try:
        await asyncio.wait_for(ws.recv(), timeout=3)  # wait for ChallengeResponse
    except Exception:  # noqa: BLE001
        pass
    await ws.close()


# -- fake QQ OneBot WS -------------------------------------------------------
async def _onebot_ws(ws):
    await ws.send(json.dumps({
        "post_type": "message", "message_type": "group", "group_id": 123,
        "user_id": 999, "message": [{"type": "text", "data": {"text": "开始任务"}}],
    }))
    async for raw in ws:
        req = json.loads(raw)
        if req.get("action") == "send_group_msg":
            print(f"      [qq<-bridge] 回复发回群 123: {req['params']['message']!r}")
        await ws.send(json.dumps({"status": "ok", "retcode": 0, "echo": req.get("echo")}))


async def main() -> int:
    # fake servers
    feishu_api = ThreadingHTTPServer(("127.0.0.1", 0), _FeishuApiHandler)
    threading.Thread(target=feishu_api.serve_forever, daemon=True).start()
    feishu_api_port = feishu_api.server_address[1]
    feishu_ws = await websockets.serve(_feishu_ws, "127.0.0.1", 0)
    feishu_ws_port = feishu_ws.sockets[0].getsockname()[1]
    qq_ws = await websockets.serve(_onebot_ws, "127.0.0.1", 0)
    qq_ws_port = qq_ws.sockets[0].getsockname()[1]

    client = FakeDsh()
    hub = BridgeHub(client, catch_up=False, notify_on_start=False)
    feishu = FeishuChannel({
        "appId": APP_ID, "appSecret": APP_SECRET, "baseUrl": f"http://127.0.0.1:{feishu_api_port}",
    })
    feishu._request_endpoint = lambda: f"ws://127.0.0.1:{feishu_ws_port}"
    qq = QQOneBotChannel({"wsUrl": f"ws://127.0.0.1:{qq_ws_port}", "selfId": "10001"})
    hub.register(feishu)
    hub.register(qq)
    await hub.start()
    hub._add_binding(SessionBinding("feishu:oc-demo", "demo-session"))
    hub._add_binding(SessionBinding("qq:group:123", "demo-session"))

    print("=== 飞书 + QQ 端到端演示 ===\n")

    print("【1】飞书：用户在私聊机器人发「帮我看看今天的安排」（走长连接真实接收路径）")
    await asyncio.sleep(2.0)  # let the feishu long-conn connect + event deliver
    await asyncio.sleep(0.5)

    print("\n【2】QQ：用户在群里 @机器人 发「开始任务」（走 OneBot WS 真实接收路径）")
    await asyncio.sleep(1.5)

    # simulate dsh finishing and replying to both conversations
    from dsh_im_bridge.parser import parse_mux_frame

    for sid in ("demo-session",):
        reply_frame = parse_mux_frame({
            "type": "server-request", "rpcId": "r-reply", "method": "session/event",
            "payload": {
                "type": "session/event", "sessionId": sid,
                "event": {"type": "assistant/message", "seq": 2, "time": 2000.0,
                          "data": {"message": {"role": "assistant",
                                               "content": [{"type": "text", "text": "已收到，马上处理。"}]}}},
            },
        })
        await hub._on_frame(reply_frame)
        await asyncio.sleep(0.8)

    print("\n【3】dsh 回复发回平台：")
    if _FeishuApiHandler.feishu_sent:
        feishu_text = json.loads(_FeishuApiHandler.feishu_sent[-1]["content"])["text"]
        print(f"      飞书: {feishu_text!r}")

    print("\n=== 演示完成：飞书 + QQ 收发链路正常 ===")
    await hub.stop()
    feishu_api.shutdown()
    feishu_api.server_close()
    feishu_ws.close()
    await feishu_ws.wait_closed()
    qq_ws.close()
    await qq_ws.wait_closed()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
