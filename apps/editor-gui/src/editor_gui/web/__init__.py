"""Browser UI: run the editor without a window and draw it in a browser (``--web``).

The app runs unchanged on hello_imgui's Null platform and renderer. After each frame its draw data
(vertices, indices, draw commands) and texture updates go to the browser, which draws them with
WebGL 2; the browser's mouse, keyboard, text and clipboard come back as ImGui input. Only frames
that changed are sent, so an idle UI costs next to nothing. This module stays light: the server
part is imported only when the web mode starts.

Options:

- ``--web[=[host:]port]``: serve the browser UI (default ``127.0.0.1:8080``, this computer only).
- ``--web-cert=FILE``, ``--web-key=FILE``: HTTPS.
- ``--web-files=DIR``: the folder the browser's files go to (default: ``files`` in the app's data
  folder).
- ``--web-password-file=FILE`` or ``XKNX_WEB_PASSWORD``: browsers sign in with this password. A
  server reachable in the network needs it, or ``--web-trust-proxy`` when a proxy in front signs
  users in itself (e.g. Home Assistant Ingress); otherwise the web mode does not start.
- ``--web-idle=MINUTES``: after that long without input from the controlling browser, a browser
  that connects gets control (default 10).
- ``--web-no-browser``: served on this computer only, the default browser opens the page once the
  server listens; this option keeps it closed (an autostart, a service).

Ctrl+C in the terminal it was started from (and SIGTERM, e.g. from ``docker stop``) ends the app
like File > Exit."""

from __future__ import annotations

import logging
import os
import signal
import socket
import struct
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from imgui_bundle import hello_imgui, imgui

if TYPE_CHECKING:
    from editor_gui.web.server import WebHub

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
# Seconds after the last browser input during which the app renders at full rate.
_ACTIVE_SECONDS = 1.5
# The full rate: the Null backend has no frame cap of its own (no vsync), so the web mode paces it.
_ACTIVE_FPS = 60.0
_TRACE = os.environ.get("XKNX_WEB_TRACE") == "1"

active_hub: WebHub | None = None

# The zoom steps of the browser UI (as in the page's client.js).
ZOOMS = (0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)


# The areas drawn this frame whose view zooms itself (see pinch_area).
_pinch_areas: list[tuple[float, float, float, float]] = []


def pinch_area(low: imgui.ImVec2, high: imgui.ImVec2) -> None:
    """In the web mode: two fingers on this area of the current frame (a view with its own zoom,
    by Ctrl+wheel) zoom that view instead of the app."""
    if active_hub is not None:
        _pinch_areas.append((low.x, low.y, high.x - low.x, high.y - low.y))


# Whether the controlling browser was last used with a finger.
_touch = [False]


def touch_used() -> bool:
    """In the web mode: whether the app is used with a finger (handles to drag can be larger)."""
    return _touch[0]


def zoom() -> float | None:
    """The controlling browser's zoom of the app; None outside the web mode."""
    return active_hub.zoom if active_hub is not None else None


def zoom_step(direction: int) -> None:
    """Zoom in (1), out (-1) or back to 100 % (0) in the controlling browser."""
    hub = active_hub
    if hub is None:
        return
    current = hub.zoom
    if direction == 0:
        target: float | None = 1.0
    elif direction > 0:
        target = next((z for z in ZOOMS if z > current + 0.001), None)
    else:
        target = next((z for z in reversed(ZOOMS) if z < current - 0.001), None)
    if target is not None:
        hub.set_zoom(target)


@dataclass(frozen=True)
class WebOptions:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    certfile: str | None = None
    keyfile: str | None = None
    files: str | None = None
    password_file: str | None = None
    trust_proxy: bool = False
    idle_minutes: float = 10.0
    open_browser: bool = True

    @property
    def local(self) -> bool:
        """Served on this computer only."""
        return self.host in ("127.0.0.1", "localhost", "::1")

    def local_only(self, password: str | None) -> bool:
        """Whether only requests to this computer's own name are answered (against DNS
        rebinding): served on this computer, without a password or a proxy that signs users in.
        A network address given explicitly is answered under any name."""
        return self.local and password is None and not self.trust_proxy

    def password(self) -> str | None:
        """The sign-in password: the file's first line, else ``XKNX_WEB_PASSWORD``."""
        if self.password_file:
            lines = Path(self.password_file).read_text(encoding="utf-8").splitlines()
            return (lines[0] if lines else "") or None
        return os.environ.get("XKNX_WEB_PASSWORD") or None


