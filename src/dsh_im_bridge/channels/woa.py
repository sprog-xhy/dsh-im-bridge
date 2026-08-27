"""WOA channel.

**Status: needs clarification.** "WOA 协作" is not a standardized messaging
protocol we could find during development. Open hypotheses (see REPORT.md):

* WPS Office Automation / WPS 协作 (WPS 云文档 / 多人协作) — the user runs WPS;
* a company-internal "WOA" platform with its own HTTP/webhook API;
* 企业微信 (WeCom) — sometimes abbreviated in Chinese contexts.

Until clarified, this channel is a thin alias over the generic
:class:`~dsh_im_bridge.channels.webhook.WebhookChannel`: any WOA server that can
POST JSON to a local endpoint can drive the bridge. Once the real API is known,
replace the stub below with a real adapter.
"""

from __future__ import annotations

from typing import Optional

from .webhook import WebhookChannel


class WoaChannel(WebhookChannel):
    name = "woa"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        # default to its own port so woa and webhook can coexist
        self.port = int(config.get("port", 8766))
