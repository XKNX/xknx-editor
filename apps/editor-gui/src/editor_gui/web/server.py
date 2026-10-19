"""The web server of the browser UI: the page, its script, file transfers and a WebSocket per browser.

The GUI thread publishes frames, texture messages and page events through :class:`WebHub`; the
server thread (its own asyncio loop, run by uvicorn) forwards them to the browsers and queues the
input of the controlling browser for the GUI thread.

- **Access** needs the session token: ``?token=`` in the address once, which the server swaps for an
  HttpOnly cookie, or the sign-in form. WebSockets and uploads from another site are refused
  (Origin check), so the cookie cannot be used by foreign pages.
- **Control**: one browser controls the app; any other one watches and can take over.
- **Frames**: every browser gets only the draw lists it does not have from its previous frame. A
  browser that falls behind skips frames, never texture messages or page events."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import json
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.requests import HTTPConnection, Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from editor_gui.strings import S
from editor_gui.web.files import Downloads, FileFolder, watch_written, zip_folder
from editor_gui.web.frames import (
    KIND_CLIPBOARD,
    KIND_EVENT,
    KIND_FRAME,
    Frame,
    TextureMirror,
    message,
)

if TYPE_CHECKING:
    from editor_gui.web.dialogs import WebDialog

logger = logging.getLogger(__name__)

_STATIC = Path(__file__).parent / "static"
_COOKIE = "xknx_web"
_MAX_UPLOAD = 2 * 1024**3


@dataclass(eq=False)
class _Client:
    """One connected browser: its ordered messages and the latest frame not sent yet."""

    socket: WebSocket
    ordered: deque[bytes] = field(default_factory=lambda: deque[bytes]())
    frame: Frame | None = None
    # The draw lists of the last frame this browser got (it keeps exactly those).
    known: frozenset[bytes] = frozenset()
    wake: asyncio.Event = field(default_factory=asyncio.Event)


def event_message(event: dict[str, Any]) -> bytes:
    return message(KIND_EVENT, json.dumps(event).encode("utf-8"))


_PAGE_TEXTS = (
    "WEB_RECONNECTING",
    "WEB_DENIED",
    "WEB_UPLOAD",
    "WEB_DROP_HINT",
    "WEB_SERVER_FILES",
    "WEB_NO_FILES",
    "WEB_OPEN",
    "WEB_SAVE",
    "WEB_SELECT",
    "WEB_FILE_NAME",
    "WEB_FOLDER_NAME",
    "WEB_FILE_TYPE",
    "WEB_DOWNLOAD_AFTER",
    "WEB_UPLOADING",
    "WEB_UPLOAD_FAILED",
    "WEB_FILE_READY",
    "WEB_DOWNLOAD",
    "WEB_SHOW",
    "WEB_FOLDER_TITLE",
    "WEB_VIEW_ONLY",
    "WEB_TAKE_CONTROL",
    "WEB_OPEN_LINK",
    "BTN_CANCEL",
    "BTN_CLOSE",
)


def page_texts() -> dict[str, str]:
    """The page's own texts in the app's language."""
    return {name: str(getattr(S, name)) for name in _PAGE_TEXTS}


class WebHub:
    """Between the GUI thread and the browsers."""

    def __init__(self, token: str, files: Path) -> None:
        self.token = token
        self.textures = TextureMirror()
        self.files = FileFolder(files)
        self.downloads = Downloads()
        self.clipboard = ""
        self.last_input = 0.0
        # Set when input arrives, to wake an idle GUI thread.
        self.wake = threading.Event()
        self.size: tuple[float, float] | None = None
        # The controlling browser's device pixel ratio: ImGui renders its fonts at that density.
        self.scale = 1.0
        self._input: deque[dict[str, Any]] = deque()
        self._clients: list[_Client] = []
        self._controller: _Client | None = None
        self._frame: Frame | None = None
        self._dialogs: dict[int, WebDialog] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # GUI thread (and workers)

    def publish(self, ordered: list[bytes], frame: Frame | None) -> None:
        """Send texture messages (all of them, in order) and the latest frame to every browser."""
        if frame is not None:
            self._frame = frame
        if (ordered or frame is not None) and self._loop is not None:
            self._loop.call_soon_threadsafe(self._dispatch, ordered, frame)

    def publish_clipboard(self, text: str) -> None:
        self.clipboard = text
        self._post(message(KIND_CLIPBOARD, text.encode("utf-8")))

    def take_input(self) -> list[dict[str, Any]]:
        """The controlling browser's input since the last call, oldest first."""
        events: list[dict[str, Any]] = []
        while self._input:
            events.append(self._input.popleft())
        return events

    @property
    def connected(self) -> bool:
        return bool(self._clients)

    def open_dialog(self, dialog: WebDialog) -> None:
        self._dialogs[dialog.id] = dialog
        self._post(event_message(dialog.event()))

    def close_dialog(self, dialog_id: int) -> None:
        if self._dialogs.pop(dialog_id, None) is not None:
            self._post(event_message({"t": "dialog-close", "id": dialog_id}), all_=True)

    def answer_dialog(self, event: dict[str, Any]) -> None:
        dialog = self._dialogs.get(int(event.get("id", 0) or 0))
        if dialog is not None:
            dialog.answer(event)

    def download_when_written(self, path: Path) -> None:
        """Offer ``path`` for download once the app has written it (a folder as a ZIP)."""
        watch_written(path, lambda p: self.offer_file(p, download=True))

    def offer_file(
        self, path: Path, *, show: bool = False, download: bool = False
    ) -> None:
        """Let the browser download (or show, e.g. an HTML report) a file the app wrote."""
        if path.is_dir():
            path = zip_folder(path)
        key = self.downloads.register(path)
        self._post(
            event_message(
                {
                    "t": "file",
                    "url": f"files/{key}/{quote(path.name)}",
                    "name": path.name,
                    "show": show,
                    "download": download,
                }
            )
        )

    def show_folder(self, folder: Path) -> None:
        """List a folder's files in the browser, each one downloadable."""
        entries: list[dict[str, Any]] = []
        try:
            children = sorted(
                (c for c in folder.iterdir() if c.is_file()),
                key=lambda c: c.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            children = []
        for child in children[:200]:
            key = self.downloads.register(child)
            entries.append(
                {
                    "name": child.name,
                    "size": child.stat().st_size,
                    "url": f"files/{key}/{quote(child.name)}",
                }
            )
        self._post(
            event_message({"t": "folder", "path": str(folder), "files": entries})
        )

    def open_url(self, url: str) -> None:
        self._post(event_message({"t": "open", "url": url}))

    def _post(self, msg: bytes, *, all_: bool = False) -> None:
        """Queue a page message for the controlling browser (or every browser)."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._dispatch_one, msg, all_)

    # server thread

    def _dispatch(self, ordered: list[bytes], frame: Frame | None) -> None:
        for client in self._clients:
            client.ordered.extend(ordered)
            if frame is not None:
                client.frame = frame
            client.wake.set()

    def _dispatch_one(self, msg: bytes, all_: bool) -> None:
        for client in self._clients:
            if all_ or client is self._controller:
                client.ordered.append(msg)
                client.wake.set()

    def _announce_control(self) -> None:
        """Tell every browser whether it controls the app; hand open dialogs to the controller."""
        for client in self._clients:
            controls = client is self._controller
            client.ordered.append(
                event_message(
                    {
                        "t": "control",
                        "control": controls,
                        "browsers": len(self._clients),
                    }
                )
            )
            if controls:
                client.ordered.extend(
                    event_message(d.event()) for d in list(self._dialogs.values())
                )
            client.wake.set()

    def _set_controller(self, client: _Client | None) -> None:
        if client is self._controller:
            return
        self._controller = client
        # Nothing stays pressed from the previous browser.
        self._input.append({"t": "focus", "f": False})
        self._announce_control()
        self.wake.set()

    def _session(self) -> str:
        return hashlib.sha256(f"xknx-web:{self.token}".encode()).hexdigest()

    def _authorized(self, connection: HTTPConnection) -> bool:
        cookie = connection.cookies.get(_COOKIE)
        if cookie is not None and secrets.compare_digest(cookie, self._session()):
            return True
        token = connection.query_params.get("token")
        return token is not None and secrets.compare_digest(token, self.token)

    @staticmethod
    def _same_origin(connection: HTTPConnection) -> bool:
        """A browser request from this server's own page (not from another site)."""
        origin = connection.headers.get("origin")
        if origin is None:
            return True
        host = connection.headers.get("x-forwarded-host") or connection.headers.get(
            "host", ""
        )
        return urlsplit(origin).netloc == host.split(",")[0].strip()

    def _with_session(self, request: Request, response: Response) -> Response:
        secure = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
        )
        response.set_cookie(
            _COOKIE,
            self._session(),
            httponly=True,
            samesite="strict",
            secure=secure,
            max_age=30 * 24 * 3600,
        )
        return response

    async def _index(self, request: Request) -> Response:
        if request.query_params.get("token") is not None and self._authorized(request):
            # Swap the token in the address for the cookie, so it leaves the address bar.
            return self._with_session(request, RedirectResponse("./", status_code=303))
        if not self._authorized(request):
            return _login_page(error=False)
        return FileResponse(
            _STATIC / "index.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    async def _login(self, request: Request) -> Response:
        form = await request.form()
        token = str(form.get("token", ""))
        if not self._same_origin(request) or not secrets.compare_digest(
            token, self.token
        ):
            await asyncio.sleep(1.0)
            return _login_page(error=True)
        return self._with_session(request, RedirectResponse("./", status_code=303))

    async def _script(self, request: Request) -> Response:
        return FileResponse(
            _STATIC / "client.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    async def _file(self, request: Request) -> Response:
        if not self._authorized(request):
            return PlainTextResponse("Forbidden", status_code=403)
        path = self.downloads.get(request.path_params["key"])
        if path is None or not path.is_file():
            return PlainTextResponse("Not found", status_code=404)
        inline = request.query_params.get("show") == "1"
        return FileResponse(
            path,
            filename=path.name,
            content_disposition_type="inline" if inline else "attachment",
            headers={"Cache-Control": "no-store"},
        )

    async def _upload(self, request: Request) -> Response:
        if not self._authorized(request) or not self._same_origin(request):
            return PlainTextResponse("Forbidden", status_code=403)
        dialog_id = int(request.query_params.get("dialog", "0") or 0)
        if dialog_id not in self._dialogs:
            return PlainTextResponse("No open file dialog", status_code=409)
        name = self.files.unique_name(request.query_params.get("name", "upload"))
        target = self.files.root / name
        partial = target.with_name(f".{name}.part")
        size = 0
        try:
            with partial.open("wb") as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > _MAX_UPLOAD:
                        raise ValueError("upload too large")
                    handle.write(chunk)
            partial.replace(target)
        except (OSError, ValueError) as exc:
            partial.unlink(missing_ok=True)
            return PlainTextResponse(str(exc), status_code=413)
        logger.info("web upload stored: %s (%d bytes)", target, size)
        return JSONResponse({"name": name, "size": size})

    async def _socket(self, socket: WebSocket) -> None:
        if not self._authorized(socket) or not self._same_origin(socket):
            await socket.close(code=4403)
            return
        await socket.accept()
        if self._loop is None:  # served without start(), e.g. by a test client
            self._loop = asyncio.get_running_loop()
        client = _Client(socket)
        client.ordered.append(event_message({"t": "texts", "texts": page_texts()}))
        client.ordered.extend(self.textures.snapshot())
        client.frame = self._frame
        self._clients.append(client)
        if self._controller is None:
            self._controller = client
        self._announce_control()
        self.wake.set()  # a new browser needs a frame now
        sender = asyncio.create_task(self._send(client))
        try:
            while True:
                self._receive(client, await socket.receive_text())
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.remove(client)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
            if client is self._controller:
                self._set_controller(self._clients[0] if self._clients else None)
            else:
                self._announce_control()

    async def _send(self, client: _Client) -> None:
        while True:
            await client.wake.wait()
            client.wake.clear()
            while client.ordered:
                await client.socket.send_bytes(client.ordered.popleft())
            frame, client.frame = client.frame, None
            if frame is not None:
                body = frame.body(client.known)
                client.known = frame.hashes
                await client.socket.send_bytes(message(KIND_FRAME, body))

    def _receive(self, client: _Client, text: str) -> None:
        try:
            events: object = json.loads(text)
        except ValueError:
            return
        if not isinstance(events, list):
            return
        for item in cast("list[object]", events):
            if not isinstance(item, dict):
                continue
            event = cast("dict[str, Any]", item)
            if client is not self._controller:
                # A watching browser can only take over.
                if event.get("t") == "take":
                    self._set_controller(client)
                continue
            if event.get("t") == "size":
                self.size = (float(event["w"]), float(event["h"]))
                self.scale = float(event.get("r", 1.0)) or 1.0
            elif event.get("t") == "clip":
                self.clipboard = str(event.get("text", ""))
            self._input.append(event)
        self.last_input = time.monotonic()
        self.wake.set()

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/", self._index),
                Route("/login", self._login, methods=["POST"]),
                Route("/client.js", self._script),
                Route("/files/{key}/{name}", self._file),
                Route("/upload", self._upload, methods=["POST"]),
                WebSocketRoute("/ws", self._socket),
            ]
        )

    def start(
        self,
        host: str,
        port: int,
        *,
        certfile: str | None = None,
        keyfile: str | None = None,
    ) -> threading.Thread:
        """Serve on ``host``:``port`` (HTTPS with a certificate) from a daemon thread."""
        config = uvicorn.Config(
            self.app(),
            host=host,
            port=port,
            log_level="warning",
            ws="websockets-sansio",
            ssl_certfile=certfile,
            ssl_keyfile=keyfile,
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


def _login_page(*, error: bool) -> HTMLResponse:
    """The sign-in form: the session token, as printed when the app started."""
    title = html.escape(S.APP_TITLE)
    notice = f'<p class="error">{html.escape(S.WEB_LOGIN_WRONG)}</p>' if error else ""
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px system-ui, sans-serif; display: grid; place-items: center; min-height: 100vh;
         margin: 0; background: Canvas; color: CanvasText; }}
  form {{ display: grid; gap: 10px; width: min(340px, 90vw); }}
  input, button {{ font: inherit; padding: 8px 10px; }}
  .error {{ color: #d33; margin: 0; }}
  p {{ margin: 0; opacity: .8; }}
</style></head>
<body><form method="post" action="login">
  <h2 style="margin:0">{title}</h2>
  <p>{html.escape(S.WEB_LOGIN_HINT)}</p>
  {notice}
  <input name="token" type="password" autocomplete="current-password" autofocus
         aria-label="{html.escape(S.WEB_LOGIN_TOKEN)}" placeholder="{html.escape(S.WEB_LOGIN_TOKEN)}">
  <button type="submit">{html.escape(S.WEB_LOGIN_BUTTON)}</button>
</form></body></html>"""
    return HTMLResponse(page, status_code=401 if error else 200)
