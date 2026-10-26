"""Tests of the browser UI: frame and texture encoding, key mapping, options, files, dialogs and the
server (access, uploads, downloads, control)."""

from __future__ import annotations

import contextlib
import html
import json
import socket
import struct
import time
import urllib.request
import zipfile
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import hello_imgui, imgui
from imgui_bundle import portable_file_dialogs as pfd
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from editor_gui import file_dialogs, web
from editor_gui.strings import S
from editor_gui.web import frames, server
from editor_gui.web.dialogs import (
    DownloadNotice,
    FolderNotice,
    WebOpenFile,
    WebSaveFile,
    WebSelectFolder,
    filter_pairs,
)
from editor_gui.web.files import FileFolder, safe_name, watch_written, zip_folder
from editor_gui.web.keys import imgui_key
from editor_gui.web.server import WebHub
from editor_gui.widgets import dialog as app_dialog

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000030000000208060000009d7466"
    "1a0000001149444154789c63f8cfc0f01f86199039009b7e0bf572b0b93c0000"
    "000049454e44ae426082"
)
_PASSWORD = "secret password for the tests"


@pytest.fixture
def context() -> Iterator[imgui.IO]:
    """A Dear ImGui context whose textures this backend handles."""
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(640, 480)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield io
    imgui.destroy_context(ctx)


@pytest.fixture
def hub(tmp_path: Path) -> WebHub:
    return WebHub(tmp_path / "files")


def _render(mirror: frames.TextureMirror, text: str = "Hallo ä ö ü") -> list[bytes]:
    """One frame with a window and some text; the texture messages it produced."""
    imgui.get_io().delta_time = 1 / 60
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(10, 10))
    imgui.set_next_window_size(imgui.ImVec2(300, 200))
    imgui.begin("Window")
    imgui.text(text)
    imgui.end()
    imgui.set_next_window_pos(imgui.ImVec2(320, 10))
    imgui.set_next_window_size(imgui.ImVec2(300, 200))
    imgui.begin("Other")
    imgui.text("unchanged")
    imgui.end()
    imgui.render()
    return mirror.process(imgui.get_platform_io().textures)


def _body(message: bytes) -> tuple[int, bytes]:
    return message[0], zlib.decompress(message[1:])


def _event(message: bytes) -> dict[str, Any]:
    kind, body = _body(message)
    assert kind == frames.KIND_EVENT
    return json.loads(body)


def _parse_frame(body: bytes) -> dict[str, Any]:
    """The frame layout the browser reads (see frames.py)."""
    cursor, index_size, _, x, y, w, h, count = struct.unpack_from("<BBHffffI", body)
    off = 24
    lists: list[dict[str, Any]] = []
    for _ in range(count):
        kind, digest = struct.unpack_from("<B8s", body, off)
        off += 9
        if kind == frames.LIST_KEPT:
            lists.append({"kept": True, "hash": digest})
            continue
        vertices, indices, commands = struct.unpack_from("<III", body, off)
        off += 12 + vertices * 20
        index_bytes = indices * index_size
        off += index_bytes + (4 - index_bytes % 4) % 4
        cmds = [
            struct.unpack_from("<ffffIIII", body, off + 32 * i) for i in range(commands)
        ]
        off += 32 * commands
        lists.append(
            {
                "kept": False,
                "hash": digest,
                "vertices": vertices,
                "indices": indices,
                "commands": cmds,
            }
        )
    assert off == len(body)
    return {
        "cursor": cursor - 1,
        "index_size": index_size,
        "display": (x, y, w, h),
        "lists": lists,
    }


# --- frames and textures ----------------------------------------------------------------------


def test_frame_round_trip(context: imgui.IO) -> None:
    mirror = frames.TextureMirror()
    created = _render(mirror)
    _render(mirror)  # a new window shows from its second frame on
    draw_data = imgui.get_draw_data()
    frame = frames.encode_frame(draw_data, int(imgui.MouseCursor_.text_input))

    parsed = _parse_frame(frame.body())
    assert parsed["cursor"] == int(imgui.MouseCursor_.text_input)
    assert parsed["index_size"] in (2, 4)
    assert parsed["display"] == (0.0, 0.0, 640.0, 480.0)
    lists = parsed["lists"]
    assert not any(item["kept"] for item in lists)
    assert sum(item["vertices"] for item in lists) == draw_data.total_vtx_count
    assert sum(item["indices"] for item in lists) == draw_data.total_idx_count
    # Every draw command uses the font atlas texture the mirror created.
    ((kind, texture),) = (_body(m) for m in created)
    assert kind == frames.KIND_TEXTURE
    atlas_id = struct.unpack_from("<I", texture)[0]
    used = {cmd[4] for item in lists for cmd in item["commands"] if cmd[7]}
    assert used == {atlas_id}


def test_unchanged_draw_lists_are_only_referenced(context: imgui.IO) -> None:
    mirror = frames.TextureMirror()
    _render(mirror)
    _render(mirror)
    first = frames.encode_frame(imgui.get_draw_data(), 0)
    _render(mirror, "Hallo Welt, geändert")
    second = frames.encode_frame(imgui.get_draw_data(), 0)

    assert not second.same_as(first)
    parsed = _parse_frame(second.body(first.hashes))
    kept = [item for item in parsed["lists"] if item["kept"]]
    sent = [item for item in parsed["lists"] if not item["kept"]]
    # The window with the changed text travels; the unchanged one is only referenced.
    assert kept and sent
    assert {item["hash"] for item in kept} <= first.hashes
    assert len(second.body(first.hashes)) < len(second.body())
    assert second.same_as(second)


