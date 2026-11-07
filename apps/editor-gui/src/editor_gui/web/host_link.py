"""The link to the program that started the editor: Home Assistant's integration.

The integration signs in like its proxy does (``/login``) and opens the websocket ``/host``. Both
sides send JSON objects with a ``type``; one peer at a time (a new one replaces the old). Messages:

- integration → editor
  - ``{"type": "hello", "protocol": 1, "integration": "<version>", "home_assistant": "<version>"}``
  - ``{"type": "knx", "loaded": bool, "project": {name, guid, tool_version, created_by,
    last_modified} | None}``: the state of Home Assistant's KNX integration and the project it
    holds, at the start and whenever it changes.
  - ``{"type": "result", "id": int, "error": str | None}``: the answer to a request of the editor;
    after a ``set_project`` the ``knx`` message with the new project comes first.
- editor → integration
  - ``{"type": "hello", "protocol": 1, "editor": "<version>"}``
  - ``{"type": "set_project", "id": int, "project": {...}}``: the project, in xknxproject's
    ``KNXProject`` format, becomes the KNX integration's project.

Messages from the peer are collected for the GUI thread (:meth:`HostLink.receive`); the GUI thread
sends from its own thread (:meth:`HostLink.send`), serialized off the GUI thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any, cast

from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

PROTOCOL = 1
# Closes the connection a new peer replaces.
_CLOSE_REPLACED = 4409


class HostLink:
    """The integration's connection: messages both ways, one peer at a time."""

    def __init__(self, wake: Callable[[], None] | None = None) -> None:
        # Wakes an idle GUI thread when a message arrives.
        self._wake = wake
        self._lock = threading.Lock()
        self._inbox: deque[dict[str, Any]] = deque()
        self._socket: WebSocket | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outbox: asyncio.Queue[dict[str, Any]] | None = None
        # Increases with every new peer, so its state can be asked for again.
        self.generation = 0

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._socket is not None

    def receive(self) -> list[dict[str, Any]]:
        """The messages that arrived since the last call (GUI thread)."""
        with self._lock:
            messages = list(self._inbox)
            self._inbox.clear()
        return messages

    def send(self, message: dict[str, Any]) -> bool:
        """Send ``message`` (from any thread); False without a peer. The message is serialized on
        the server's thread, so it must not change after the call."""
        with self._lock:
            loop, outbox = self._loop, self._outbox
            if self._socket is None or loop is None or outbox is None:
                return False
        loop.call_soon_threadsafe(outbox.put_nowait, message)
        return True

    async def serve(self, socket: WebSocket, hello: dict[str, Any]) -> None:
        """Talk to ``socket`` (accepted) until it closes; ``hello`` is sent first."""
        outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._lock:
            previous = self._socket
            self._socket, self._loop, self._outbox = (
                socket,
                asyncio.get_running_loop(),
                outbox,
            )
            self.generation += 1
            self._inbox.clear()
        if previous is not None:
            with contextlib.suppress(Exception):
                await previous.close(code=_CLOSE_REPLACED)
        logger.info("host link connected")
        outbox.put_nowait(hello)
        sender = asyncio.create_task(self._send(socket, outbox))
        try:
            while True:
                self._received(await socket.receive_text())
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
            with self._lock:
                if self._socket is socket:
                    self._socket = self._loop = self._outbox = None
                    self.generation += 1
            logger.info("host link closed")
            self._notify()

    async def _send(
        self, socket: WebSocket, outbox: asyncio.Queue[dict[str, Any]]
    ) -> None:
        while True:
            message = await outbox.get()
            # A project is megabytes of JSON: not on the server's loop.
            text = await asyncio.to_thread(json.dumps, message, separators=(",", ":"))
            await socket.send_text(text)

    def _received(self, text: str) -> None:
        try:
            message: object = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, dict) or "type" not in message:
            return
        with self._lock:
            self._inbox.append(cast("dict[str, Any]", message))
        self._notify()

    def _notify(self) -> None:
        if self._wake is not None:
            self._wake()
