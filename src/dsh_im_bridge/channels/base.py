"""Channel abstraction: one IM / collaboration adapter."""

from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, Optional

from ..events import InboundMessage

if TYPE_CHECKING:
    from ..hub import BridgeHub

log = logging.getLogger("dsh_im_bridge.channel")


class Channel(abc.ABC):
    """A messaging channel that can send to conversations and receive messages.

    Subclasses override :meth:`start` (connect / begin listening), :meth:`stop`
    and :meth:`send`. To deliver an inbound user message, a channel calls
    ``self.deliver(InboundMessage(...))`` which routes it to the hub.
    """

    name: str = "base"

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.hub: Optional[BridgeHub] = None
        self.log = logging.getLogger(f"dsh_im_bridge.channel.{self.name}")

    def bind(self, hub: "BridgeHub") -> None:
        self.hub = hub

    def deliver(self, message: InboundMessage) -> None:
        if self.hub is None:
            self.log.warning("dropping inbound message (hub not bound): %s", message.text[:80])
            return
        self.hub.enqueue_inbound(message)

    @abc.abstractmethod
    async def start(self) -> None:
        """Connect to the IM service and start receiving messages."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Tear down connections gracefully."""

    @abc.abstractmethod
    async def send(self, conversation_id: str, text: str, kind: str = "notify") -> None:
        """Deliver one outbound text message to a conversation."""

    # -- helpers -----------------------------------------------------------
    async def start_ok(self) -> None:
        self.log.info("channel %r started", self.name)

    async def stop_ok(self) -> None:
        self.log.info("channel %r stopped", self.name)
