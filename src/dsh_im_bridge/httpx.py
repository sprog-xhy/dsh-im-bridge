"""A minimal JSON HTTP server for local bridge endpoints.

Runs ``http.server.ThreadingHTTPServer`` in a daemon thread and bridges
requests into the asyncio loop. Kept deliberately tiny: no TLS/auth (local
loopback only by default), JSON in / JSON out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("dsh_im_bridge.http")

Handler = Callable[[dict], Awaitable[Any]]


class _Handler(BaseHTTPRequestHandler):
    server_version = "dsh-im-bridge/0.1"
    # silence default stderr logging
    def log_message(self, fmt, *args):  # noqa: A003
        log.debug("http: " + fmt, *args)

    def _handle(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            payload: Any = {}
            if body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    self._reply(400, {"error": "invalid JSON"})
                    return
            app: "MinimalHttpServer" = self.server.app  # type: ignore[attr-defined]
            coro = app.route(self.path, self.command, payload)
            if asyncio.iscoroutine(coro):
                fut = asyncio.run_coroutine_threadsafe(coro, app.loop)
                result = fut.result(timeout=30)
            else:
                result = coro
            self._reply(200, result)
        except Exception as exc:  # noqa: BLE001
            log.exception("http handler error: %s", exc)
            self._reply(500, {"error": str(exc)})

    def do_POST(self):  # noqa: N802
        self._handle()

    def do_GET(self):  # noqa: N802
        self._handle()

    def _reply(self, status: int, body: Any) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class MinimalHttpServer:
    """Thread-backed JSON HTTP server with an async route callback."""

    def __init__(
        self,
        host: str,
        port: int,
        loop: asyncio.AbstractEventLoop,
        route: Handler,
    ):
        self.host = host
        self.port = port
        self.loop = loop
        self.route = route
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def bound_port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else self.port

    def start(self) -> None:
        self._httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._httpd.app = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        log.info("http server listening on %s:%d", self.host, self.bound_port)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @classmethod
    def respond_into_loop(cls, loop: asyncio.AbstractEventLoop, coro, callback=None):
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        if callback is not None:
            fut.add_done_callback(lambda f: callback(f.result() if not f.exception() else None))
