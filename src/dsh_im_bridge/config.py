"""Configuration loading for the bridge.

Configuration comes from a YAML file (``--config``) with environment-variable
overrides (``DSH_IM_BRIDGE_*``). Secret-ish fields (tokens) prefer env vars.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - dev only
    yaml = None

DEFAULT_CONFIG = {
    "dsh": {"baseUrl": "http://127.0.0.1:10010"},
    "bridge": {"apiPort": 8764, "host": "127.0.0.1"},
    "channels": {
        "console": {"enabled": True},
        "webhook": {"enabled": True, "port": 8765},
        "feishu": {"enabled": False},
        "qq": {"enabled": False, "wsUrl": "ws://127.0.0.1:3001"},
        "woa": {"enabled": False, "port": 8766},
    },
}

# Event types forwarded to bound IM conversations when `forwardEvents` is not
# configured. Keeping these in sync with `hub.DEFAULT_FORWARD_EVENTS`:
# `step/end` is intentionally excluded (fires every step; `turn/end` marks done).
DEFAULT_FORWARD_EVENTS = frozenset(
    {"user/message", "assistant/message", "tool/result", "turn/end"}
)


@dataclasses.dataclass
class Config:
    dsh_base_url: str
    bridge_host: str
    bridge_api_port: int
    state_file: Optional[Path]
    default_workspace_id: Optional[str]
    default_cwd: Optional[str]
    agent_preset: Optional[str]
    forward_events: frozenset
    max_message_chars: int
    catch_up: bool
    catch_up_max_events: int
    notify_on_start: bool
    send_retries: int
    send_retry_delay: float
    channels: dict  # channel name -> config dict


    def validate(self) -> tuple[list[str], list[str]]:
        """Return (errors, warnings) for the current configuration.

        Errors block a useful start; warnings are informational.
        """
        from .channels import available_channels

        errors: list[str] = []
        warnings: list[str] = []

        if self.default_workspace_id and self.default_cwd:
            errors.append(
                "bridge: set only one of defaultWorkspaceId or defaultCwd (dsh session.create rejects both)"
            )
        known = set(available_channels())
        for name in self.channels:
            if name not in known:
                errors.append(f"channel: unknown channel {name!r} (known: {', '.join(sorted(known))})")

        feishu = self.channels.get("feishu") or {}
        if feishu.get("webhookUrl"):
            if feishu.get("appId") or feishu.get("appSecret"):
                warnings.append("feishu: both webhookUrl and appId are set; webhook (send-only) takes precedence")
        elif feishu.get("appId") and not feishu.get("appSecret"):
            errors.append("feishu: appId set but appSecret missing (app bot needs both)")
        elif feishu.get("appSecret") and not feishu.get("appId"):
            errors.append("feishu: appSecret set but appId missing (app bot needs both)")
        elif self.channels.get("feishu") and not feishu.get("appId") and not feishu.get("appSecret"):
            warnings.append("feishu: enabled but has neither webhookUrl nor appId/appSecret — it will not connect")

        if self.channels.get("qq") and not self.channels["qq"].get("wsUrl"):
            errors.append("qq: wsUrl is required (OneBot11 reverse-WebSocket endpoint)")

        woa = self.channels.get("woa") or {}
        if woa:
            if not woa.get("appId") or not woa.get("secretKey"):
                errors.append("woa: appId and secretKey are required (WPS 开放平台应用)")

        if not self.channels:
            warnings.append("no channels enabled; only the bridge management API will run")

        if self.forward_events and not self.forward_events.intersection(
            {"assistant/message", "turn/end", "user/message", "tool/result", "step/end"}
        ):
            warnings.append(f"bridge.forwardEvents contains unknown event types: {sorted(self.forward_events)}")

        return errors, warnings


def _merge(base: dict, overrides: dict) -> dict:
    out = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"DSH_IM_BRIDGE_{name}", default)


def load_dotenv(path: Optional[str] = None) -> None:
    """Load a simple ``.env`` file into the environment (without overriding).

    Tiny dependency-free parser: lines ``KEY=VALUE``, ``#`` comments, blank
    lines ignored. Existing environment variables take precedence. Falls back
    to ``<cwd>/.env`` when no path is given.
    """
    target = path if path is not None else os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(target):
        return
    try:
        with open(target, encoding="utf-8-sig") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def load_config(path: Optional[str] = None) -> Config:
    load_dotenv()
    data = _merge(DEFAULT_CONFIG, {})
    if path:
        if yaml is None:
            raise RuntimeError("PyYAML is required to read a config file")
        with open(path, encoding="utf-8") as fh:
            data = _merge(data, yaml.safe_load(fh) or {})

    channels = data.get("channels") or {}
    enabled_channels = {}
    for name, cfg in channels.items():
        if isinstance(cfg, dict) and cfg.get("enabled"):
            # env overrides for secrets / urls
            clone = dict(cfg)
            if name == "feishu":
                clone["baseUrl"] = _env("FEISHU_BASE_URL", clone.get("baseUrl", "https://open.feishu.cn"))
                clone["webhookUrl"] = _env("FEISHU_WEBHOOK_URL", clone.get("webhookUrl", "")) or None
                clone["appId"] = _env("FEISHU_APP_ID", clone.get("appId", "")) or None
                clone["appSecret"] = _env("FEISHU_APP_SECRET", clone.get("appSecret", "")) or None
                clone["encryptKey"] = _env("FEISHU_ENCRYPT_KEY", clone.get("encryptKey", "")) or None
            elif name == "qq":
                clone["wsUrl"] = _env("QQ_WS_URL", clone.get("wsUrl", "ws://127.0.0.1:3001"))
                clone["accessToken"] = _env("QQ_ACCESS_TOKEN", clone.get("accessToken", "")) or None
            elif name == "woa":
                clone["appId"] = _env("WOA_APP_ID", clone.get("appId", "")) or None
                clone["secretKey"] = _env("WOA_SECRET_KEY", clone.get("secretKey", "")) or None
                clone["encryptKey"] = _env("WOA_ENCRYPT_KEY", clone.get("encryptKey", "")) or None
                clone["apiUrl"] = _env("WOA_API_URL", clone.get("apiUrl", "https://openapi.wps.cn"))
            enabled_channels[name] = clone

    bridge = data.get("bridge") or {}
    dsh = data.get("dsh") or {}
    state_file = None
    raw_state = bridge.get("stateFile")
    if raw_state:
        state_file = Path(raw_state)
    return Config(
        dsh_base_url=_env("DSH_BASE_URL", dsh.get("baseUrl", "http://127.0.0.1:10010")),
        bridge_host=bridge.get("host", "127.0.0.1"),
        bridge_api_port=int(bridge.get("apiPort", 8764)),
        state_file=state_file,
        default_workspace_id=bridge.get("defaultWorkspaceId"),
        default_cwd=bridge.get("defaultCwd"),
        agent_preset=bridge.get("agentPreset"),
        forward_events=(
            frozenset(bridge["forwardEvents"])
            if bridge.get("forwardEvents")
            else DEFAULT_FORWARD_EVENTS
        ),
        max_message_chars=int(bridge.get("maxMessageChars", 2000)),
        catch_up=bool(bridge.get("catchUp", True)),
        catch_up_max_events=int(bridge.get("catchUpMaxEvents", 200)),
        notify_on_start=bool(bridge.get("notifyOnStart", True)),
        send_retries=int(bridge.get("sendRetries", 2)),
        send_retry_delay=float(bridge.get("sendRetryDelay", 1.0)),
        channels=enabled_channels,
    )
