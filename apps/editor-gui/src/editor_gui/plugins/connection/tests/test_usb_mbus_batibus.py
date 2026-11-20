"""The frames of a KNX USB interface's M-Bus and BatiBus tunnels: kept, shown in the USB Interface
dialog, and sent from it (not while Block Bus Writes is on)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.mbus_batibus import (
    MbusBatibusFrameLog,
    parse_hex,
    parse_octet,
    parse_protocol,
    protocol_key,
    protocol_name,
)
from editor_gui.plugins.connection.strings import S
from editor_gui.plugins.connection.xknx_usb import ProtocolID, USBTransferFrame
from editor_gui.plugins.project.tests._clicks import Clicks

from .conftest import FakeHIDModule
from .test_usb_connection import _connected, _plugin


async def _until(condition: Any, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def _frame(body: bytes, protocol: int = ProtocolID.M_BUS_TUNNEL) -> USBTransferFrame:
    return USBTransferFrame(protocol=protocol, service=0x01, body=body)


class TestLog:
    def test_the_last_ones_are_kept(self) -> None:
        log = MbusBatibusFrameLog(max_frames=3)
        for number in range(5):
            log.add(_frame(bytes((number,))), sent=number == 4)
        frames = log.frames()
        assert [frame.id for frame in frames] == [3, 4, 5]
        assert [frame.body for frame in frames] == [b"\x02", b"\x03", b"\x04"]
        assert frames[-1].sent and not frames[0].sent
        assert frames[0].service == 0x01 and frames[0].manufacturer_code == 0
        assert frames[0].time.tzinfo is not None
        assert len(log) == 3 and log.last_id == 5
        assert [frame.id for frame in log.frames(since=3)] == [4, 5]
        assert [frame.id for frame in log.frames(limit=1)] == [5]
        assert log.frames(limit=0) == []
        log.clear()
        assert log.frames() == [] and log.last_id == 5
        # the numbering goes on
        assert log.add(_frame(b""), sent=False).id == 6

    def test_texts(self) -> None:
        assert protocol_name(ProtocolID.M_BUS_TUNNEL) == "M-Bus"
        assert protocol_name(ProtocolID.BATIBUS_TUNNEL) == "BatiBus"
        assert protocol_name(4) == "04h"
        assert protocol_key(ProtocolID.BATIBUS_TUNNEL) == "batibus"
        assert protocol_key(9) == "9"
        assert parse_protocol(" M-Bus ") is ProtocolID.M_BUS_TUNNEL
        assert parse_protocol("batibus") is ProtocolID.BATIBUS_TUNNEL
        with pytest.raises(ValueError):
            parse_protocol("knx")
        assert parse_hex("44 AE 0c") == bytes.fromhex("44ae0c")
        assert parse_hex("0x44ae") == bytes.fromhex("44ae")
        assert parse_hex("") == b""
        with pytest.raises(ValueError):
            parse_hex("4")
        assert parse_octet("f") == 0x0F
        assert parse_octet(" FF ") == 0xFF
        for text in ("0102", "100", "x"):
            with pytest.raises(ValueError):
                parse_octet(text)


class TestSending:
    async def test_sent_and_received(self, fake_hid: FakeHIDModule) -> None:
        plugin = await _connected(fake_hid)
        try:
            await asyncio.wrap_future(
                plugin.send_mbus_batibus_frame(
                    ProtocolID.M_BUS_TUNNEL, 0x05, bytes.fromhex("44ae0c")
                )
            )
            # the simulated interface echoes it
            await _until(lambda: len(plugin.mbus_batibus_frames()) == 2)
            sent, received = sorted(
                plugin.mbus_batibus_frames(), key=lambda frame: not frame.sent
            )
            assert sent.sent and not received.sent
            assert received.body == bytes.fromhex("44ae0c")
            assert received.service == 0x05
            assert received.protocol == ProtocolID.M_BUS_TUNNEL
            assert plugin.mbus_batibus_last_id == 2
            # one of the interface's own
            fake_hid.interface.send_frame(
                USBTransferFrame(
                    protocol=ProtocolID.BATIBUS_TUNNEL, service=0, body=b"\x99"
                )
            )
            await _until(lambda: plugin.mbus_batibus_last_id == 3)
            assert plugin.mbus_batibus_frames(since=2)[0].body == b"\x99"
            plugin.clear_mbus_batibus_frames()
            assert plugin.mbus_batibus_frames() == []
        finally:
            await plugin._disconnect_async()

    async def test_refused(self, fake_hid: FakeHIDModule) -> None:
        plugin = _plugin()
        with pytest.raises(ValueError, match="Not connected through a USB interface"):
            plugin.send_mbus_batibus_frame(ProtocolID.M_BUS_TUNNEL, 0, b"\x01")
        with pytest.raises(ValueError, match="not an M-Bus or BatiBus"):
            plugin.send_mbus_batibus_frame(ProtocolID.KNX_TUNNEL, 0, b"\x01")
        with pytest.raises(ValueError, match="not a hexadecimal octet"):
            plugin.send_mbus_batibus_frame(ProtocolID.M_BUS_TUNNEL, 256, b"\x01")
        # a frame needs data
        with pytest.raises(ValueError, match="Enter the data to send"):
            plugin.send_mbus_batibus_frame(ProtocolID.M_BUS_TUNNEL, 0, b"")
        plugin = await _connected(fake_hid)
        try:
            # Block Bus Writes
            plugin._api.connection.writes_refused = lambda op: True
            with pytest.raises(ValueError, match="Block Bus Writes is on"):
                plugin.send_mbus_batibus_frame(ProtocolID.M_BUS_TUNNEL, 0, b"\x01")
            assert not fake_hid.interface.written or all(
                frame.protocol != ProtocolID.M_BUS_TUNNEL
                for frame in fake_hid.interface.written
            )
            plugin._api.connection.writes_refused = lambda op: False
            # too long for the USB transfer
            with pytest.raises(ConnectionError, match="could not be sent"):
                await asyncio.wrap_future(
                    plugin.send_mbus_batibus_frame(
                        ProtocolID.M_BUS_TUNNEL, 0, bytes(64 * 256)
                    )
                )
        finally:
            await plugin._disconnect_async()


def _imgui() -> Any:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    return ctx


class TestDialog:
    async def test_frames_shown_and_sent(
        self, fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin = await _connected(fake_hid)
        sent: list[tuple[int, int, bytes]] = []
        real_send = plugin.send_mbus_batibus_frame

        def send(protocol: int, service: int, body: bytes) -> Any:
            sent.append((protocol, service, body))
            return real_send(protocol, service, body)

        plugin.send_mbus_batibus_frame = send
        ctx = _imgui()
        clicks = Clicks(monkeypatch, plugin_module)
        monkeypatch.setattr(plugin_module, "collapsing_header", lambda label: True)
        texts: list[str] = []
        for name in ("text", "text_wrapped", "text_disabled"):
            real = getattr(imgui, name)

            def recorded(text: str, *args: Any, _real: Any = real) -> None:
                texts.append(text)
                _real(text, *args)

            monkeypatch.setattr(imgui, name, recorded)
        plugin._select_open = plugin._select_shown = plugin._progmode_open = False

        def frames(count: int = 2) -> None:
            for _ in range(count):
                imgui.new_frame()
                plugin.render_window()
                imgui.end_frame()

        try:
            plugin.open_usb_interface()
            frames()
            assert S.MBUS_FRAMES_HINT in texts
            # invalid entries are named
            plugin._mbus_service_text = "xyz"
            clicks.items.add(f"{S.MBUS_FRAME_SEND}##mbus_send")
            frames()
            assert plugin._mbus_error == S.MBUS_FRAME_INVALID_SERVICE.format(
                service="xyz"
            )
            plugin._mbus_service_text = "0a"
            plugin._mbus_data_text = "4"
            clicks.items.add(f"{S.MBUS_FRAME_SEND}##mbus_send")
            frames()
            assert plugin._mbus_error == S.MBUS_FRAME_INVALID_DATA.format(data="4")
            # BatiBus chosen, then sent
            clicks.combos.add("##mbus_protocol")
            clicks.items.add(f"BatiBus##mbus_protocol_{int(ProtocolID.BATIBUS_TUNNEL)}")
            frames()
            # without data Send is off
            plugin._mbus_data_text = ""
            empty: list[bool] = []
            unpatched = imgui.button

            def send_button(label: str, *args: Any) -> bool:
                if label == f"{S.MBUS_FRAME_SEND}##mbus_send":
                    empty.append(
                        bool(
                            imgui.get_current_context().current_item_flags
                            & imgui.ItemFlags_.disabled
                        )
                    )
                return bool(unpatched(label, *args))

            monkeypatch.setattr(imgui, "button", send_button)
            frames()
            assert empty and all(empty)
            monkeypatch.setattr(imgui, "button", unpatched)
            plugin._mbus_data_text = "44 AE 0C"
            clicks.items.add(f"{S.MBUS_FRAME_SEND}##mbus_send")
            frames()
            assert sent == [(ProtocolID.BATIBUS_TUNNEL, 0x0A, bytes.fromhex("44ae0c"))]
            await _until(lambda: len(plugin.mbus_batibus_frames()) == 2)
            await _until(lambda: not plugin._mbus_sending)
            texts.clear()
            frames()
            assert S.MBUS_FRAME_RECEIVED in texts and S.MBUS_FRAME_SENT in texts
            assert "BatiBus" in texts and "0A" in texts
            # cleared
            clicks.items.add(f"{S.MBUS_FRAMES_CLEAR}##mbus_clear")
            frames()
            assert plugin.mbus_batibus_frames() == []
            texts.clear()
            frames()
            assert S.MBUS_FRAMES_NONE in texts
            # Clear stays shown, disabled while there is nothing to clear
            cleared: list[bool] = []
            plain = imgui.button

            def clear_button(label: str, *args: Any) -> bool:
                if label == f"{S.MBUS_FRAMES_CLEAR}##mbus_clear":
                    cleared.append(
                        bool(
                            imgui.get_current_context().current_item_flags
                            & imgui.ItemFlags_.disabled
                        )
                    )
                return bool(plain(label, *args))

            monkeypatch.setattr(imgui, "button", clear_button)
            frames()
            assert cleared and all(cleared)
            monkeypatch.setattr(imgui, "button", plain)
            # Block Bus Writes: Send is off
            plugin._api.connection.block_bus_writes = True
            disabled: list[bool] = []
            clicked = imgui.button

            def button(label: str, *args: Any) -> bool:
                if label == f"{S.MBUS_FRAME_SEND}##mbus_send":
                    disabled.append(
                        bool(
                            imgui.get_current_context().current_item_flags
                            & imgui.ItemFlags_.disabled
                        )
                    )
                return bool(clicked(label, *args))

            monkeypatch.setattr(imgui, "button", button)
            frames()
            assert disabled and all(disabled)
            plugin._api.connection.block_bus_writes = False
            # a failure is shown
            plugin._mbus_error = "The frame could not be sent: x"
            texts.clear()
            frames()
            assert "The frame could not be sent: x" in texts
        finally:
            imgui.destroy_context(ctx)
            await plugin._disconnect_async()
