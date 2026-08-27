"""Run the bridge: ``python -m dsh_im_bridge [--config config.yaml]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from .channels import create_channel
from .config import load_config
from .dsh_client import DshClient
from .hub import BridgeHub
from .server import BridgeServer


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dsh-im-bridge", description="Bridge dsh agents to IM tools")
    p.add_argument("--config", default=None, help="path to a YAML config file")
    p.add_argument("--dsh", default=None, help="dsh web base URL (overrides config)")
    p.add_argument("--api-port", type=int, default=None, help="bridge management API port")
    p.add_argument("--verbose", action="store_true", help="debug logging")
    p.add_argument("--data-dir", default=None, help="directory for bridge state (default: <cwd>/.dsh-im-bridge)")
    return p


async def _amain(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.dsh:
        config.dsh_base_url = args.dsh
    if args.api_port:
        config.bridge_api_port = args.api_port

    data_dir = Path(args.data_dir) if args.data_dir else Path.cwd() / ".dsh-im-bridge"
    state_file = config.state_file or (data_dir / "bridge-state.json")

    client = DshClient(base_url=config.dsh_base_url)
    hub = BridgeHub(
        client,
        state_file=state_file,
        forward_events=config.forward_events,
        max_message_chars=config.max_message_chars,
        default_workspace_id=config.default_workspace_id,
        default_cwd=config.default_cwd,
        agent_preset=config.agent_preset,
        catch_up=config.catch_up,
        catch_up_max_events=config.catch_up_max_events,
    )

    if not config.channels:
        logging.warning("no channels enabled; only the bridge API will be available")

    for name, cfg in config.channels.items():
        channel = create_channel(name, cfg)
        hub.register(channel)
        logging.info("enabled channel: %s", name)

    server = BridgeServer(hub, host=config.bridge_host, port=config.bridge_api_port)

    stop = asyncio.Event()

    def _request_stop(*_args):
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover - windows
            pass

    await server.start()
    await hub.start()

    print(
        "\n[dsh-im-bridge] running."
        "\n  dsh        : %s"
        "\n  bridge API : http://%s:%d/status"
        "\n  state      : %s"
        "\n  channels   : %s"
        "\nPress Ctrl+C to stop.\n"
        % (
            config.dsh_base_url,
            config.bridge_host,
            server.bound_port,
            state_file,
            ", ".join(hub.channels) or "(none)",
        )
    )

    try:
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logging.info("shutting down ...")
        await hub.stop()
        await server.stop()
    return 0


def main(argv: list | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # Windows consoles default to the local codepage (e.g. GBK) and crash on
    # emoji; force UTF-8 with replacement so the bridge never dies printing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
