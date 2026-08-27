"""Demo: watch the full confirmation round-trip without a real agent.

Runs the REAL BridgeHub + ConsoleChannel against a SIMULATED dsh wire that:

1. accepts a prompt (session.prompt);
2. streams assistant chunks, then asks you a question (question/requested);
3. waits while you answer (hub -> /api/respond);
4. emits question/resolved, a final assistant/message and turn/end.

You reply on the terminal with the chat commands (e.g. `/answer 1:方案A`), just
as you would in QQ/Feishu. Optionally auto-answers after a few seconds.

Usage:  python scripts/demo_confirmation.py [--auto 5]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import websockets  # noqa: E402

from dsh_im_bridge.channels.console import ConsoleChannel  # noqa: E402
from dsh_im_bridge.dsh_client import DshClient  # noqa: E402
from dsh_im_bridge.hub import BridgeHub, SessionBinding  # noqa: E402


class FakeDshWire:
    """A tiny dsh lookalike: unary HTTP + mux WS, scripted responses."""

    def __init__(self):
        self.prompt_count = 0
        self.question_rpc_id = "demo-question-0001"
        self.clients = set()
        self.buffered = []
        self.seq = 100

    # -- WS side -----------------------------------------------------------
    async def mux_handler(self, ws):
        self.clients.add(ws)
        try:
            # on connect: baseline subscription markers, then flush buffered frames
            await ws.send(
                json.dumps(
                    {
                        "type": "server-request",
                        "rpcId": "sub-1",
                        "method": "session/subscribed",
                        "payload": {"type": "session/subscribed", "sessionId": "demo-session", "lastSeq": 99},
                    }
                )
            )
            if self.buffered:
                for env in self.buffered:
                    await ws.send(json.dumps(env))
                self.buffered.clear()
            await ws.wait_closed()
        finally:
            self.clients.discard(ws)

    async def push(self, payload):
        """Broadcast one server-request frame; buffer until a client connects."""
        env = {
            "type": "server-request",
            "rpcId": payload.get("rpcId", f"r-{self.seq}"),
            "method": payload.get("type", ""),
            "payload": payload,
        }
        if not self.clients:
            self.buffered.append(env)
            return
        for ws in list(self.clients):
            try:
                await ws.send(json.dumps(env))
            except Exception:  # noqa: BLE001
                pass

    def _ev(self, type_, data):
        self.seq += 1
        return {
            "type": "session/event",
            "sessionId": "demo-session",
            "event": {
                "type": type_,
                "seq": self.seq,
                "time": 1000.0 + self.seq,
                "data": data,
            },
        }

    async def on_prompt(self, text):
        """Simulate an agent that works, then asks the user to confirm."""
        self.prompt_count += 1
        await self.push(self._ev("user/message", {"content": [{"type": "text", "text": text}]}))
        await self.push(self._ev("request/context", {"content": [{"type": "text", "text": "demo context"}]}))
        await self.push(
            self._ev("assistant/message", {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "我发现有两个可行方案，需要你确认选哪个。"}],
                    "source": {"kind": "model"},
                }
            })
        )
        await self.push(
            {
                "type": "question/requested",
                "rpcId": self.question_rpc_id,
                "sessionId": "demo-session",
                "questions": [
                    {
                        "id": "q-plan",
                        "header": "请选择方案",
                        "question": "采用哪个方案继续?",
                        "options": [
                            {"label": "方案A", "description": "快速但不稳定"},
                            {"label": "方案B", "description": "稍慢但稳定"},
                        ],
                    }
                ],
            }
        )

    async def on_question_answered(self):
        await self.push(
            {
                "type": "question/resolved",
                "sessionId": "demo-session",
                "questionRpcId": self.question_rpc_id,
                "outcome": "answered",
            }
        )
        await self.push(
            self._ev("assistant/message", {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "好的，按你的选择继续执行。"}],
                    "source": {"kind": "model"},
                }
            })
        )
        await self.push(
            self._ev("tool/result", {
                "callId": "demo-call-1",
                "content": [{"type": "text", "text": "执行完成，产出已保存。"}],
                "isError": False,
            })
        )
        await self.push(self._ev("turn/end", {}))


class _UnaryHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        wire = self.server.wire  # type: ignore[attr-defined]
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        rpc_id = body.get("rpcId")
        method = body.get("method", "")

        if self.path == "/api/respond":
            result = body.get("result") or {}
            if result.get("ok") and result.get("value", {}).get("questionRpcId") is not None:
                pass  # not the shape we send; ignore
            # question answer: value == {sessionId, answer}
            if result.get("ok") and "answer" in (result.get("value") or {}):
                wire.question_answer = result["value"]["answer"]
                wire.answered = True
                loop = wire.loop
                if loop is not None:
                    asyncio.run_coroutine_threadsafe(wire.on_question_answered(), loop)
                self._json({"accepted": True})
                return
            self._json({"accepted": False, "reason": "bad-response"})
            return

        method = self.path[len("/api/"):]
        value = {"ok": False, "error": {"code": "unknown", "message": f"unhandled {method}"}}
        if method == "host.describe":
            value = {"version": "0.0.1", "cwd": "/tmp/demo", "provider": "demo"}
        elif method == "session.create":
            value = {"sessionId": "demo-session"}
        elif method == "session.prompt":
            value = {"accepted": True}
            text = ""
            content = body.get("payload", {}).get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block.get("text", "")
            if wire.loop is not None:
                asyncio.run_coroutine_threadsafe(wire.on_prompt(text), wire.loop)
        elif method == "session.history":
            value = {"events": [], "hasMore": False}
        elif method == "session.list":
            value = {"items": [{"sessionId": "demo-session", "running": False}]}
        self._json({"type": "server-response", "rpcId": rpc_id, "result": {"ok": True, "value": value}})


class FakeDshServer:
    def __init__(self):
        self.wire = FakeDshWire()
        self.httpd = None
        self.loop = None

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.wire.loop = self.loop
        # HTTP on a random port
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _UnaryHandler)
        self.httpd.wire = self.wire  # type: ignore[attr-defined]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        # WS on a random port
        self.ws_server = await websockets.serve(self.wire.mux_handler, "127.0.0.1", 0)
        http_port = self.httpd.server_address[1]
        ws_port = self.ws_server.sockets[0].getsockname()[1]
        return http_port, ws_port

    async def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.ws_server.close()
        await self.ws_server.wait_closed()


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--auto", type=float, default=0.0, help="auto-answer after N seconds (0 = wait for you to type)")
    p.add_argument("--wait", type=float, default=30.0, help="seconds to run before exiting")
    args = p.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    fake = FakeDshServer()
    http_port, ws_port = await fake.start()

    client = DshClient(
        base_url=f"http://127.0.0.1:{http_port}",
        ws_base=f"ws://127.0.0.1:{ws_port}",
    )
    hub = BridgeHub(client, catch_up=False)
    chan = ConsoleChannel()
    hub.register(chan)
    hub._add_binding(SessionBinding("console:default", "demo-session"))
    await hub.start()

    print("\n=== dsh-im-bridge 确认流程演示 ===\n")
    print("(模拟的 dsh agent 会向你提问，在下面用聊天指令回答)")

    if args.auto > 0:
        async def auto_answer():
            await asyncio.sleep(args.auto)
            print(f"\n[自动演示] {args.auto}s 后自动回答: /answer 1:方案A\n")
            await chan.feed_text("/answer 1:方案A")
        asyncio.create_task(auto_answer())

    await asyncio.sleep(0.6)  # let the mux stream connect first
    await chan.feed_text("开始任务")
    try:
        await asyncio.sleep(args.wait)
    finally:
        await hub.stop()
        await fake.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