def test_texture_create_and_snapshot(context: imgui.IO) -> None:
    mirror = frames.TextureMirror()
    (message,) = _render(mirror)
    kind, body = _body(message)
    texture_id, operation, width, height, x, y, w, h = struct.unpack_from(
        "<IB3xIIIIII", body
    )
    assert kind == frames.KIND_TEXTURE
    assert operation == frames.TEX_CREATE
    assert (x, y, w, h) == (0, 0, width, height)
    assert len(body) == 32 + width * height * 4
    # Handled: no request left for the next frame.
    assert _render(mirror) == []
    # A browser connecting later gets the same texture.
    assert [_body(m) for m in mirror.snapshot()] == [(kind, body)]
    assert all(
        t.status == imgui.ImTextureStatus.ok for t in imgui.get_platform_io().textures
    )
    assert texture_id > 0


def test_new_glyphs_update_the_texture(context: imgui.IO) -> None:
    mirror = frames.TextureMirror()
    _render(mirror)
    imgui.get_io().delta_time = 1 / 60
    imgui.new_frame()
    imgui.begin("Window")
    imgui.push_font(None, 41.0)
    imgui.text("Größere Schrift")
    imgui.pop_font()
    imgui.end()
    imgui.render()
    messages = mirror.process(imgui.get_platform_io().textures)
    operations = {struct.unpack_from("<IB", _body(m)[1])[1] for m in messages}
    assert operations <= {frames.TEX_UPDATE, frames.TEX_CREATE}
    assert operations
    # The snapshot carries the updated pixels.
    (snapshot,) = mirror.snapshot()
    texture = next(iter(imgui.get_platform_io().textures))
    _, body = _body(snapshot)
    assert body[32:] == texture.get_pixels_array().tobytes()


def test_encoded_image_texture() -> None:
    mirror = frames.TextureMirror()
    texture_id, message = mirror.add_encoded(_PNG, 3, 2)
    kind, body = _body(message)
    assert kind == frames.KIND_TEXTURE
    assert struct.unpack_from("<IB3xII", body) == (
        texture_id,
        frames.TEX_ENCODED,
        3,
        2,
    )
    assert body[32:] == _PNG
    assert [_body(m) for m in mirror.snapshot()] == [(kind, body)]


def test_image_sizes() -> None:
    assert frames.png_size(_PNG) == (3, 2)
    assert frames.png_size(b"GIF89a") is None
    jpeg = (
        b"\xff\xd8"
        + b"\xff\xe0\x00\x04\x00\x00"
        + b"\xff\xc0\x00\x0b\x08\x00\x07\x00\x05\x01\x01\x11\x00"
    )
    assert web._image_size(jpeg) == (5, 7)
    assert web._image_size(b"\xff\xd8\x00") is None


# --- input, options ---------------------------------------------------------------------------


def test_keys() -> None:
    assert imgui_key("KeyA") == imgui.Key.a
    assert imgui_key("Digit3") == imgui.Key._3
    assert imgui_key("Numpad7") == imgui.Key.keypad7
    assert imgui_key("ArrowLeft") == imgui.Key.left_arrow
    assert imgui_key("F12") == imgui.Key.f12
    assert imgui_key("ControlRight") == imgui.Key.right_ctrl
    assert imgui_key("NoSuchKey") is None


def test_web_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert web.web_options(["app"]) is None
    assert web.web_options(["app", "--web-files=x"]) is None
    local = web.web_options(["app", "--web"])
    assert local == web.WebOptions() and local.local
    assert web.web_options(["app", "--web=9000"]) == web.WebOptions(
        port=9000, port_given=True
    )
    secret = tmp_path / "password.txt"
    secret.write_text("from the file\n")
    options = web.web_options(
        [
            "app",
            "project.xknx",
            "--web=0.0.0.0:9001",
            "--web-cert=c.pem",
            "--web-key=k.pem",
            "--web-files=/srv/knx",
            f"--web-password-file={secret}",
            "--web-trust-proxy",
            "--web-idle=2.5",
        ]
    )
    assert options == web.WebOptions(
        "0.0.0.0",
        9001,
        "c.pem",
        "k.pem",
        "/srv/knx",
        str(secret),
        True,
        2.5,
        port_given=True,
    )
    assert options is not None and not options.local
    assert options.open_browser
    assert options.password() == "from the file"
    quiet = web.web_options(["app", "--web", "--web-no-browser"])
    assert quiet is not None and not quiet.open_browser
    monkeypatch.setenv("XKNX_WEB_PASSWORD", "from the environment")
    assert web.WebOptions(host="0.0.0.0").password() == "from the environment"


def test_without_a_port_a_free_one_is_taken() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        sock = web.server_socket(web.WebOptions(port=port))
        assert sock is not None
        with sock:
            assert sock.getsockname()[1] != port
        # A port given is not replaced by another one.
        assert web.server_socket(web.WebOptions(port=port, port_given=True)) is None


def test_a_port_given_in_use_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(SystemExit) as ended:
        web._port_in_use(web.WebOptions(port=8080, port_given=True))
    assert ended.value.code == 2