def web_options(argv: list[str]) -> WebOptions | None:
    """The web mode's options from the command line, None without ``--web``."""
    enabled = False
    host, port = DEFAULT_HOST, DEFAULT_PORT
    values: dict[str, str] = {}
    for arg in argv[1:]:
        if arg == "--web":
            enabled = True
        elif arg.startswith("--web="):
            enabled = True
            given_host, _, given_port = arg.split("=", 1)[1].rpartition(":")
            host = given_host or DEFAULT_HOST
            port = int(given_port) if given_port else DEFAULT_PORT
        elif arg in ("--web-trust-proxy", "--web-no-browser"):
            values[arg] = "1"
        elif arg.startswith(
            (
                "--web-cert=",
                "--web-key=",
                "--web-files=",
                "--web-password-file=",
                "--web-idle=",
            )
        ):
            name, _, value = arg.partition("=")
            values[name] = value
    if not enabled:
        return None
    return WebOptions(
        host,
        port,
        values.get("--web-cert") or None,
        values.get("--web-key") or None,
        values.get("--web-files") or None,
        values.get("--web-password-file") or None,
        "--web-trust-proxy" in values,
        float(values.get("--web-idle") or 10.0),
        "--web-no-browser" not in values,
    )


def enable_web_mode(
    runner_params: hello_imgui.RunnerParams, options: WebOptions
) -> WebHub:
    """Switch ``runner_params`` to the windowless web mode and start the server."""
    global active_hub
    from editor_gui.settings import config_dir
    from editor_gui.web.frames import Frame, encode_frame
    from editor_gui.web.server import WebHub
    from editor_gui.web.touch import ImGuiTouchUi, TouchGestures, TouchLook
    from editor_gui.widgets import dialog

    password = options.password()
    if not options.local and password is None and not options.trust_proxy:
        # Allowed (it may be protected otherwise, e.g. by the network), but not unnoticed.
        print(
            f"XKNX Editor web UI: {options.host} is reachable from the network without a "
            "password: anyone reaching it can use the editor. A password: XKNX_WEB_PASSWORD or "
            "--web-password-file=FILE.",
            file=sys.stderr,
            flush=True,
        )
    files = Path(options.files) if options.files else config_dir() / "files"
    hub = WebHub(
        files,
        password=password,
        local_only=options.local_only(password),
        idle_seconds=options.idle_minutes * 60.0,
    )
    active_hub = hub
    # The web file dialogs are the app's own: nested in the topmost open dialog, else after the
    # app's windows.
    dialog.nested_content = hub.host.draw
    runner_params.platform_backend_type = hello_imgui.PlatformBackendType.null
    runner_params.renderer_backend_type = hello_imgui.RendererBackendType.null

    callbacks = runner_params.callbacks
    post_init = callbacks.post_init or _nothing
    pre_new_frame = callbacks.pre_new_frame or _nothing
    after_swap = callbacks.after_swap or _nothing
    after_windows = callbacks.post_render_dockable_windows or _nothing
    before_render = callbacks.before_imgui_render or _nothing
    last_frame: list[Frame | None] = [None]
    clients_seen = [False]
    frame_start = [0.0]
    # Set up in web_post_init, once ImGui runs.
    gestures: list[TouchGestures] = []
    touch_look = TouchLook()
    touch = _touch
    text_input = [False]

    def web_post_init() -> None:
        io = imgui.get_io()
        # This backend uploads ImGui's textures itself (the browser holds them).
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        # Shortcuts follow the browser's platform (the client sends Cmd as Ctrl on a Mac), not
        # the server's.
        io.config_mac_osx_behaviors = False
        platform_io = imgui.get_platform_io()
        platform_io.platform_set_clipboard_text_fn = lambda _ctx, text: (
            hub.publish_clipboard(text)
        )
        platform_io.platform_get_clipboard_text_fn = lambda _ctx: hub.clipboard
        gestures.append(TouchGestures(ImGuiTouchUi(io)))
        post_init()

    # The web mode paces the frames itself: hello_imgui's idling sleeps a fixed time, while browser
    # input has to wake the app at once. Active: up to _ACTIVE_FPS; idle: the app's idle rate, or
    # earlier when input arrives.
    idle_fps = runner_params.fps_idling.fps_idle or 6.0
    runner_params.fps_idling.enable_idling = False

    def web_pre_new_frame() -> None:
        now = time.monotonic()
        # A swipe's content moving on is drawn at the full rate too.
        active = now - hub.last_input <= _ACTIVE_SECONDS or (
            bool(gestures) and gestures[0].active
        )
        wait = (1.0 / (_ACTIVE_FPS if active else idle_fps)) - (now - frame_start[0])
        if wait > 0:
            if active:
                time.sleep(wait)
            elif hub.wake.wait(wait):
                time.sleep(0.002)  # let the rest of a burst of input arrive
        hub.wake.clear()
        # hello_imgui's own idling would add its fixed sleep on top (its menu toggle included).
        hello_imgui.get_runner_params().fps_idling.enable_idling = False
        frame_start[0] = time.monotonic()
        io = imgui.get_io()
        if hub.size is not None:
            io.display_size = imgui.ImVec2(*hub.size)
        io.display_framebuffer_scale = imgui.ImVec2(hub.scale, hub.scale)
        hub.show_later()
        events = hub.take_input()
        for i, event in enumerate(events):
            if _TRACE:
                print(f"{time.monotonic():.3f} apply {event}", flush=True)
            kind = event.get("t")
            if kind == "upload":
                hub.upload_event(event)
            elif kind == "blocked":
                hub.blocked(event)
            elif kind == "pinch-wheel":
                # Two fingers on a view with its own zoom: Ctrl+wheel there.
                touch[0] = True
                io.add_mouse_source_event(imgui.MouseSource.touch_screen)
                if event.get("end"):
                    io.add_mouse_pos_event(-3.4e38, -3.4e38)
                else:
                    io.add_mouse_pos_event(float(event["x"]), float(event["y"]))
                    io.add_key_event(imgui.Key.mod_ctrl, True)
                    io.add_mouse_wheel_event(0.0, float(event.get("steps", 0)))
                    io.add_key_event(imgui.Key.mod_ctrl, False)
            elif kind == "touch" and gestures:
                if gestures[0].needs_frame(event):
                    hub.put_back(events[i:])
                    break
                touch[0] = True
                gestures[0].handle(event, float(event.get("ms", 0.0)) / 1000.0)
            else:
                if kind in ("move", "button", "wheel") and touch[0]:
                    touch[0] = False
                    io.add_mouse_source_event(imgui.MouseSource.mouse)
                _apply(io, event)
        if gestures:
            gestures[0].step(time.monotonic())
        touch_look.apply(touch[0])
        # With a finger, a swipe on a tab bar would easily tear a panel out of its section:
        # tabs only change their order then.
        docking = hello_imgui.get_runner_params().docking_params
        no_undocking = int(imgui.DockNodeFlags_.no_undocking)
        flags = docking.main_dock_space_node_flags
        flags = flags | no_undocking if touch[0] else flags & ~no_undocking
        if flags != docking.main_dock_space_node_flags:
            docking.main_dock_space_node_flags = flags
        pre_new_frame()

    def web_after_windows() -> None:
        after_windows()
        hub.host.draw()

    def web_before_render() -> None:
        before_render()
        # The grey behind a dialog at once: imgui fades it in over a few frames, which the
        # browser only gets as often as frames change, so the fade looked slow there.
        # None without one (the stub leaves that out).
        modal = cast(
            "imgui.internal.Window | None",
            imgui.internal.get_top_most_and_visible_popup_modal(),
        )
        imgui.get_current_context().dim_bg_ratio = 0.0 if modal is None else 1.0
        areas = [list(a) for a in _pinch_areas]
        _pinch_areas.clear()
        if areas != hub.pinch_areas:
            hub.set_pinch_areas(areas)
        # The page shows a touch screen's keyboard only while a text field takes typing.
        wanted = imgui.get_io().want_text_input
        if wanted != text_input[0]:
            text_input[0] = wanted
            hub.set_text_input(wanted)

    def web_after_swap() -> None:
        after_swap()
        theme = _theme()
        if theme != hub.theme:
            hub.set_theme(theme)
        ordered = hub.textures.process(imgui.get_platform_io().textures)
        connected = hub.connected
        frame: Frame | None = None
        # None before the first frame was rendered.
        draw_data = cast("imgui.ImDrawData | None", imgui.get_draw_data())
        if connected and draw_data is not None and draw_data.valid:
            encoded = encode_frame(draw_data, int(imgui.get_mouse_cursor()))
            # A browser that just connected needs the current frame even when nothing changed.
            if not encoded.same_as(last_frame[0]) or not clients_seen[0]:
                last_frame[0] = frame = encoded
                if _TRACE:
                    print(f"{time.monotonic():.3f} frame", flush=True)
        clients_seen[0] = connected
        hub.publish(ordered, frame)

    callbacks.post_init = web_post_init
    callbacks.pre_new_frame = web_pre_new_frame
    callbacks.after_swap = web_after_swap
    callbacks.post_render_dockable_windows = web_after_windows
    callbacks.before_imgui_render = web_before_render

    hub.start(
        options.host, options.port, certfile=options.certfile, keyfile=options.keyfile
    )
    scheme = "https" if options.certfile else "http"
    if options.local:
        shown = "localhost"
    elif options.host in ("0.0.0.0", "::", ""):
        shown = socket.gethostname()  # every address of this computer: name it
    else:
        shown = options.host
    url = f"{scheme}://{shown}:{options.port}/"
    print(f"XKNX Editor web UI: {url}", flush=True)
    logger.info(
        "web UI listening on %s:%d, files in %s", options.host, options.port, files
    )

    def stop(_signum: int, _frame: object) -> None:
        # Like File > Exit; the next frame ends the app (an idle one is woken).
        hello_imgui.get_runner_params().app_shall_exit = True
        hub.wake.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK: Ctrl+Break on Windows
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    if options.local and options.open_browser:
        threading.Thread(
            target=_open_when_listening,
            args=(options.port, url),
            name="web-ui-browser",
            daemon=True,
        ).start()
    return hub


