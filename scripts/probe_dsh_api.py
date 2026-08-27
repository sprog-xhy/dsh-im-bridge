"""Probe the running dsh web `/api` from a loopback non-browser client.

Usage:  python scripts/probe_dsh_api.py [base_url]
Default base_url: http://127.0.0.1:10010
"""
import json
import sys
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:10010"


def call(method, payload):
    body = json.dumps(
        {
            "type": "client-request",
            "rpcId": str(uuid.uuid4()),
            "method": method,
            "payload": payload,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/{method}",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, repr(e)


def main():
    for method, payload in [
        ("host.describe", {}),
        ("session.list", {}),
        ("workspace.list", {}),
    ]:
        status, resp = call(method, payload)
        print(f"{method} -> HTTP {status}")
        print(json.dumps(resp, ensure_ascii=False)[:800])
        print("---")


if __name__ == "__main__":
    main()