def test_the_server_serves_on_its_socket(hub: WebHub) -> None:
    sock = web.server_socket(web.WebOptions(port=0, port_given=True))
    assert sock is not None
    port = sock.getsockname()[1]
    hub.start(sock)
    deadline = time.monotonic() + 10
    while True:
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/") as reply:
                assert reply.status == 200
                break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def test_the_browser_opens_once_the_server_listens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen()
        port = listening.getsockname()[1]
        web._open_when_listening(port, f"http://localhost:{port}/")
    assert opened == [f"http://localhost:{port}/"]
    # Nothing listens: no browser, after the time given.
    web._open_when_listening(port, "http://localhost:1/", timeout=0.3)
    assert len(opened) == 1


def test_only_this_computer_checks_the_name() -> None:
    # On this computer without a password: only requests to its own name (DNS rebinding).
    assert web.WebOptions().local_only(None)
    assert not web.WebOptions().local_only("secret")
    assert not web.WebOptions(trust_proxy=True).local_only(None)
    # A network address given explicitly: any name, also without a password.
    assert not web.WebOptions(host="0.0.0.0").local_only(None)


def test_apply_input(context: imgui.IO) -> None:
    io = context
    for event in (
        {"t": "move", "x": 12, "y": 34},
        {"t": "button", "b": 0, "d": True},
        {"t": "mods", "c": True, "s": False, "a": False, "m": False},
        {"t": "key", "code": "KeyC", "d": True},
        {"t": "text", "s": "ä"},
    ):
        web._apply(io, event)
    # ImGui spreads a click and the following keys over consecutive frames.
    for _ in range(3):
        io.delta_time = 1 / 60
        imgui.new_frame()
        imgui.end_frame()
    io.delta_time = 1 / 60
    imgui.new_frame()
    assert (io.mouse_pos.x, io.mouse_pos.y) == (12, 34)
    assert io.mouse_down[0]
    # With the macOS key behaviours ImGui maps Ctrl to Super, so the control modifier surfaces as
    # key_super there and as key_ctrl elsewhere; the app reads both, so accept either.
    assert io.key_ctrl or io.key_super
    assert imgui.is_key_down(imgui.Key.c)
    imgui.end_frame()


# --- files and dialogs ------------------------------------------------------------------------


def test_safe_and_unique_names(tmp_path: Path) -> None:
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name(r"C:\Users\x\project.xknx") == "project.xknx"
    assert safe_name('a<b>:"c|?*.knxproj') == "a_b___c___.knxproj"
    assert safe_name("  ..  ") == "file"
    folder = FileFolder(tmp_path)
    assert folder.unique_name("p.xknx") == "p.xknx"
    (tmp_path / "p.xknx").write_bytes(b"1")
    (tmp_path / "p (2).xknx").write_bytes(b"2")
    assert folder.unique_name("p.xknx") == "p (3).xknx"
    assert {e.name for e in folder.files()} == {"p.xknx", "p (2).xknx"}


def test_watch_written_waits_for_the_change(tmp_path: Path) -> None:
    existing = tmp_path / "old.knxproj"
    existing.write_bytes(b"old")
    seen: list[Path] = []
    watch_written(existing, seen.append, interval=0.05)
    time.sleep(0.3)
    assert seen == []  # unchanged: not offered
    existing.write_bytes(b"new content")
    deadline = time.monotonic() + 5
    while not seen and time.monotonic() < deadline:
        time.sleep(0.05)
    assert seen == [existing]


def test_zip_folder(tmp_path: Path) -> None:
    folder = tmp_path / "Store"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    (folder / "sub" / "b.txt").write_text("b")
    with zipfile.ZipFile(zip_folder(folder)) as archive:
        assert sorted(archive.namelist()) == ["Store/a.txt", "Store/sub/b.txt"]


def test_filter_pairs() -> None:
    assert filter_pairs(["Projects", "*.xknx *.knxproj", "All Files", "*"]) == [
        ("Projects", ["*.xknx", "*.knxproj"]),
        ("All Files", ["*"]),
    ]
    assert filter_pairs(None) == []


def test_open_dialog(hub: WebHub) -> None:
    (hub.files.root / "a.knxproj").write_bytes(b"x")
    dialog = WebOpenFile(hub, "Open", "", ["Projects", "*.knxproj"])
    request = dialog.event()
    assert request == {
        "t": "dialog",
        "id": dialog.id,
        "upload": True,
        "multi": False,
        "accept": ".knxproj",
    }
    assert hub.host.items == [dialog]
    assert not dialog.ready()
    dialog.answer({"ok": True, "names": ["a.knxproj", "../missing.xknx"]})
    assert dialog.ready()
    assert dialog.result() == [str(hub.files.root / "a.knxproj")]
    assert dialog.closing


def test_upload_progress_answers_the_dialog(hub: WebHub) -> None:
    (hub.files.root / "up.knxproj").write_bytes(b"x")
    dialog = WebOpenFile(hub, "Open")
    dialog.upload_event({"state": "start", "name": "up.knxproj"})
    assert not dialog.ready()
    dialog.upload_event({"state": "error", "error": "too large"})
    assert not dialog.ready()
    dialog.upload_event({"state": "done", "names": ["up.knxproj"]})
    assert dialog.ready()
    assert dialog.result() == [str(hub.files.root / "up.knxproj")]