def _open_when_listening(port: int, url: str, timeout: float = 15.0) -> None:
    """Open ``url`` in the default browser once the server accepts connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        return
    try:
        webbrowser.open(url)
    except Exception:  # no browser here: the address is printed and logged
        logger.info("no browser opened for %s", url)


def _nothing() -> None:
    pass


def _css(colour: imgui.ImVec4) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        *(round(max(0.0, min(1.0, c)) * 255) for c in (colour.x, colour.y, colour.z))
    )


def _theme() -> dict[str, Any]:
    """The app's colours and font size, for the page's own elements."""
    style = imgui.get_style()
    col = imgui.Col_
    return {
        "bg": _css(style.color_(col.window_bg)),
        "panel": _css(style.color_(col.popup_bg)),
        "text": _css(style.color_(col.text)),
        "muted": _css(style.color_(col.text_disabled)),
        "frame": _css(style.color_(col.frame_bg)),
        "border": _css(style.color_(col.border)),
        "button": _css(style.color_(col.button)),
        "button_hovered": _css(style.color_(col.button_hovered)),
        "font_px": round(style.font_size_base),
    }


def _apply(io: imgui.IO, event: dict[str, Any]) -> None:
    """Feed one browser input event to ImGui."""
    from editor_gui.web.keys import imgui_key

    kind = event.get("t")
    if kind == "mods":
        io.add_key_event(imgui.Key.mod_ctrl, bool(event.get("c")))
        io.add_key_event(imgui.Key.mod_shift, bool(event.get("s")))
        io.add_key_event(imgui.Key.mod_alt, bool(event.get("a")))
        io.add_key_event(imgui.Key.mod_super, bool(event.get("m")))
    elif kind == "move":
        io.add_mouse_pos_event(float(event["x"]), float(event["y"]))
    elif kind == "button":
        io.add_mouse_button_event(int(event["b"]), bool(event["d"]))
    elif kind == "wheel":
        io.add_mouse_wheel_event(float(event["x"]), float(event["y"]))
    elif kind == "leave":
        io.add_mouse_pos_event(-3.4e38, -3.4e38)
    elif kind == "key":
        key = imgui_key(str(event.get("code", "")))
        if key is not None:
            io.add_key_event(key, bool(event["d"]))
    elif kind == "text":
        io.add_input_characters_utf8(str(event.get("s", "")))
    elif kind == "focus":
        io.add_focus_event(bool(event.get("f")))


def encoded_texture(data: bytes) -> tuple[imgui.ImTextureRef, float, float] | None:
    """In web mode: a texture for an encoded image (PNG or JPEG), decoded by the browser."""
    hub = active_hub
    if hub is None:
        return None
    size = _image_size(data)
    if size is None:
        return None
    texture_id, msg = hub.textures.add_encoded(data, *size)
    hub.publish([msg], None)
    return imgui.ImTextureRef(texture_id), float(size[0]), float(size[1])


def _image_size(data: bytes) -> tuple[int, int] | None:
    """Width and height of a PNG or baseline/progressive JPEG."""
    from editor_gui.web.frames import png_size

    size = png_size(data)
    if size is not None or data[:2] != b"\xff\xd8":
        return size
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    return None
