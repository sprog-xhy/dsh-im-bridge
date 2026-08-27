"""End-to-end smoke test against a live `dsh web`.

This drives the real DshClient + BridgeHub + ConsoleChannel:

1. connect to dsh, describe the host;
2. create a fresh session in the given workspace/cwd;
3. bind a console conversation to it and send a prompt as if from an IM user;
4. run the mux stream and print the events that get forwarded to the channel;
5. clean up by cancelling the session.

Usage:
  python scripts/e2e_smoke.py [--base http://127.0.0.1:10010] [--cwd D:/...]

Only run against a dsh web you are willing to let create a session.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dsh_im_bridge.channels.console import ConsoleChannel  # noqa: E402
from dsh_im_bridge.dsh_client import DshClient  # noqa: E402
from dsh_im_bridge.hub import BridgeHub, SessionBinding  # noqa: E402


def _arg(name, default):
    import os

    return os.environ.get(name, default)


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:10010")
    p.add_argument("--cwd", default=None)
    p.add_argument("--prompt", default="请只回复“OK”，不要执行任何其他操作。")
    p.add_argument("--wait", type=float, default=120.0)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    client = DshClient(base_url=args.base)
    desc = client.describe()
    print(f"[smoke] dsh reachable: {desc.get('version')} model={desc.get('model')}")

    # 1) create a session
    cwd = args.cwd or desc.get("cwd")
    created = client.create_session(cwd=cwd)
    session_id = created["sessionId"]
    print(f"[smoke] created session {session_id} cwd={cwd}")

    # 2) hub + console channel bound to that session
    out_file = Path.cwd() / "e2e-output.log"
    hub = BridgeHub(client, state_file=Path.cwd() / "e2e-state.json")
    chan = ConsoleChannel({"outputFile": str(out_file)})
    hub.register(chan)
    await hub.start()  # start() loads persisted state; bind AFTER it
    hub._add_binding(SessionBinding("console:default", session_id))
    print(f"[smoke] hub started; output -> {out_file}")

    # 3) prompt through the hub as if a user sent it over IM
    print(f"[smoke] prompting session: {args.prompt!r}")
    await hub._handle_inbound(
        __import__("dsh_im_bridge.events", fromlist=["InboundMessage"]).InboundMessage(
            channel="console", conversation_id="default", text=args.prompt
        )
    )

    # 4) wait for the turn to finish (watch the running flag)
    deadline = time.time() + args.wait
    while time.time() < deadline:
        sessions = {s["sessionId"]: s for s in client.list_sessions()}
        summary = sessions.get(session_id)
        if summary and not summary.get("running") and not summary.get("blank"):
            print(f"[smoke] session finished running (updatedAt={summary.get('updatedAt')})")
            break
        await asyncio.sleep(2)
    else:
        print("[smoke] timed out waiting for session to finish; cancelling")
        try:
            client.cancel(session_id)
        except Exception:  # noqa: BLE001
            pass

    await asyncio.sleep(1)
    await hub.stop()
    print("[smoke] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