def test_save_dialog_adds_the_extension_and_downloads(
    hub: WebHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    offered: list[Path] = []
    monkeypatch.setattr(hub, "download_when_written", offered.append)
    dialog = WebSaveFile(
        hub, "Export", "C:/x/Project.knxproj", ["ETS", "*.knxproj", "All", "*"]
    )
    assert dialog.default_name == "Project.knxproj"
    dialog.answer({"ok": True, "name": "Haus", "filter": 0, "download": True})
    target = hub.files.root / "Haus.knxproj"
    assert dialog.result() == str(target)
    assert offered == [target]


def test_download_box_starts_unticked_when_saving_a_project(
    hub: WebHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    import editor_gui.web.dialogs as dialogs

    stored: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(
        dialogs, "load_settings", lambda name: dict(stored.get(name, {}))
    )
    monkeypatch.setattr(
        dialogs, "save_settings", lambda name, data: stored.update({name: data})
    )
    project = ["XKNX Project (*.xknx)", "*.xknx"]
    export = ["Project (*.knxproj)", "*.knxproj"]
    assert not WebSaveFile(hub, "Save XKNX Project", "", project)._download
    assert WebSaveFile(hub, "Export", "", export)._download
    # A choice that differs from the default is kept per dialog, one back to it is dropped.
    dialogs._remember_download("Save XKNX Project", True, False)
    assert WebSaveFile(hub, "Save XKNX Project", "", project)._download
    dialogs._remember_download("Save XKNX Project", False, False)
    assert stored["web"]["download_choices"] == {}
    # Choices stored before (also the untouched defaults) no longer count.
    stored["web"] = {"download": {"Save XKNX Project": True}}
    assert not WebSaveFile(hub, "Save XKNX Project", "", project)._download


def test_cancelled_dialogs(hub: WebHub) -> None:
    save = WebSaveFile(hub, "Save")
    save.answer({"ok": False})
    assert save.ready() and save.result() == ""
    opened = WebOpenFile(hub, "Open")
    opened.answer({"ok": False})
    assert opened.ready() and opened.result() == []


def test_select_folder(hub: WebHub) -> None:
    dialog = WebSelectFolder(hub, "Store")
    dialog.answer({"ok": True, "name": "ProjectStore", "download": False})
    assert Path(dialog.result()).is_dir()


def test_file_dialogs_use_the_page_in_web_mode(
    hub: WebHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web, "active_hub", hub)
    assert isinstance(file_dialogs.open_file("Open"), WebOpenFile)
    multi = file_dialogs.open_file("Open", options=pfd.opt.multiselect)
    assert isinstance(multi, WebOpenFile) and multi.multiselect
    assert isinstance(file_dialogs.save_file("Save"), WebSaveFile)
    assert isinstance(file_dialogs.select_folder("Folder"), WebSelectFolder)


# --- server -----------------------------------------------------------------------------------


def _receive_until(
    socket: Any, test: Callable[[bytes], bool], limit: int = 50
) -> list[bytes]:
    """Messages up to and including the first that passes ``test``."""
    seen: list[bytes] = []
    for _ in range(limit):
        message = socket.receive_bytes()
        seen.append(message)
        if test(message):
            return seen
    raise AssertionError("message not received")


def _ws(client: TestClient, headers: dict[str, str] | None = None) -> Any:
    """A WebSocket made to ``localhost`` (the test client otherwise names its own host)."""
    return client.websocket_connect(
        "/ws", headers={"host": "localhost", **(headers or {})}
    )


def _is_event(kind: str) -> Callable[[bytes], bool]:
    return lambda m: m[0] == frames.KIND_EVENT and _event(m).get("t") == kind


def _controls(socket: Any, expected: bool) -> None:
    """Wait until the browser is told it does (or does not) control the app."""
    _receive_until(
        socket,
        lambda m: (
            m[0] == frames.KIND_EVENT
            and _event(m).get("t") == "control"
            and _event(m)["control"] is expected
        ),
    )


def test_this_computer_needs_no_sign_in(hub: WebHub) -> None:
    with TestClient(
        hub.app(), base_url="http://localhost", follow_redirects=False
    ) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "client.js" in page.text
        # A new browser gets its own session.
        cookie = page.headers["set-cookie"].lower()
        assert "xknx_web_session=" in cookie and "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "set-cookie" not in client.get("/").headers  # already has one
        assert client.get("/client.js").status_code == 200
    # Another name for this computer (a web page's own domain): refused.
    with TestClient(hub.app(), base_url="http://attacker.example") as client:
        assert client.get("/").status_code == 403
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            _ws(client, {"host": "attacker.example:8080"}),
        ):
            pass
        assert closed.value.code == 4403


def test_password_sign_in(tmp_path: Path) -> None:
    hub = WebHub(tmp_path / "files", password=_PASSWORD, local_only=False)
    with TestClient(
        hub.app(), base_url="http://editor.lan", follow_redirects=False
    ) as client:
        form = client.get("/")
        assert 'name="password"' in form.text
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            _ws(client),
        ):
            pass
        assert closed.value.code == 4403
        wrong = client.post("/login", data={"password": "nope"})
        assert wrong.status_code == 401
        assert 'class="error"' in wrong.text
        right = client.post("/login", data={"password": _PASSWORD})
        assert right.status_code == 303
        assert "client.js" in client.get("/").text
        session = client.cookies["xknx_web_session"]
        with _browser(client, session) as socket:
            assert _event(socket.receive_bytes())["t"] == "texts"


