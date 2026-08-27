"""Console channel: a local terminal that both sends and receives messages.

This is the simplest channel and doubles as the testing/demo channel. Outbound
messages are printed to stdout; inbound lines come from stdin (or, in tests,
from a programmatic queue).

Outbound can also be piped to a file via config ``outputFile``, which is handy
for demos that run unattended.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Optional

from ..events import InboundMessage
from .base import Channel


class ConsoleChannel(Channel):
    name = "console"

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self._out_file = self.config.get("outputFile")
        self._out_handle = None
        self._reader_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._out_handle = None
        if self._out_file:
            self._out_handle = await asyncio.to_thread(
                open, self._out_file, "a", encoding="utf-8"
            )
        self._reader_task = asyncio.create_task(self._read_stdin(), name="console-reader")
        await self.start_ok()

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader_task = None
        if self._out_handle is not None:
            await asyncio.to_thread(self._out_handle.close)
            self._out_handle = None
        await self.stop_ok()

    async def _read_stdin(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":
                break
            line = line.rstrip("\n")
            if not line.strip():
                continue
            await self.feed_text(line)

    async def feed_text(self, text: str, sender: str = "console-user") -> None:
        """Programmatic inbound (used by tests and the demo)."""
        self.deliver(
            InboundMessage(
                channel=self.name,
                conversation_id="default",
                text=text,
                sender=sender,
            )
        )

    async def _emit(self, text: str) -> None:
        if self._out_handle is not None:
            await asyncio.to_thread(self._out_handle.write, text + "\n")
            await asyncio.to_thread(self._out_handle.flush)
        else:
            self._write_stdout(text)

    @staticmethod
    def _write_stdout(text: str) -> None:
        """Write to stdout as UTF-8, never crashing on emoji/multibyte.

        ``print()`` on Windows uses the console codepage (often GBK) and raises
        UnicodeEncodeError on emoji; writing the binary buffer as UTF-8 keeps
        the bridge alive on Windows while behaving normally on Ubuntu.
        """
        try:
            sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
            sys.stdout.buffer.flush()
        except (AttributeError, OSError):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            print(text, flush=True)

    async def send(self, conversation_id: str, text: str, kind: str = "notify") -> None:
        prefix = {
            "notify": "",
            "event": "",
            "question": "",
            "approval": "",
            "answer": "> ",
            "error": "!! ",
        }.get(kind, "")
        await self._emit(f"{prefix}{text}")
