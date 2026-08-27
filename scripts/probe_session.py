"""Probe one session's summary + tail of history via the live dsh API.

Usage: python scripts/probe_session.py <sessionId>
"""
import json
import sys
import urllib.request
import uuid

BASE = "http://127.0.0.1:10010"
SESSION = sys.argv[1]


def call(method, payload):
    body = json.dumps(
        {"type": "client-request", "rpcId": str(uuid.uuid4()), "method": method, "payload": payload}
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/api/{method}", data=body, headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


res = call("session.list", {})
for item in res["result"]["value"]["items"]:
    if item["sessionId"] == SESSION:
        print("summary:", json.dumps(item, ensure_ascii=False, indent=1)[:800])

hist = call("session.history", {"sessionId": SESSION, "maxMessages": 10})
events = hist["result"]["value"]["events"]
for entry in events:
    ev = entry["event"]
    print("EVENT:", ev["type"], "seq=", ev["seq"], "time=", ev["time"])
    data = ev.get("data") or {}
    if ev["type"] in ("user/message", "assistant/message", "tool/result"):
        msg = data.get("message") or data
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") in ("text", "reasoning"):
                    print("   ", b.get("type"), ":", b.get("text", "")[:200])