def test_wrong_passwords_lock_out_for_a_while(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_LOGIN_DELAY", 0.0)
    monkeypatch.setattr(server, "_LOGIN_ATTEMPTS", 3)
    monkeypatch.setattr(server, "_LOGIN_LOCKOUT", 0.5)
    hub = WebHub(tmp_path / "files", password=_PASSWORD, local_only=False)
    with TestClient(
        hub.app(), base_url="http://editor.lan", follow_redirects=False
    ) as client:
        for _ in range(3):
            assert client.post("/login", data={"password": "nope"}).status_code == 401
        # Locked: even the right password is refused for now.
        locked = client.post("/login", data={"password": _PASSWORD})
        assert locked.status_code == 429
        assert S.WEB_LOGIN_LOCKED in html.unescape(locked.text)
        time.sleep(0.6)
        assert client.post("/login", data={"password": _PASSWORD}).status_code == 303


def test_pages_are_not_framed_by_other_sites(tmp_path: Path) -> None:
    hub = WebHub(tmp_path / "files", password=_PASSWORD, local_only=False)
    with TestClient(hub.app(), base_url="http://editor.lan") as client:
        pages = [client.get("/")]
        client.post("/login", data={"password": _PASSWORD})
        pages.append(client.get("/"))
    assert "client.js" in pages[1].text
    for page in pages:
        assert page.headers["x-frame-options"] == "SAMEORIGIN"
        assert page.headers["content-security-policy"] == "frame-ancestors 'self'"


def test_pages_show_the_app_icon(tmp_path: Path) -> None:
    hub = WebHub(tmp_path / "files", password=_PASSWORD, local_only=False)
    with TestClient(hub.app(), base_url="http://editor.lan") as client:
        # The sign-in page links it, and it is there before signing in.
        assert 'rel="icon"' in client.get("/").text
        for path in ("/favicon.png", "/favicon.ico"):
            icon = client.get(path)
            assert icon.status_code == 200
            assert icon.headers["content-type"] == "image/png"
            assert icon.content.startswith(b"\x89PNG")
        client.post("/login", data={"password": _PASSWORD})
        assert 'rel="icon"' in client.get("/").text


def test_socket_refuses_other_sites(hub: WebHub) -> None:
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        pytest.raises(WebSocketDisconnect) as closed,
        _ws(client, {"origin": "https://evil.example"}),
    ):
        pass
    assert closed.value.code == 4403


def test_proxy_ip_restriction_answers_only_the_proxy(tmp_path: Path) -> None:
    # Behind a reverse proxy (e.g. Home Assistant Ingress at 172.30.32.2): no sign-in, but only the
    # proxy's own address is answered, so nothing else on the network reaches the passwordless app.
    hub = WebHub(
        tmp_path / "files", local_only=False, proxy_ips=frozenset({"172.30.32.2"})
    )
    with TestClient(
        hub.app(), base_url="http://ingress", client=("172.30.32.2", 40000)
    ) as client:
        assert client.get("/").status_code == 200
    # Any other peer (another add-on on the host) is refused, over HTTP and WebSocket alike.
    with TestClient(
        hub.app(), base_url="http://ingress", client=("172.30.32.9", 40000)
    ) as client:
        assert client.get("/").status_code == 403
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            _ws(client, {"host": "ingress"}),
        ):
            pass
        assert closed.value.code == 4403


def test_socket_sends_texts_textures_and_takes_input(hub: WebHub) -> None:
    _, texture = hub.textures.add_encoded(_PNG, 3, 2)
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        _ws(client) as socket,
    ):
        texts = _event(socket.receive_bytes())
        assert texts["t"] == "texts" and texts["texts"]["WEB_TAKE_CONTROL"]
        assert socket.receive_bytes() == texture
        control = _event(socket.receive_bytes())
        assert control == {
            "t": "control",
            "control": True,
            "browsers": 1,
            "text_input": False,
            "pinch_areas": [],
        }
        socket.send_text(json.dumps([{"t": "size", "w": 800, "h": 600, "r": 2}]))
        deadline = time.monotonic() + 5
        events: list[dict[str, object]] = []
        while not events and time.monotonic() < deadline:
            events = [e for e in hub.take_input() if e.get("t") != "focus"]
            time.sleep(0.01)
    assert events == [{"t": "size", "w": 800, "h": 600, "r": 2}]
    assert hub.size == (800.0, 600.0)
    assert hub.scale == 2.0
    assert hub.wake.is_set()


def test_zoom_and_touch_keyboard_go_to_the_controlling_browser(hub: WebHub) -> None:
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        _ws(client) as socket,
    ):
        _receive_until(socket, _is_event("control"))
        socket.send_text(
            json.dumps([{"t": "size", "w": 400, "h": 700, "r": 3, "z": 1.25}])
        )
        deadline = time.monotonic() + 5
        while hub.size is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert hub.zoom == 1.25
        hub.set_zoom(1.5)
        zoom = _receive_until(socket, _is_event("zoom"))[-1]
        assert _event(zoom) == {"t": "zoom", "z": 1.5}
        hub.set_text_input(True, (10, 20, 18))
        typing = _receive_until(socket, _is_event("text-input"))[-1]
        assert _event(typing) == {
            "t": "text-input",
            "on": True,
            "caret": [10, 20, 18],
            "numeric": False,
        }


