"""The web server of the browser UI: the page, its script, file transfers and a WebSocket per browser.

The GUI thread publishes frames, texture messages and page events through :class:`WebHub`; the
server thread (its own asyncio loop, run by uvicorn) forwards them to the browsers and queues the
input of the controlling browser for the GUI thread.

- **Browsers** are told apart by a random session cookie each gets on its first visit.
- **Access**: served only on this computer, every browser may use the app, but only requests made to
  ``localhost`` are answered (a web page cannot reach it under a name of its own, DNS rebinding).
  Reachable in the network, a browser signs in once with the password, or a proxy that signs users
  in itself is trusted. WebSockets and uploads from another site are refused (Origin check).
- **Control**: one browser controls the app, also across a reload. Another one watches and can
  take over; it gets control right away when the controlling one was inactive for a while.
- **Frames**: every browser gets only the draw lists it does not have from its previous frame. A
  browser that falls behind skips frames, never texture messages or page events.
- **The page** itself only does what a browser has to: the file picker, dropped files, downloads,
  opening links. The dialogs around them are the app's own (:mod:`editor_gui.web.dialogs`); the few
  page elements (sign-in, view-only note) use the app's font and theme colours."""

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
from collections.abc import Callable
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
from editor_gui.web.dialogs import DialogHost, DownloadNotice, FolderNotice, LinkNotice
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
# The page's code as this app serves it: a page from an older start reloads itself.
_PAGE_BUILD = hashlib.blake2b(
    b"".join((_STATIC / name).read_bytes() for name in ("index.html", "client.js")),
    digest_size=8,
).hexdigest()
_FONT = Path(__file__).parent.parent / "assets" / "fonts" / "DroidSans.ttf"
# The app icon, the page's icon in the browser tab.
_ICON = Path(__file__).parent.parent / "assets" / "app_settings" / "icon.png"
_COOKIE = "xknx_web_session"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# A browser that just left (a reload) gets control back when it returns within this time.
_RETURN_SECONDS = 5.0
_MAX_UPLOAD = 2 * 1024**3


@dataclass(eq=False)
class _Client:
    """One connected browser: its ordered messages and the latest frame not sent yet."""

    socket: WebSocket
    # The browser's session (its cookie): a reload is the same browser.
    session: str = ""
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
    "WEB_VIEW_ONLY",
    "WEB_TAKE_CONTROL",
    "WEB_CHOOSE_FILE",
    "BTN_CLOSE",
)


def page_texts() -> dict[str, str]:
    """The page's own texts in the app's language."""
    return {name: str(getattr(S, name)) for name in _PAGE_TEXTS}


