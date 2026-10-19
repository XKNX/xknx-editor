"""Tests of the browser UI: frame and texture encoding, key mapping, options and the server."""

from __future__ import annotations

import json
import struct
import time
import zlib
from collections.abc import Iterator

import pytest
from imgui_bundle import imgui
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from editor_gui import web
from editor_gui.web import frames
from editor_gui.web.keys import imgui_key
from editor_gui.web.server import WebHub

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000030000000208060000009d7466"
    "1a0000001149444154789c63f8cfc0f01f86199039009b7e0bf572b0b93c0000"
    "000049454e44ae426082"
)


@pytest.fixture
def context() -> Iterator[imgui.IO]:
    """A Dear ImGui context whose textures this backend handles."""
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(640, 480)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield io
    imgui.destroy_context(ctx)


def _render(mirror: frames.TextureMirror, text: str = "Hallo ä ö ü") -> list[bytes]:
    """One frame with a window and some text; the texture messages it produced."""
    imgui.get_io().delta_time = 1 / 60
    imgui.new_frame()
    imgui.begin("Window")
    imgui.text(text)
    imgui.end()
    imgui.render()
    return mirror.process(imgui.get_platform_io().textures)


def _body(message: bytes) -> tuple[int, bytes]:
    return message[0], zlib.decompress(message[1:])


def _parse_frame(body: bytes) -> dict[str, object]:
    """The frame layout the browser reads (see frames.py)."""
    cursor, index_size, _, x, y, w, h, count = struct.unpack_from("<BBHffffI", body)
    off = 24
    lists = []
    for _ in range(count):
        vertices, indices, commands = struct.unpack_from("<III", body, off)
        off += 12 + vertices * 20
        index_bytes = indices * index_size
        off += index_bytes + (4 - index_bytes % 4) % 4
        cmds = [
            struct.unpack_from("<ffffIIII", body, off + 32 * i) for i in range(commands)
        ]
        off += 32 * commands
        lists.append({"vertices": vertices, "indices": indices, "commands": cmds})
    assert off == len(body)
    return {
        "cursor": cursor - 1,
        "index_size": index_size,
        "display": (x, y, w, h),
        "lists": lists,
    }


def test_frame_round_trip(context: imgui.IO) -> None:
    mirror = frames.TextureMirror()
    created = _render(mirror)
    _render(mirror)  # a new window shows from its second frame on
    draw_data = imgui.get_draw_data()
    body = frames.encode_frame(draw_data, int(imgui.MouseCursor_.text_input))

    frame = _parse_frame(body)
    assert frame["cursor"] == int(imgui.MouseCursor_.text_input)
    assert frame["index_size"] in (2, 4)
    assert frame["display"] == (0.0, 0.0, 640.0, 480.0)
    lists = frame["lists"]
    assert isinstance(lists, list)
    assert sum(item["vertices"] for item in lists) == draw_data.total_vtx_count
    assert sum(item["indices"] for item in lists) == draw_data.total_idx_count
    # Every draw command uses the font atlas texture the mirror created.
    ((kind, texture),) = (_body(m) for m in created)
    assert kind == frames.KIND_TEXTURE
    atlas_id = struct.unpack_from("<I", texture)[0]
    used = {cmd[4] for item in lists for cmd in item["commands"] if cmd[7]}
    assert used == {atlas_id}


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
    assert struct.unpack_from("<IB3xII", body) == (texture_id, frames.TEX_ENCODED, 3, 2)
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


def test_keys() -> None:
    assert imgui_key("KeyA") == imgui.Key.a
    assert imgui_key("Digit3") == imgui.Key._3
    assert imgui_key("Numpad7") == imgui.Key.keypad7
    assert imgui_key("ArrowLeft") == imgui.Key.left_arrow
    assert imgui_key("F12") == imgui.Key.f12
    assert imgui_key("ControlRight") == imgui.Key.right_ctrl
    assert imgui_key("NoSuchKey") is None


def test_web_address() -> None:
    assert web.web_address(["app"]) is None
    assert web.web_address(["app", "--web"]) == ("127.0.0.1", 8080)
    assert web.web_address(["app", "--web=9000"]) == ("127.0.0.1", 9000)
    assert web.web_address(["app", "--web=0.0.0.0:9001"]) == ("0.0.0.0", 9001)
    assert web.web_address(["app", "project.xknx", "--web=:9002"]) == (
        "127.0.0.1",
        9002,
    )


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
    assert io.key_ctrl
    assert imgui.is_key_down(imgui.Key.c)
    imgui.end_frame()


def test_server_needs_the_token() -> None:
    hub = WebHub(token="secret")
    with TestClient(hub.app()) as client:
        assert client.get("/").status_code == 403
        assert client.get("/?token=wrong").status_code == 403
        page = client.get("/?token=secret")
        assert page.status_code == 200
        assert "client.js" in page.text
        assert client.get("/client.js").status_code == 200
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            client.websocket_connect("/ws?token=wrong"),
        ):
            pass
        assert closed.value.code == 4403


def test_server_sends_textures_and_takes_input() -> None:
    hub = WebHub(token="secret")
    _, message = hub.textures.add_encoded(_PNG, 3, 2)
    with (
        TestClient(hub.app()) as client,
        client.websocket_connect("/ws?token=secret") as socket,
    ):
        assert socket.receive_bytes() == message
        socket.send_text(json.dumps([{"t": "size", "w": 800, "h": 600, "r": 2}]))
        deadline = time.monotonic() + 5
        events: list[dict[str, object]] = []
        while not events and time.monotonic() < deadline:
            events = hub.take_input()
            time.sleep(0.01)
    assert events == [{"t": "size", "w": 800, "h": 600, "r": 2}]
    assert hub.size == (800.0, 600.0)
    assert hub.scale == 2.0
    assert hub.wake.is_set()
