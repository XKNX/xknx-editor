"""The browser UI's tray icon: its menu, the icon's pixels, and each operating system's backend on
the system it runs on."""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import threading
import zlib
from typing import Any

import pytest

from editor_gui.web import web_options
from editor_gui.web.tray import ICON, OPEN, STOP, TrayMenu, has_desktop
from editor_gui.web.tray.png import _paeth, argb_pixels, rgba_pixels


def _menu(calls: list[str]) -> TrayMenu:
    return TrayMenu(
        "XKNX Editor - http://localhost:8080/",
        "Open in Browser",
        "Stop XKNX Editor",
        lambda: calls.append("open"),
        lambda: calls.append("stop"),
    )


def _png(width: int, rows: list[tuple[int, bytes]]) -> bytes:
    """An RGBA PNG from rows given as (filter method, filtered bytes)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, len(rows), 8, 6, 0, 0, 0)
    raw = b"".join(bytes([method]) + data for method, data in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_the_menu_runs_its_commands_and_survives_their_errors() -> None:
    calls: list[str] = []
    menu = _menu(calls)
    menu.run(OPEN)
    menu.run(STOP)
    menu.run(0)
    assert calls == ["open", "stop"]

    def fail() -> None:
        raise RuntimeError("no browser")

    TrayMenu("t", "o", "s", fail, fail).run(OPEN)  # logged, not raised


def test_the_option_leaves_the_tray_out() -> None:
    options = web_options(["app", "--web"])
    assert options is not None and options.tray
    options = web_options(["app", "--web", "--web-no-tray"])
    assert options is not None and not options.tray


def test_no_desktop_without_a_display_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert not has_desktop()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert has_desktop()


def test_png_rows_with_every_filter_are_read() -> None:
    first = bytes([10, 20, 30, 255, 40, 50, 60, 128])
    second = bytes([12, 18, 33, 250, 41, 55, 66, 120])
    # Row 1 as "Sub", row 2 as "Paeth" against row 1.
    sub = first[:4] + bytes((first[i] - first[i - 4]) & 0xFF for i in range(4, 8))
    paeth = bytes(
        (
            second[i]
            - _paeth(
                second[i - 4] if i >= 4 else 0, first[i], first[i - 4] if i >= 4 else 0
            )
        )
        & 0xFF
        for i in range(8)
    )
    up = bytes((second[i] - first[i]) & 0xFF for i in range(8))
    average = bytes(
        (second[i] - ((second[i - 4] if i >= 4 else 0) + first[i]) // 2) & 0xFF
        for i in range(8)
    )
    for method, row in ((4, paeth), (2, up), (3, average), (0, second)):
        width, height, pixels = rgba_pixels(_png(2, [(1, sub), (method, row)]))
        assert (width, height, pixels) == (2, 2, first + second), method
    _w, _h, argb = argb_pixels(_png(2, [(0, first), (0, second)]))
    assert argb[:8] == bytes([255, 10, 20, 30, 128, 40, 50, 60])


def test_the_icon_is_64_pixels() -> None:
    width, height, pixels = rgba_pixels(ICON.read_bytes())
    assert (width, height, len(pixels)) == (64, 64, 64 * 64 * 4)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows notification area")
def test_windows_tray_clicks_and_closes() -> None:
    from editor_gui.web.tray import windows

    calls: list[str] = []
    tray = windows.WindowsTray(_menu(calls), ICON.read_bytes())
    hwnd = tray._hwnd  # pyright: ignore[reportPrivateUsage]
    assert hwnd
    # A left click on the icon opens the browser.
    windows._user32.SendMessageW.argtypes = (  # pyright: ignore[reportPrivateUsage]
        windows.wintypes.HWND,
        windows.wintypes.UINT,
        windows.wintypes.WPARAM,
        windows.wintypes.LPARAM,
    )
    windows._user32.SendMessageW(hwnd, windows._WM_TRAY, 1, windows._WM_LBUTTONUP)  # pyright: ignore[reportPrivateUsage]
    assert calls == ["open"]
    tray.close()
    assert not tray._thread.is_alive()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.skipif(sys.platform != "linux", reason="D-Bus StatusNotifierItem")
def test_linux_item_answers_the_tray_host() -> None:
    from dbus_fast import Message, MessageType

    from editor_gui.web.tray.linux import ITEM_PATH, MENU_PATH, StatusNotifierItem

    calls: list[str] = []
    item = StatusNotifierItem(_menu(calls), ICON.read_bytes())

    def call(
        path: str, interface: str, member: str, signature: str, body: list[Any]
    ) -> Any:
        msg = Message(
            destination="x.y",
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body,
            serial=1,
        )
        reply = item.handle(msg)
        assert reply is not None
        assert reply.message_type is MessageType.METHOD_RETURN, reply.body
        return reply.body

    props = call(ITEM_PATH, "org.freedesktop.DBus.Properties", "GetAll", "s", [""])[0]
    assert props["Menu"].value == MENU_PATH
    assert props["IconPixmap"].value[0][:2] == [64, 64]
    _revision, layout = call(
        MENU_PATH, "com.canonical.dbusmenu", "GetLayout", "iias", [0, -1, []]
    )
    labels = [child.value[1].get("label") for child in layout[2]]
    assert [label.value for label in labels if label is not None][1:] == [
        "Open in Browser",
        "Stop XKNX Editor",
    ]
    call(ITEM_PATH, "org.kde.StatusNotifierItem", "Activate", "ii", [0, 0])
    call(
        MENU_PATH,
        "com.canonical.dbusmenu",
        "Event",
        "isvu",
        [STOP, "clicked", _variant(), 0],
    )
    assert calls == ["open", "stop"]


def _variant() -> Any:
    from dbus_fast import Variant

    return Variant("s", "")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS menu bar")
def test_macos_tray_menu_actions() -> None:
    from editor_gui.web.tray.macos import MacTray

    calls: list[str] = []
    tray = MacTray(_menu(calls), ICON.read_bytes())
    menu = tray._item.menu()  # pyright: ignore[reportPrivateUsage]
    for index in (2, 3):
        entry = menu.itemAtIndex_(index)
        entry.target().performSelector_withObject_(entry.action(), entry)
    assert calls == ["open", "stop"]
    tray.pump()
    assert not tray.wait(threading.Event(), 0.05)
    tray.close()


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("dbus-daemon") is None,
    reason="a private D-Bus session bus",
)
def test_linux_tray_registers_with_the_watcher_and_is_clicked() -> None:
    import asyncio

    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    from editor_gui.web.tray.linux import ITEM_PATH, LinuxTray

    daemon = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert daemon.stdout is not None
        address = daemon.stdout.readline().strip()
        registered: list[str] = []
        calls: list[str] = []

        async def scenario() -> None:
            watcher = await MessageBus(
                bus_address=address, bus_type=BusType.SESSION
            ).connect()

            def handle(msg: Message) -> Message | None:
                if msg.member == "RegisterStatusNotifierItem":
                    registered.append(msg.body[0])
                    return Message.new_method_return(msg)
                return None

            watcher.add_message_handler(handle)
            await watcher.request_name("org.kde.StatusNotifierWatcher")
            tray = await asyncio.to_thread(
                LinuxTray, _menu(calls), ICON.read_bytes(), address
            )
            reply = await watcher.call(
                Message(
                    destination=registered[0],
                    path=ITEM_PATH,
                    interface="org.kde.StatusNotifierItem",
                    member="Activate",
                    signature="ii",
                    body=[0, 0],
                )
            )
            assert reply.message_type is MessageType.METHOD_RETURN
            await asyncio.to_thread(tray.close)
            watcher.disconnect()

        asyncio.run(scenario())
        assert registered and registered[0].startswith("org.kde.StatusNotifierItem-")
        assert calls == ["open"]
    finally:
        daemon.terminate()
        daemon.wait(5)
