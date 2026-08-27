"""Connect to the dsh web mux WebSocket and print frames for a few seconds.

Usage:  python scripts/probe_dsh_mux.py [base_url] [seconds]
Default base_url: http://127.0.0.1:10010
"""
import asyncio
import json
import sys

import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:10010"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
WS = BASE.replace("http://", "ws://").replace("https://", "wss://")


async def main():
    uri = f"{WS}/api/events.mux"
    print(f"connecting to {uri} ...")
    try:
        async with websockets.connect(uri, open_timeout=10) as ws:
            print("connected")
            try:
                async with asyncio.timeout(SECONDS):
                    async for raw in ws:
                        try:
                            frame = json.loads(raw)
                        except json.JSONDecodeError:
                            print("RAW:", raw[:300])
                            continue
                        kind = frame.get("payload", {}).get("type", frame.get("type"))
                        print(f"[{kind}] {json.dumps(frame, ensure_ascii=False)[:600]}")
            except TimeoutError:
                print("...time window elapsed, closing")
    except Exception as e:  # noqa: BLE001
        print("ERROR:", repr(e))


if __name__ == "__main__":
    asyncio.run(main())