def test_input_put_back_comes_first(hub: WebHub) -> None:
    hub.put_back([{"t": "c"}])
    hub.put_back([{"t": "a"}, {"t": "b"}])
    assert [e["t"] for e in hub.take_input()] == ["a", "b", "c"]


def test_frames_send_only_new_draw_lists(hub: WebHub) -> None:
    header = struct.pack("<BBHffffI", 1, 2, 0, 0, 0, 100, 100, 2)
    a, b, c = (frames._LIST_HEADER.pack(0, 0, 0) for _ in range(3))
    first = frames.Frame(header, ((b"A" * 8, a), (b"B" * 8, b)))
    second = frames.Frame(header, ((b"A" * 8, a), (b"C" * 8, c)))
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        _ws(client) as socket,
    ):
        _receive_until(socket, _is_event("control"))
        hub.publish([], first)
        one = _receive_until(socket, lambda m: m[0] == frames.KIND_FRAME)[-1]
        hub.publish([], second)
        two = _receive_until(socket, lambda m: m[0] == frames.KIND_FRAME)[-1]
    kinds_one = [(i["kept"], i["hash"]) for i in _parse_frame(_body(one)[1])["lists"]]
    kinds_two = [(i["kept"], i["hash"]) for i in _parse_frame(_body(two)[1])["lists"]]
    assert kinds_one == [(False, b"A" * 8), (False, b"B" * 8)]
    assert kinds_two == [(True, b"A" * 8), (False, b"C" * 8)]


def _browser(client: TestClient, session: str) -> Any:
    """A WebSocket of the browser with this session cookie."""
    return _ws(client, {"cookie": f"xknx_web_session={session}"})


def test_one_browser_controls(hub: WebHub) -> None:
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        _browser(client, "first") as first,
    ):
        _controls(first, True)
        first.send_text(json.dumps([{"t": "move", "x": 1, "y": 1}]))  # active
        with _browser(client, "second") as second:
            _controls(second, False)
            # A watching browser's input is ignored ...
            second.send_text(json.dumps([{"t": "text", "s": "x"}]))
            time.sleep(0.2)
            assert all(e.get("t") != "text" for e in hub.take_input())
            # ... until it takes over; the other one then watches.
            second.send_text(json.dumps([{"t": "take"}]))
            _controls(second, True)
            _controls(first, False)


def test_a_reload_keeps_control(hub: WebHub) -> None:
    with TestClient(hub.app(), base_url="http://localhost") as client:
        with _browser(client, "mine") as first:
            _controls(first, True)
        with _browser(client, "other") as other:
            # Right after the controlling browser left: it may come back (a reload).
            _controls(other, False)
            with _browser(client, "mine") as again:
                _controls(again, True)


def test_an_inactive_browser_loses_control(tmp_path: Path) -> None:
    hub = WebHub(tmp_path / "files", idle_seconds=0.2)
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        _browser(client, "idle") as idle,
    ):
        _controls(idle, True)
        time.sleep(0.4)
        with _browser(client, "new") as new:
            _controls(new, True)
            _controls(idle, False)


def test_control_passes_on_when_the_browser_does_not_return(
    hub: WebHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_RETURN_SECONDS", 0.2)
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        contextlib.ExitStack() as stack,
    ):
        controlling = stack.enter_context(_browser(client, "controlling"))
        _controls(controlling, True)
        controlling.send_text(json.dumps([{"t": "move", "x": 1, "y": 1}]))
        watching = stack.enter_context(_browser(client, "watching"))
        _controls(watching, False)
        controlling.close()
        _controls(watching, True)  # not back within the moment a reload takes


def test_upload_needs_an_open_dialog(hub: WebHub) -> None:
    with TestClient(hub.app(), base_url="http://localhost") as client:
        client.get("/")
        refused = client.post("/upload?dialog=999&name=a.knxproj", content=b"data")
        assert refused.status_code == 409
        dialog = WebOpenFile(hub, "Open")
        stored = client.post(
            f"/upload?dialog={dialog.id}&name=../a.knxproj", content=b"data"
        )
        assert stored.status_code == 200
        assert stored.json() == {"name": "a.knxproj", "size": 4}
        assert (hub.files.root / "a.knxproj").read_bytes() == b"data"
        again = client.post(f"/upload?dialog={dialog.id}&name=a.knxproj", content=b"2")
        assert again.json()["name"] == "a (2).knxproj"
        foreign = client.post(
            f"/upload?dialog={dialog.id}&name=b.knxproj",
            content=b"x",
            headers={"origin": "https://evil.example"},
        )
        assert foreign.status_code == 403


def test_downloads_only_offered_files(hub: WebHub, tmp_path: Path) -> None:
    report = tmp_path / "labels.html"
    report.write_text("<p>labels</p>")
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        _ws(client) as socket,
    ):
        _receive_until(socket, _is_event("control"))
        hub.offer_file(report, show=True)
        shown_event = _event(_receive_until(socket, _is_event("open"))[-1])
        assert shown_event["show"] and shown_event["name"] == "labels.html"
        assert shown_event["url"].endswith("?show=1")
        client.get("/")
        shown = client.get(f"/{shown_event['url']}")
        assert shown.status_code == 200 and shown.text == "<p>labels</p>"
        assert "inline" in shown.headers["content-disposition"]
        assert client.get("/files/1-unknown/x.html").status_code == 404
        hub.offer_file(report, download=True)
        fetch = _event(_receive_until(socket, _is_event("download"))[-1])
        assert fetch["name"] == "labels.html"
        assert (
            "attachment"
            in client.get(f"/{fetch['url']}").headers["content-disposition"]
        )
        hub.open_url("https://example.com/manual.pdf")
        assert _event(_receive_until(socket, _is_event("open"))[-1])["url"] == (
            "https://example.com/manual.pdf"
        )
    hub.show_folder(tmp_path)
    (notice,) = hub.host.items
    assert isinstance(notice, FolderNotice)
    # The hub's own file folder is in there too, as a folder.
    assert [e.name for e in notice.entries] == ["files", "labels.html"]


