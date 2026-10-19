"""Browser UI: run the editor without a window and draw it in a browser (``--web``).

The app runs unchanged on hello_imgui's Null platform and renderer. After each frame its draw data
(vertices, indices, draw commands) and texture updates go to the browser, which draws them with
WebGL 2; the browser's mouse, keyboard, text and clipboard come back as ImGui input. Only frames
that changed are sent, so an idle UI costs next to nothing. This module stays light: the server
part is imported only when the web mode starts.

Options: ``--web[=[host:]port]`` (default ``127.0.0.1:8080``), ``--web-cert=FILE`` and
``--web-key=FILE`` for HTTPS, ``--web-files=DIR`` for the folder the browser's files go to (default:
``files`` in the app's data folder). The access token comes from ``XKNX_WEB_TOKEN``, else it is
created once and kept in the app's settings, so the address stays the same across restarts."""

from __future__ import annotations

import logging
import os
import secrets
import struct
import time
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


@dataclass(frozen=True)
class WebOptions:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    certfile: str | None = None
    keyfile: str | None = None
    files: str | None = None


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
        elif arg.startswith(("--web-cert=", "--web-key=", "--web-files=")):
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
    )


def access_token() -> str:
    """``XKNX_WEB_TOKEN``, else the token kept in the settings (created on first use)."""
    from editor_gui.settings import load_settings, save_settings

    given = os.environ.get("XKNX_WEB_TOKEN", "").strip()
    if given:
        return given
    settings = load_settings("web")
    token = settings.get("token")
    if isinstance(token, str) and len(token) >= 16:
        return token
    token = secrets.token_urlsafe(24)
    save_settings("web", {**settings, "token": token})
    return token


def enable_web_mode(
    runner_params: hello_imgui.RunnerParams, options: WebOptions
) -> WebHub:
    """Switch ``runner_params`` to the windowless web mode and start the server."""
    global active_hub
    from editor_gui.settings import config_dir
    from editor_gui.web.frames import Frame, encode_frame
    from editor_gui.web.server import WebHub
    from editor_gui.widgets import dialog

    files = Path(options.files) if options.files else config_dir() / "files"
    hub = WebHub(access_token(), files)
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
    last_frame: list[Frame | None] = [None]
    clients_seen = [False]
    frame_start = [0.0]

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
        post_init()

    # The web mode paces the frames itself: hello_imgui's idling sleeps a fixed time, while browser
    # input has to wake the app at once. Active: up to _ACTIVE_FPS; idle: the app's idle rate, or
    # earlier when input arrives.
    idle_fps = runner_params.fps_idling.fps_idle or 6.0
    runner_params.fps_idling.enable_idling = False

    def web_pre_new_frame() -> None:
        now = time.monotonic()
        active = now - hub.last_input <= _ACTIVE_SECONDS
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
        for event in hub.take_input():
            if _TRACE:
                print(f"{time.monotonic():.3f} apply {event}", flush=True)
            kind = event.get("t")
            if kind == "upload":
                hub.upload_event(event)
            elif kind == "blocked":
                hub.blocked(event)
            else:
                _apply(io, event)
        pre_new_frame()

    def web_after_windows() -> None:
        after_windows()
        hub.host.draw()

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

    hub.start(
        options.host, options.port, certfile=options.certfile, keyfile=options.keyfile
    )
    scheme = "https" if options.certfile else "http"
    shown = "localhost" if options.host in ("127.0.0.1", "::1") else options.host
    url = f"{scheme}://{shown}:{options.port}/?token={hub.token}"
    print(f"XKNX Editor web UI: {url}", flush=True)
    logger.info(
        "web UI listening on %s:%d, files in %s", options.host, options.port, files
    )
    return hub


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
