"""WOA (WPS 协作) end-to-end demo — no credentials needed.

Simulates the whole flow with a fake WPS platform:
  1. a fake WPS API server (oauth token + /v7/messages/create records sends);
  2. the REAL WoaChannel + BridgeHub (bound to a fake dsh session);
  3. after start, a fake WPS message (signed + AES-encrypted, exactly like the
     real platform) is POSTed to the channel's webhook;
  4. the hub routes it to dsh, the fake dsh replies, and the hub sends the
     reply back to WPS (recorded by the fake API server).

Usage:  python scripts/demo_woa.py
"""
import asyncio
import base64
import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dsh_im_bridge.channels.woa import WoaChannel, _event_signature  # noqa: E402
from dsh_im_bridge.dsh_client import DshError  # noqa: E402
from dsh_im_bridge.hub import BridgeHub, SessionBinding  # noqa: E402
from dsh_im_bridge.parser import parse_mux_frame  # noqa: E402

APP_ID = "demo-app"
SECRET = "demo-secret"
NONCE = "abcdefgh12345678"  # 16 chars


# -- fake WPS API ----------------------------------------------------------
class _FakeWpsHandler(BaseHTTPRequestHandler):
    sent = []

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if self.path == "/oauth2/token":
            self._json({"access_token": "demo-token", "expires_in": 7200})
            return
        if self.path == "/v7/messages/create":
            body = json.loads(raw.decode("utf-8"))
            self.sent.append(body)
            self._json({"code": 0, "msg": "ok", "data": {"message_id": "mid-out"}})
            return
        self._json({"code": 404}, 404)


# -- fake dsh --------------------------------------------------------------
class FakeDsh:
    def prompt(self, session_id, text, mode="queue"):
        print(f"      [dsh] 收到消息: {text!r}")
        print(f"      [dsh] 回复: 已收到「{text}」，这就办。")

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


# -- WPS message builder ----------------------------------------------------
def _encrypt(secret_key: str, nonce: str, plaintext: str) -> str:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = hashlib.md5(secret_key.encode()).hexdigest().encode("utf-8")
    iv = nonce.encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    ct = cipher.encryptor().update(padded) + cipher.encryptor().finalize()
    return base64.b64encode(ct).decode("ascii")


def build_wps_event(text: str, chat_type: str = "p2p") -> dict:
    event = {
        "message": {"id": f"m-{int(time.time()*1000)}", "type": "text",
                    "content": {"text": {"content": text}}},
        "chat": {"id": "oc-demo", "type": chat_type},
        "sender": {"id": "user-demo"},
    }
    if chat_type == "group":
        event["message"]["mentions"] = [{"identity": {"type": "app"}}]
    ts = int(time.time())
    encrypted = _encrypt(SECRET, NONCE, json.dumps(event, ensure_ascii=False))
    sig = _event_signature(APP_ID, SECRET, "kso.app_chat.message", NONCE, ts, encrypted)
    return {"topic": "kso.app_chat.message", "nonce": NONCE, "time": ts,
            "signature": sig, "encrypted_data": encrypted}


async def main() -> int:
    # fake WPS API server
    wps_srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeWpsHandler)
    threading.Thread(target=wps_srv.serve_forever, daemon=True).start()
    wps_port = wps_srv.server_address[1]

    # real WoaChannel + real hub, bound to a fake dsh session
    client = FakeDsh()
    hub = BridgeHub(client, catch_up=False, notify_on_start=False)
    channel = WoaChannel({
        "appId": APP_ID,
        "secretKey": SECRET,
        "apiUrl": f"http://127.0.0.1:{wps_port}",
        "webhookHost": "127.0.0.1",
        "webhookPort": 0,
        "webhookPath": "/webhook",
    })
    hub.register(channel)
    await hub.start()
    hub._add_binding(SessionBinding("woa:p2p:user-demo", "demo-session"))

    webhook_url = f"http://127.0.0.1:{channel._server.bound_port}/webhook"
    print("=== WOA (WPS 协作) 端到端演示 ===\n")
    print(f"  WPS API(假)   : http://127.0.0.1:{wps_port}")
    print(f"  webhook       : {webhook_url}")
    print("  流程: 用户在 WPS 发消息 -> 桥接 -> dsh -> 回复发回 WPS\n")

    print("【1】用户在 WPS 私聊机器人发了一句：帮我看看今天的安排")
    payload = build_wps_event("帮我看看今天的安排", chat_type="p2p")
    import urllib.request

    def _post_webhook():
        req = urllib.request.Request(
            webhook_url, data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    resp = await asyncio.to_thread(_post_webhook)
    print(f"      [wps->bridge] webhook 收到并验签解密: {resp}")

    await asyncio.sleep(1.0)  # let the hub deliver + dsh prompt

    # simulate dsh finishing its turn and replying: inject an assistant/message
    # event so the hub forwards it to the bound WOA conversation.
    reply_frame = parse_mux_frame({
        "type": "server-request",
        "rpcId": "r-reply",
        "method": "session/event",
        "payload": {
            "type": "session/event",
            "sessionId": "demo-session",
            "event": {
                "type": "assistant/message",
                "seq": 2,
                "time": 2000.0,
                "data": {"message": {"role": "assistant",
                                     "content": [{"type": "text", "text": "已收到「帮我看看今天的安排」，马上处理。"}]}},
            },
        },
    })
    await hub._on_frame(reply_frame)
    await asyncio.sleep(1.0)  # let the reply send back to WPS

    print(f"\n【2】桥接已把消息交给 dsh（见上面 [dsh] 输出）")
    print(f"【3】回复发回 WPS：")
    if _FakeWpsHandler.sent:
        out = _FakeWpsHandler.sent[-1]
        print(f"      [bridge->wps] POST /v7/messages/create")
        print(f"      receiver={out.get('receiver')}  text={out['content']['text']['content']!r}")
    else:
        print("      (未捕获到回复发送)")

    print("\n=== 演示完成：WOA 通道收发链路正常 ===")
    await hub.stop()
    wps_srv.shutdown()
    wps_srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
