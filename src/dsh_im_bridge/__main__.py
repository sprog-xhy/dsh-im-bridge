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
    p.add_argument("--log-file", default=None, help="also append logs to this file (useful for hidden Windows service runs)")
    p.add_argument("--data-dir", default=None, help="directory for bridge state (default: <cwd>/.dsh-im-bridge)")
    p.add_argument("--check", action="store_true", help="run self-diagnostics and exit (no server)")
    return p


async def _check(config, config_path) -> int:
    """Self-diagnostics: validate config, probe dsh, then exit."""
    import websockets

    from .channels import available_channels

    errors, warnings = config.validate()
    ok = True

    def line(text: str, status: str = "ok") -> None:
        nonlocal ok
        mark = {"ok": "✅", "warn": "⚠️", "err": "❌"}.get(status, "•")
        if status == "err":
            ok = False
        print(f"  {mark} {text}")

    print("[dsh-im-bridge] self-check")
    print(f"  config file : {config_path or 'built-in defaults (no --config)'}")

    print("\n  -- config --")
    if not errors and not warnings:
        line("configuration looks good")
    for e in errors:
        line(e, "err")
    for w in warnings:
        line(w, "warn")

    print("\n  -- dsh connectivity --")
    client = DshClient(base_url=config.dsh_base_url)
    try:
        desc = client.describe()
        line(
            f"HTTP /api reachable: version={desc.get('version')} model={desc.get('provider')}/{desc.get('model')}",
        )
    except Exception as exc:  # noqa: BLE001
        line(f"cannot reach dsh at {config.dsh_base_url}: {exc}", "err")
    else:
        try:
            async with websockets.connect(
                client._ws_base + "/api/events.mux", open_timeout=5
            ) as ws:
                line("mux event stream (WS) connects")
                # read one frame to prove frames flow
                import asyncio

                try:
                    await asyncio.wait_for(ws.recv(), timeout=5)
                    line("mux delivers frames")
                except asyncio.TimeoutError:
                    line("mux connected but no frame within 5s (may be idle)", "warn")
        except Exception as exc:  # noqa: BLE001
            line(f"mux WebSocket failed: {exc}", "err")

    print("\n  -- channels --")
    known = set(available_channels())
    if not config.channels:
        line("no channels enabled", "warn")
    for name, cfg in config.channels.items():
        if name not in known:
            continue
        if name == "feishu":
            mode = "webhook" if cfg.get("webhookUrl") else ("app-bot" if cfg.get("appId") else "UNCONFIGURED")
            line(f"feishu: {mode}", "warn" if mode == "UNCONFIGURED" else "ok")
        elif name == "qq":
            line(f"qq: OneBot at {cfg.get('wsUrl')} (needs a running NapCat/Lagrange/LLOneBot)", "ok")
        else:
            line(f"{name}: enabled")

    print("\n  -- state --")
    try:
        state_dir = Path(config.state_file).parent if config.state_file else Path.cwd() / ".dsh-im-bridge"
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        line(f"state directory writable: {state_dir}")
    except Exception as exc:  # noqa: BLE001
        line(f"state directory not writable: {exc}", "err")

    print()
    if ok:
        print("Result: OK — you can start the bridge (python -m dsh_im_bridge).")
    else:
        print("Result: problems found above; fix config or dsh connectivity first.")
    return 0 if ok else 1


async def _amain(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.dsh:
        config.dsh_base_url = args.dsh
    if args.api_port:
        config.bridge_api_port = args.api_port

    errors, warnings = config.validate()
    for w in warnings:
        logging.warning("config: %s", w)
    if errors:
        for e in errors:
            logging.error("config: %s", e)
        print("配置有误，无法启动。运行 `python -m dsh_im_bridge --check` 查看详情。", file=sys.stderr)
        return 2

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
        notify_on_start=config.notify_on_start,
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
    _configure_logging(args)
    try:
        if args.check:
            config = load_config(args.config)
            if args.dsh:
                config.dsh_base_url = args.dsh
            return asyncio.run(_check(config, args.config))
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 0


def _configure_logging(args) -> None:
    handlers = [logging.StreamHandler()]
    if args.log_file:
        try:
            Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
        except OSError as exc:  # pragma: no cover - filesystem dependent
            print(f"warning: cannot open log file {args.log_file!r}: {exc}", file=sys.stderr)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


if __name__ == "__main__":
    sys.exit(main())
