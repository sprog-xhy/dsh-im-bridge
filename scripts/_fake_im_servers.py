"""Helper: start fake Feishu (HTTP webhook) and fake QQ (OneBot WS) servers,
printing their ports for `--test-notify` CLI verification.
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets


class _FeishuHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        data = json.dumps({"code": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


async def _onebot(ws):
    async for raw in ws:
        req = json.loads(raw)
        await ws.send(json.dumps({"status": "ok", "retcode": 0, "echo": req.get("echo")}))


async def main():
    # fake feishu webhook HTTP server
    feishu = ThreadingHTTPServer(("127.0.0.1", 0), _FeishuHandler)
    threading.Thread(target=feishu.serve_forever, daemon=True).start()
    # fake onebot WS server
    onebot = await websockets.serve(_onebot, "127.0.0.1", 0)
    ws_port = onebot.sockets[0].getsockname()[1]
    print(f"FEISHU_PORT={feishu.server_address[1]}", flush=True)
    print(f"QQ_WS_PORT={ws_port}", flush=True)
    sys.stdout.flush()
    try:
        await asyncio.Event().wait()
    finally:
        feishu.shutdown()
        feishu.server_close()
        onebot.close()
        await onebot.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