def test_folder_notice_goes_into_subfolders(hub: WebHub, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    (root / "product-data").mkdir(parents=True)
    (root / "product-data" / "a.pkl").write_bytes(b"x")
    (root / "docs").mkdir()
    (root / "index.json").write_text("{}")
    notice = FolderNotice(hub, root)
    # Folders first, by name, then the files; nothing above the folder shown first.
    assert [(e.name, e.folder) for e in notice.entries] == [
        ("docs", True),
        ("product-data", True),
        ("index.json", False),
    ]
    notice.open(notice.entries[1])
    assert notice.folder == root / "product-data"
    assert [(e.name, e.folder) for e in notice.entries] == [
        ("..", True),
        ("a.pkl", False),
    ]
    notice.open(notice.entries[0])
    assert notice.folder == root
    assert notice.entries[0].name == "docs"


def test_dialog_goes_to_the_controlling_browser(hub: WebHub) -> None:
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        _ws(client) as socket,
    ):
        _receive_until(socket, _is_event("control"))
        dialog = WebSaveFile(hub, "Save", "p.xknx", ["XKNX", "*.xknx"])
        request = _event(_receive_until(socket, _is_event("dialog"))[-1])
        assert request["id"] == dialog.id and request["upload"] is False
        hub.answer_dialog({"t": "dialog", "id": dialog.id, "ok": False})
        closed = _event(_receive_until(socket, _is_event("dialog-close"))[-1])
        assert closed["id"] == dialog.id
        assert dialog.ready()


def test_page_actions_are_sent_when_they_change(hub: WebHub) -> None:
    with (
        TestClient(hub.app(), base_url="http://localhost") as client,
        _ws(client) as socket,
    ):
        _receive_until(socket, _is_event("control"))
        action = {"kind": "download", "url": "files/1/a", "name": "a"}
        hub.page_action("folder-1:a", (10, 20, 30, 40), action)
        hub.page_action("folder-1:a", (10, 20, 30, 40), action)  # unchanged: not sent
        hub.page_action("folder-1:a", (10, 25, 30, 40), action)
        first = _event(_receive_until(socket, _is_event("area"))[-1])
        moved = _event(_receive_until(socket, _is_event("area"))[-1])
        assert (first["y"], moved["y"]) == (20, 25)
        assert first["action"] == action
        hub.clear_actions("folder-1:")
        cleared = _event(_receive_until(socket, _is_event("area-clear"))[-1])
        assert cleared == {"t": "area-clear", "prefix": "folder-1:"}


def test_safari_gets_a_download_notice(hub: WebHub, tmp_path: Path) -> None:
    exported = tmp_path / "p.knxproj"
    exported.write_bytes(b"x")
    notes: list[str] = []
    hub.notify = notes.append
    hub.offer_file(exported, download=True)
    hub.show_later()
    assert hub.host.items == []  # the browser downloads by itself
    hub.auto_download = False
    hub.offer_file(exported, download=True)
    hub.show_later()
    (notice,) = hub.host.items
    assert isinstance(notice, DownloadNotice) and notice.name == "p.knxproj"
    assert len(notes) == 2 and "p.knxproj" in notes[0]


def _frame(draw: Callable[[], None]) -> None:
    io = imgui.get_io()
    io.delta_time = 1 / 60
    imgui.new_frame()
    draw()
    imgui.render()
    for texture in imgui.get_platform_io().textures:
        if texture.status != imgui.ImTextureStatus.ok:
            texture.set_tex_id(1)
            texture.set_status(imgui.ImTextureStatus.ok)


def test_web_dialog_opens_inside_an_open_app_dialog(
    context: imgui.IO, hub: WebHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_dialog, "nested_content", hub.host.draw)
    dialog = WebOpenFile(hub, "Open")
    seen: list[bool] = []

    def app_window() -> None:
        imgui.open_popup("App Dialog")
        if app_dialog.begin_dialog("App Dialog", width=400.0):
            imgui.text("asks for a file")
            seen.append(imgui.is_popup_open(dialog.popup_id))
            app_dialog.end_dialog()  # the web dialog is drawn in here
        hub.host.draw()  # after the app's windows: already drawn this frame

    for _ in range(3):
        _frame(app_window)
    # From the second frame on the web dialog is open one level above the app's dialog.
    assert seen[1:] == [True, True]
    dialog.answer({"ok": False})
    for _ in range(2):
        _frame(app_window)
    assert hub.host.items == []
    assert seen[-1] is False