class WebHub:
    """Between the GUI thread and the browsers."""

    def __init__(
        self,
        files: Path,
        *,
        password: str | None = None,
        local_only: bool = True,
        proxy_ips: frozenset[str] | None = None,
        idle_seconds: float = 600.0,
    ) -> None:
        """``password``: browsers sign in with it (None: no sign-in). ``local_only``: answer only
        requests made to ``localhost``. ``proxy_ips``: when set, answer only connections whose peer
        is one of these addresses (the reverse proxy in front, e.g. Home Assistant Ingress at
        172.30.32.2), rejecting everything else. ``idle_seconds``: after that long without input from
        the controlling browser, a browser that connects gets control."""
        self._password = password
        self._local_only = local_only
        self._proxy_ips = proxy_ips
        self._idle_seconds = idle_seconds
        self._signed_in: set[str] = set()
        self._controller_session: str | None = None
        self._controller_active = 0.0
        self._controller_left = 0.0
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
        # The controlling browser's zoom of the app (1.0: 100 %).
        self.zoom = 1.0
        # Whether a text field of the app takes typing (a touch screen shows its keyboard).
        self.text_input = False
        # Where two fingers zoom a view instead of the app: [x, y, width, height] in app units.
        self.pinch_areas: list[list[float]] = []
        self._input: deque[dict[str, Any]] = deque()
        self._clients: list[_Client] = []
        self._controller: _Client | None = None
        self._frame: Frame | None = None
        self._dialogs: dict[int, WebDialog] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        # The web dialogs and notices, drawn by the app (GUI thread).
        self.host = DialogHost(self.clear_actions)
        # Notices to show, queued from other threads (the GUI thread adds them to the host).
        self._later: deque[Callable[[], Any]] = deque()
        # Where the app's buttons are whose action the page performs itself on a click (file
        # picker, download, link): browsers do these only from the click's own handler.
        self._actions: dict[str, str] = {}
        self._actions_lock = threading.Lock()
        # Whether the controlling browser starts a download without a click (not Safari).
        self.auto_download = True
        # Shows a short note in the app (its toast), set by the app.
        self.notify: Callable[[str], None] | None = None
        # The app's theme colours for the page's own elements.
        self.theme: dict[str, Any] = {}

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

    def put_back(self, events: list[dict[str, Any]]) -> None:
        """Return events taken with :meth:`take_input` for the next frame, ahead of newer ones."""
        self._input.extendleft(reversed(events))

    def set_zoom(self, zoom: float) -> None:
        """Zoom the app in the controlling browser (it answers with its new size)."""
        self._post(event_message({"t": "zoom", "z": zoom}))

    def set_pinch_areas(self, areas: list[list[float]]) -> None:
        self.pinch_areas = areas
        self._post(event_message({"t": "pinch-areas", "areas": areas}))

    def set_text_input(
        self,
        wanted: bool,
        caret: tuple[int, int, int] | None = None,
        *,
        numeric: bool = False,
    ) -> None:
        """Whether a text field takes typing, where its line typed in is (x, y, height in app
        units), and whether it takes a number."""
        self.text_input = wanted
        self._post(
            event_message(
                {
                    "t": "text-input",
                    "on": wanted,
                    "caret": list(caret) if caret else None,
                    "numeric": numeric,
                }
            )
        )

    @property
    def connected(self) -> bool:
        return bool(self._clients)

    def open_dialog(self, dialog: WebDialog) -> None:
        """Show ``dialog`` in the app; tell the page whether files can be dropped on it."""
        self._dialogs[dialog.id] = dialog
        self.host.add(dialog)
        self._post(event_message(dialog.event()))

    def close_dialog(self, dialog_id: int) -> None:
        if self._dialogs.pop(dialog_id, None) is not None:
            self._post(event_message({"t": "dialog-close", "id": dialog_id}), all_=True)

    def answer_dialog(self, event: dict[str, Any]) -> None:
        dialog = self._dialogs.get(int(event.get("id", 0) or 0))
        if dialog is not None:
            dialog.answer(event)

    def request_pick(self, dialog: WebDialog) -> None:
        """Ask the page to open the browser's file picker for ``dialog``."""
        self._post(event_message({**dialog.event(), "t": "pick"}))

    def page_action(
        self,
        key: str,
        rect: tuple[float, float, float, float] | None,
        action: dict[str, Any],
    ) -> None:
        """The button ``key`` is at ``rect`` (None: not visible); a click there makes the page do
        ``action`` itself. Sent to the page only when it changed."""
        if rect is None:
            with self._actions_lock:
                gone = self._actions.pop(key, None)
            if gone is not None:
                self._post(event_message({"t": "area-clear", "key": key}))
            return
        x, y, w, h = rect
        message_json = json.dumps(
            {"t": "area", "key": key, "x": x, "y": y, "w": w, "h": h, "action": action}
        )
        with self._actions_lock:
            if self._actions.get(key) == message_json:
                return
            self._actions[key] = message_json
        self._post(message(KIND_EVENT, message_json.encode("utf-8")))

    def clear_actions(self, prefix: str) -> None:
        """Forget the page actions of a closed dialog."""
        with self._actions_lock:
            for key in [k for k in self._actions if k.startswith(prefix)]:
                del self._actions[key]
        self._post(event_message({"t": "area-clear", "prefix": prefix}))

    def later(self, make: Callable[[], Any]) -> None:
        """Show the notice ``make()`` returns, on the GUI thread (callable from any thread)."""
        self._later.append(make)

    def show_later(self) -> None:
        """Add the queued notices to the host (GUI thread)."""
        while self._later:
            self.host.add(self._later.popleft()())

    def upload_event(self, event: dict[str, Any]) -> None:
        """An upload's progress, from the page (GUI thread)."""
        dialog = self._dialogs.get(int(event.get("id", 0) or 0))
        if dialog is not None:
            dialog.upload_event(event)

    def blocked(self, event: dict[str, Any]) -> None:
        """The browser did not open a link or report: offer it in a notice (GUI thread)."""
        self.host.add(
            LinkNotice(
                self,
                str(event.get("url", "")),
                show=bool(event.get("show")),
                name=str(event.get("name", "")),
            )
        )

    def download_when_written(self, path: Path) -> None:
        """Offer ``path`` for download once the app has written it (a folder as a ZIP)."""
        watch_written(path, lambda p: self.offer_file(p, download=True))

    def file_url(self, path: Path) -> str:
        """An address the browser can download ``path`` from (only files offered this way)."""
        return f"files/{self.downloads.register(path)}/{quote(path.name)}"

    def download(self, url: str, name: str) -> None:
        self._post(event_message({"t": "download", "url": url, "name": name}))

    def offer_file(
        self, path: Path, *, show: bool = False, download: bool = False
    ) -> None:
        """Download a file the app wrote, or show it in a new tab (e.g. an HTML report)."""
        if path.is_dir():
            path = zip_folder(path)
        url = self.file_url(path)
        if show:
            self.open_url(f"{url}?show=1", name=path.name, show=True)
        if download:
            self.download(url, path.name)
            if self.notify is not None:
                self.notify(S.WEB_DOWNLOAD_STARTED.format(name=path.name))
            if not self.auto_download:
                name = path.name
                self.later(lambda: DownloadNotice(self, url, name))

    def show_folder(self, folder: Path) -> None:
        """List a folder's files in the app, each one downloadable (GUI thread)."""
        self.host.add(FolderNotice(self, folder))

    def open_url(self, url: str, *, name: str = "", show: bool = False) -> None:
        """Open a link (or a file shown in the browser) in a new tab of the controlling browser."""
        self._post(event_message({"t": "open", "url": url, "name": name, "show": show}))

    def set_theme(self, theme: dict[str, Any]) -> None:
        """The app's theme colours changed: pass them to every page."""
        self.theme = theme
        self._post(event_message({"t": "theme", **theme}), all_=True)

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
                        "text_input": self.text_input,
                        "pinch_areas": self.pinch_areas,
                    }
                )
            )
            if controls:
                client.ordered.extend(
                    event_message(d.event()) for d in list(self._dialogs.values())
                )
                with self._actions_lock:
                    actions = list(self._actions.values())
                client.ordered.extend(
                    message(KIND_EVENT, a.encode("utf-8")) for a in actions
                )
            client.wake.set()

    def _set_controller(self, client: _Client | None) -> None:
        if client is self._controller:
            return
        self._controller = client
        if client is not None:
            self._controller_session = client.session
            self._controller_active = time.monotonic()
        # Nothing stays pressed from the previous browser.
        self._input.append({"t": "focus", "f": False})
        self._announce_control()
        self.wake.set()

    def _controller_idle(self) -> bool:
        return time.monotonic() - self._controller_active > self._idle_seconds

    def _on_connect(self, client: _Client) -> None:
        """Control for a browser that connects: its own again (a reload), or free, or the
        controlling browser was inactive for too long; else it watches."""
        returning = client.session == self._controller_session
        if self._controller is None:
            just_left = time.monotonic() - self._controller_left < _RETURN_SECONDS
            if returning or not just_left or self._controller_idle():
                self._set_controller(client)
                return
        elif returning or self._controller_idle():
            self._set_controller(client)
            return
        self._announce_control()

    def _on_disconnect(self, client: _Client) -> None:
        if client is not self._controller:
            self._announce_control()
            return
        self._controller = None
        self._controller_left = time.monotonic()
        self._announce_control()
        if self._loop is not None:
            # Not back in a moment (a reload): the next browser gets control.
            self._loop.call_later(_RETURN_SECONDS, self._hand_over)

    def _hand_over(self) -> None:
        if self._controller is None and self._clients:
            self._set_controller(self._clients[0])

    def _host_allowed(self, connection: HTTPConnection) -> bool:
        """Which connections to answer. With ``proxy_ips`` set, only those from the proxy in front
        (its real transport peer); otherwise, when ``local_only``, only requests made to ``localhost``."""
        if self._proxy_ips is not None:
            peer = connection.client.host if connection.client else None
            if peer not in self._proxy_ips:
                return False
        if not self._local_only:
            return True
        host = connection.headers.get("host", "")
        return (urlsplit(f"//{host}").hostname or "") in _LOCAL_HOSTS

    def _authorized(self, connection: HTTPConnection) -> bool:
        if not self._host_allowed(connection):
            return False
        if self._password is None:
            return True
        session = connection.cookies.get(_COOKIE)
        return session is not None and session in self._signed_in

    @property
    def sign_in(self) -> bool:
        """Whether browsers sign in with a password."""
        return self._password is not None

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

    def _with_session(
        self, request: Request, response: Response, session: str
    ) -> Response:
        secure = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
        )
        response.set_cookie(
            _COOKIE,
            session,
            httponly=True,
            samesite="strict",
            secure=secure,
            max_age=30 * 24 * 3600,
        )
        return response

    async def _index(self, request: Request) -> Response:
        if not self._host_allowed(request):
            return PlainTextResponse("Forbidden", status_code=403)
        if not self._authorized(request):
            return _login_page(self.theme, error=False)
        page = FileResponse(
            _STATIC / "index.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
        if request.cookies.get(_COOKIE):
            return page
        # A new browser: its own session from now on.
        return self._with_session(request, page, secrets.token_urlsafe(32))

    async def _login(self, request: Request) -> Response:
        if not self._host_allowed(request):
            return PlainTextResponse("Forbidden", status_code=403)
        form = await request.form()
        given = str(form.get("password", "")).encode("utf-8")
        expected = (self._password or "").encode("utf-8")
        if (
            self._password is None
            or not self._same_origin(request)
            or not secrets.compare_digest(given, expected)
        ):
            await asyncio.sleep(1.0)
            return _login_page(self.theme, error=True)
        session = secrets.token_urlsafe(32)
        self._signed_in.add(session)
        return self._with_session(
            request, RedirectResponse("./", status_code=303), session
        )

    async def _font(self, request: Request) -> Response:
        return FileResponse(_FONT, media_type="font/ttf")

    async def _icon(self, request: Request) -> Response:
        return FileResponse(_ICON, media_type="image/png")

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
        # Without a cookie (blocked by the browser) the connection is a browser of its own.
        session = socket.cookies.get(_COOKIE) or secrets.token_urlsafe(32)
        client = _Client(socket, session)
        client.ordered.append(
            event_message({"t": "texts", "texts": page_texts(), "build": _PAGE_BUILD})
        )
        if self.theme:
            client.ordered.append(event_message({"t": "theme", **self.theme}))
        client.ordered.extend(self.textures.snapshot())
        client.frame = self._frame
        self._clients.append(client)
        self._on_connect(client)
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
            self._on_disconnect(client)

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
            self._controller_active = time.monotonic()
            if event.get("t") == "caps":
                self.auto_download = bool(event.get("download", True))
                continue
            if event.get("t") == "size":
                self.size = (float(event["w"]), float(event["h"]))
                self.scale = float(event.get("r", 1.0)) or 1.0
                self.zoom = float(event.get("z", 1.0)) or 1.0
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
                Route("/font.ttf", self._font),
                Route("/favicon.png", self._icon),
                # Browsers ask for it on their own, e.g. for a downloaded file shown in a tab.
                Route("/favicon.ico", self._icon),
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


def _login_page(theme: dict[str, Any], *, error: bool) -> HTMLResponse:
    """The sign-in form (the password for the web access), in the app's look."""
    colours = {
        "bg": "#f0f0f0",
        "panel": "#ffffff",
        "text": "#1e1e1e",
        "muted": "#6e6e6e",
        "frame": "#ffffff",
        "border": "#c8c8c8",
        "button": "#e1e1e1",
        "button_hovered": "#d0e4f7",
        **{k: v for k, v in theme.items() if isinstance(v, str)},
    }
    size = theme.get("font_px", 15)
    title = html.escape(S.APP_TITLE)
    notice = f'<p class="error">{html.escape(S.WEB_LOGIN_WRONG)}</p>' if error else ""
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" type="image/png" href="favicon.png">
<style>
  @font-face {{ font-family: "XKNX"; src: url("font.ttf") format("truetype"); }}
  body {{ font: {size}px "XKNX", system-ui, sans-serif; display: grid; place-items: center;
         min-height: 100vh; margin: 0; background: {colours["bg"]}; color: {colours["text"]}; }}
  form {{ display: grid; gap: 8px; width: min(380px, 90vw); background: {colours["panel"]};
          border: 1px solid {colours["border"]}; padding: 16px 18px; }}
  h1 {{ font-size: 1.35em; margin: 0 0 4px; }}
  p {{ margin: 0; }}
  .hint, label {{ color: {colours["muted"]}; }}
  .error {{ color: #c0392b; }}
  input {{ font: inherit; color: inherit; background: {colours["frame"]};
           border: 1px solid {colours["border"]}; padding: 4px 6px; }}
  button {{ font: inherit; color: inherit; background: {colours["button"]};
            border: 1px solid {colours["border"]}; padding: 4px 16px; justify-self: start;
            min-width: 90px; }}
  button:hover {{ background: {colours["button_hovered"]}; }}
</style></head>
<body><form method="post" action="login">
  <h1>{title}</h1>
  <p class="hint">{html.escape(S.WEB_LOGIN_HINT)}</p>
  {notice}
  <label for="password">{html.escape(S.WEB_LOGIN_PASSWORD)}</label>
  <input id="password" name="password" type="password" autocomplete="current-password" autofocus>
  <button type="submit">{html.escape(S.WEB_LOGIN_BUTTON)}</button>
</form></body></html>"""
    return HTMLResponse(page, status_code=401 if error else 200)
