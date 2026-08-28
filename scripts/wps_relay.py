"""Standalone WPS 协作 (WOA) webhook relay — for deployments where the bridge
core (which must sit near `dsh web`) is behind NAT but WPS needs a public URL.

Flow:  WPS 平台 --HTTP 回调--> 本中继(公网) --POST /message--> 桥接核心(内网,连着 dsh)
The relay verifies the WPS signature, AES-decrypts and parses the message, then
forwards it to the bridge core's management API as a normal inbound message.
Replies go back out through the bridge core (WPS send is outbound HTTPS, so it
works from behind NAT).

Usage (run on the public server, or via systemd):
    python scripts/wps_relay.py \
        --app-id <App ID> --secret-key <Secret Key> \
        --api-url https://openapi.wps.cn \
        --host 0.0.0.0 --port 8766 --path /webhook \
        --forward http://<内网桥接机器>:8764/message
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dsh_im_bridge.channels.woa import WoaChannel  # noqa: E402
from dsh_im_bridge.events import InboundMessage  # noqa: E402


class _ForwardHub:
    """Pseudo-hub that pushes inbound messages to a remote bridge core."""

    def __init__(self, forward_url: str, channel: str):
        self.forward_url = forward_url
        self.channel = channel

    def enqueue_inbound(self, message: InboundMessage) -> None:
        payload = json.dumps({
            "channel": self.channel,
            "conversation_id": message.conversation_id,
            "text": message.text,
            "sender": message.sender,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.forward_url,
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if not body.get("accepted"):
                    print(f"[relay] bridge core rejected: {body}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[relay] failed to forward to {self.forward_url}: {exc}", file=sys.stderr)


class WpsRelay(WoaChannel):
    """WPS webhook receiver that forwards parsed messages to a bridge core."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.forward_url = config.get("forwardUrl")
        self.forward_channel = config.get("forwardChannel", "woa")
        if not self.forward_url:
            raise ValueError("--forward (bridge core /message URL) is required")
        self.bind(_ForwardHub(self.forward_url, self.forward_channel))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="WPS 协作 (WOA) webhook relay -> bridge core")
    p.add_argument("--app-id", required=True)
    p.add_argument("--secret-key", required=True)
    p.add_argument("--encrypt-key", default=None)
    p.add_argument("--api-url", default="https://openapi.wps.cn")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--path", default="/webhook")
    p.add_argument("--forward", required=True,
                   help="bridge core management API, e.g. http://<内网机器>:8764/message")
    p.add_argument("--forward-channel", default="woa")
    return p


async def _amain(args: argparse.Namespace) -> int:
    config = {
        "appId": args.app_id,
        "secretKey": args.secret_key,
        "encryptKey": args.encrypt_key or "",
        "apiUrl": args.api_url,
        "webhookHost": args.host,
        "webhookPort": args.port,
        "webhookPath": args.path,
        "forwardUrl": args.forward,
        "forwardChannel": args.forward_channel,
    }
    relay = WpsRelay(config)
    await relay.start()

    print(f"\n[wps-relay] WPS webhook relay running.")
    print(f"  listen    : http://{args.host}:{relay._server.bound_port}{args.path}")
    print(f"  forward   : {args.forward}")
    print("  把这个 listen 地址填到 WPS 开放平台的回调地址。Press Ctrl+C to stop.\n")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - windows
            pass
    try:
        await stop.wait()
    finally:
        await relay.stop()
    return 0


def main(argv: list | None = None) -> int:
    args = _build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