def test_web_dialog_opens_on_its_own(context: imgui.IO, hub: WebHub) -> None:
    dialog = WebSaveFile(hub, "Save", "p.xknx", ["XKNX", "*.xknx"])
    shown: list[bool] = []

    def windows() -> None:
        hub.host.draw()
        shown.append(imgui.is_popup_open(dialog.popup_id))

    for _ in range(3):
        _frame(windows)
    assert shown[-1]
    dialog.answer({"ok": True, "name": "q", "filter": 0, "download": False})
    _frame(windows)
    assert hub.host.items == []
    assert dialog.result() == str(hub.files.root / "q.xknx")


def test_glyphs_snap_to_whole_pixels_at_fractional_scales() -> None:
    def vertex(x: float, y: float, u: float, v: float) -> bytes:
        return struct.pack("<ffffI", x, y, u, v, 0xFFFFFFFF)

    white = (0.01, 0.01)
    glyph = [
        (10.2, 3, 0.2, 0.2),
        (17.2, 3, 0.3, 0.2),
        (17.2, 15, 0.3, 0.4),
        (10.2, 15, 0.2, 0.4),
    ]
    shape = [(0, 3, *white), (5, 3, *white), (5, 9, *white)]
    data = b"".join(vertex(*v) for v in shape + glyph)
    snapped = frames.snap_to_pixels(data, 1.25)
    values = [struct.unpack_from("<ffffI", snapped, i * 20) for i in range(7)]
    # The shape stays; the glyph's top 3 * 1.25 = 3.75 px moves to 4 px, its height kept.
    assert [v[1] for v in values[:3]] == [3, 3, 9]
    tops = [v[1] * 1.25 for v in values[3:]]
    assert tops[:2] == pytest.approx([4.0, 4.0])
    assert values[5][1] - values[3][1] == pytest.approx(12.0)
    # Its left 10.2 * 1.25 = 12.75 px moves to 13 px, its width kept.
    assert values[3][0] * 1.25 == pytest.approx(13.0)
    assert values[4][0] - values[3][0] == pytest.approx(7.0)
    # Nothing textured: unchanged.
    assert frames.snap_to_pixels(b"".join(vertex(*v) for v in shape), 1.25) == (
        b"".join(vertex(*v) for v in shape)
    )


def test_the_browser_runs_without_a_window_and_with_its_own_touch_gestures() -> None:
    """The touch input arrives already turned into mouse input: Hello ImGui's own gestures,
    which would act on it a second time, are off."""
    params = hello_imgui.RunnerParams()
    assert params.touch_scroll_mode == hello_imgui.TouchScrollMode.auto
    web.windowless(params)
    assert params.platform_backend_type == hello_imgui.PlatformBackendType.null
    assert params.renderer_backend_type == hello_imgui.RendererBackendType.null
    assert params.touch_scroll_mode == hello_imgui.TouchScrollMode.disabled
    assert not params.touch_long_press_is_right_click
    assert not params.touch_thin_scrollbars
    assert params.touch_pinch_mode == hello_imgui.TouchPinchMode.disabled


def test_lines_and_corners_from_the_texture_stay_in_place() -> None:
    """Anti-aliased lines and rounded corners are drawn from the texture too, but take no height
    of it (or no width): they stay, and a glyph after a strip of any length still snaps."""

    def vertex(x: float, y: float, u: float, v: float) -> bytes:
        return struct.pack("<ffffI", x, y, u, v, 0xFFFFFFFF)

    # A horizontal line (top and bottom edge, along it), and a rounded rectangle's top row.
    line = [(10.2, 2.2, 0.48, 0.26), (10.2, 4.2, 0.5, 0.26)]
    line += [(50.2, 2.2, 0.48, 0.26), (50.2, 4.2, 0.5, 0.26)]
    corners = [(10.2, 5.2, 0.58, 0.01), (15.2, 5.2, 0.59, 0.01)]
    corners += [(45.2, 5.2, 0.59, 0.01), (50.2, 5.2, 0.58, 0.01)]
    # A strip of three vertices: the glyph after it starts off the grid of four.
    strip = [(1.1, 1.1, 0.48, 0.26), (2.1, 1.1, 0.5, 0.26), (3.1, 2.1, 0.48, 0.26)]
    glyph = [
        (10.2, 3, 0.2, 0.2),
        (17.2, 3, 0.3, 0.2),
        (17.2, 15, 0.3, 0.4),
        (10.2, 15, 0.2, 0.4),
    ]
    kept = line + corners + strip
    data = b"".join(vertex(*v) for v in kept + glyph)
    snapped = frames.snap_to_pixels(data, 1.25)
    assert snapped[: len(kept) * 20] == data[: len(kept) * 20]
    left, top = struct.unpack_from("<ff", snapped, len(kept) * 20)
    assert (left * 1.25, top * 1.25) == pytest.approx((13.0, 4.0))


def test_an_error_in_a_panel_does_not_end_the_frame(hub: WebHub) -> None:
    """A panel that fails half-way (a child window still open) leaves imgui intact; the error is
    reported once, not every frame."""
    reports: list[tuple[str, str]] = []
    hub.report_error = lambda where, error: reports.append((where, error))

    def broken() -> None:
        imgui.begin_child("##inner", imgui.ImVec2(100, 100))
        raise ValueError("boom")

    panel = web.guarded("panel", broken, hub)
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.config_error_recovery_enable_assert = False
        io.config_error_recovery_enable_debug_log = False
        for _ in range(3):
            imgui.new_frame()
            imgui.begin("Panel")
            panel()
            imgui.end()
            imgui.render()
    finally:
        imgui.destroy_context(context)
    assert reports == [("panel", "ValueError: boom")]
