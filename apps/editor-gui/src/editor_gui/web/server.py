"""The web server of the browser UI: one page, its script and a WebSocket per browser.

The GUI thread publishes frames, texture messages and clipboard text through :class:`WebHub`;
the server thread (its own asyncio loop, run by uvicorn) forwards them to every browser and queues
the browsers' input for the GUI thread. A browser that falls behind skips frames, never texture
messages. Access needs the session token, passed as ``?token=`` in the page address."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from editor_gui.web.frames import KIND_CLIPBOARD, TextureMirror, message

logger = logging.getLogger(__name__)

_STATIC = Path(__file__).parent / "static"
_PAGES = {"/": "index.html", "/client.js": "client.js"}
_MEDIA = {"index.html": "text/html; charset=utf-8", "client.js": "text/javascript"}


@dataclass(eq=False)
class _Client:
    """One connected browser: its ordered messages and the latest frame not sent yet."""

    socket: WebSocket
    ordered: deque[bytes] = field(default_factory=lambda: deque[bytes]())
    frame: bytes | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)


class WebHub:
    """Between the GUI thread and the browsers."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token or secrets.token_urlsafe(24)
        self.textures = TextureMirror()
        self.clipboard = ""
        self.last_input = 0.0
        # Set when input arrives, to wake an idle GUI thread.
        self.wake = threading.Event()
        self.size: tuple[float, float] | None = None
        # The browser's device pixel ratio: ImGui renders its fonts at that density.
        self.scale = 1.0
        self._input: deque[dict[str, Any]] = deque()
        self._clients: set[_Client] = set()
        self._frame: bytes | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # GUI thread

    def publish(self, ordered: list[bytes], frame: bytes | None) -> None:
        """Send texture or clipboard messages (all of them, in order) and the latest frame."""
        if frame is not None:
            self._frame = frame
        if (ordered or frame is not None) and self._loop is not None:
            self._loop.call_soon_threadsafe(self._dispatch, ordered, frame)

    def publish_clipboard(self, text: str) -> None:
        self.clipboard = text
        self.publish([message(KIND_CLIPBOARD, text.encode("utf-8"))], None)

    def take_input(self) -> list[dict[str, Any]]:
        """The browser input since the last call, oldest first."""
        events: list[dict[str, Any]] = []
        while self._input:
            events.append(self._input.popleft())
        return events

    @property
    def connected(self) -> bool:
        return bool(self._clients)

    # server thread

    def _dispatch(self, ordered: list[bytes], frame: bytes | None) -> None:
        for client in self._clients:
            client.ordered.extend(ordered)
            if frame is not None:
                client.frame = frame
            client.wake.set()

    def _authorized(self, token: str | None) -> bool:
        return token is not None and secrets.compare_digest(token, self.token)

    async def _page(self, request: Request) -> Response:
        name = _PAGES[request.url.path]
        # The script holds no data; the page and the WebSocket need the token.
        if name != "client.js" and not self._authorized(
            request.query_params.get("token")
        ):
            return PlainTextResponse("Forbidden", status_code=403)
        return FileResponse(
            _STATIC / name,
            media_type=_MEDIA[name],
            headers={"Cache-Control": "no-store"},
        )

    async def _socket(self, socket: WebSocket) -> None:
        if not self._authorized(socket.query_params.get("token")):
            await socket.close(code=4403)
            return
        await socket.accept()
        client = _Client(socket)
        client.ordered.extend(self.textures.snapshot())
        client.frame = self._frame
        client.wake.set()
        self._clients.add(client)
        self.wake.set()  # a new browser needs a frame now
        sender = asyncio.create_task(self._send(client))
        try:
            while True:
                text = await socket.receive_text()
                self._receive(text)
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.discard(client)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender

    async def _send(self, client: _Client) -> None:
        while True:
            await client.wake.wait()
            client.wake.clear()
            while client.ordered:
                await client.socket.send_bytes(client.ordered.popleft())
            frame, client.frame = client.frame, None
            if frame is not None:
                await client.socket.send_bytes(frame)

    def _receive(self, text: str) -> None:
        try:
            events: object = json.loads(text)
        except ValueError:
            return
        if not isinstance(events, list):
            return
        for item in cast("list[object]", events):
            if isinstance(item, dict):
                event = cast("dict[str, Any]", item)
                if event.get("t") == "size":
                    self.size = (float(event["w"]), float(event["h"]))
                    self.scale = float(event.get("r", 1.0)) or 1.0
                elif event.get("t") == "clip":
                    self.clipboard = str(event.get("text", ""))
                self._input.append(event)
        self.last_input = time.monotonic()
        self.wake.set()

    def app(self) -> Starlette:
        routes: list[Any] = [Route(path, self._page) for path in _PAGES]
        routes.append(WebSocketRoute("/ws", self._socket))
        return Starlette(routes=routes)

    def start(self, host: str, port: int) -> threading.Thread:
        """Serve on ``host``:``port`` from a daemon thread."""
        config = uvicorn.Config(
            self.app(),
            host=host,
            port=port,
            log_level="warning",
            ws="websockets-sansio",
        )
        server = uvicorn.Server(config)
        ready = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_until_complete(server.serve())

        thread = threading.Thread(target=run, name="web-ui", daemon=True)
        thread.start()
        ready.wait()
        return thread
