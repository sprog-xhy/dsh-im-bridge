"""Helper: run a fake WPS API server and report its port (for testing
`dsh-im-bridge --test-notify woa` against the real CLI path).
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
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
        self.rfile.read(n)
        if self.path == "/oauth2/token":
            self._json({"access_token": "test-token", "expires_in": 7200})
        elif self.path == "/v7/messages/create":
            self._json({"code": 0, "msg": "ok", "data": {"message_id": "mid"}})
        else:
            self._json({"code": 404}, 404)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(srv.server_address[1], flush=True)
    sys.stdout.flush()
    threading.Event().wait()  # keep alive
