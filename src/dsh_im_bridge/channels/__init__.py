"""Channel registry and factory."""

from __future__ import annotations

from typing import Optional

from .base import Channel


def available_channels() -> dict:
    """Map channel name -> Channel subclass."""
    from .console import ConsoleChannel
    from .feishu import FeishuChannel
    from .qq import QQOneBotChannel
    from .webhook import WebhookChannel
    from .woa import WoaChannel

    return {
        "console": ConsoleChannel,
        "feishu": FeishuChannel,
        "qq": QQOneBotChannel,
        "webhook": WebhookChannel,
        "woa": WoaChannel,
    }


def create_channel(name: str, config: Optional[dict] = None) -> Channel:
    table = available_channels()
    if name not in table:
        raise ValueError(
            f"unknown channel {name!r}; available: {', '.join(sorted(table))}"
        )
    return table[name](config or {})
