"""KNX RF USB interfaces in the editor: their medium and domain address shown, and the domain
address set on the interface (against the simulated interface of conftest)."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui
from xknx.exceptions import CommunicationError
from xknx.knxip.knxip_enum import KNXMedium

from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection import usb
from editor_gui.plugins.connection.plugin import ConnectionPlugin, ConnectionState
from editor_gui.plugins.connection.usb import (
    medium_name,
    parse_rf_domain_address,
    rf_domain_address_text,
)
from editor_gui.plugins.connection.xknx_usb.const import DeviceFeature
from editor_gui.plugins.project.tests._clicks import Clicks
from xknxeditor.proj.core.medium import MEDIUM_RF, MEDIUM_TP

from .conftest import FakeHIDModule
from .test_usb_connection import _connected, _property_writes

DOMAIN_ADDRESS = bytes.fromhex("00fa12345678")


def _rf(fake_hid: FakeHIDModule) -> None:
    """Make the simulated interface a KNX RF interface (mask 2311h)."""
    fake_hid.interface.features[DeviceFeature.DEVICE_DESCRIPTOR_TYPE_0] = b"\x23\x11"
    fake_hid.interface.properties[(19, 56)] = DOMAIN_ADDRESS


def test_texts() -> None:
    assert [medium_name(medium) for medium in KNXMedium] == ["TP", "PL", "RF", "IP"]
    assert rf_domain_address_text(DOMAIN_ADDRESS) == "00FA:12345678"
    assert rf_domain_address_text(None) == ""
    assert parse_rf_domain_address(" 00fa:12345678 ") == DOMAIN_ADDRESS
    for text in ("", "00FA12345678", "x:y", "10000:00000000", "-1:0"):
        with pytest.raises(ValueError, match=f"^{re.escape(text)}$"):
            parse_rf_domain_address(text)


async def test_an_rf_interface_is_described(fake_hid: FakeHIDModule) -> None:
    _rf(fake_hid)
    plugin = await _connected(fake_hid)
    try:
        assert plugin.usb_info.medium is KNXMedium.RF
        assert plugin.usb_info.rf_domain_address == DOMAIN_ADDRESS
        # recordings name the medium
        assert plugin._link_info().medium == "Rf"
    finally:
        await plugin._disconnect_async()


async def test_a_tp_interface_is_described(fake_hid: FakeHIDModule) -> None:
    plugin = await _connected(fake_hid)
    try:
        assert plugin._link_info().medium == "Tp"
        with pytest.raises(ValueError, match="not a KNX RF interface"):
            plugin.set_usb_interface_domain_address("00FA:12345678")
    finally:
        await plugin._disconnect_async()


class TestDomainAddress:
    """The domain address of the connected KNX RF USB interface."""

    async def test_set(self, fake_hid: FakeHIDModule) -> None:
        _rf(fake_hid)
        plugin = await _connected(fake_hid)
        try:
            fake_hid.interface.written.clear()
            plugin.test_events.clear()
            future = plugin.set_usb_interface_domain_address(" 00fa:00000042 ")
            assert plugin.usb_domain_address_busy
            # nothing else is changed meanwhile
            with pytest.raises(ValueError, match="domain address of the interface"):
                plugin.set_usb_interface_address("1.0.12")
            with pytest.raises(ValueError, match="domain address of the interface"):
                plugin.set_usb_interface_domain_address("00FA:00000043")
            await asyncio.wrap_future(future)
            assert not plugin.usb_domain_address_busy
            # set in the RF Medium Object on the open connection
            assert _property_writes(fake_hid) == ["f6001301381001" + "00fa00000042"]
            assert plugin._state is ConnectionState.CONNECTED
            assert plugin.usb_info.rf_domain_address == bytes.fromhex("00fa00000042")
            assert "link False" not in plugin.test_events
        finally:
            await plugin._disconnect_async()

    async def test_the_one_it_has(self, fake_hid: FakeHIDModule) -> None:
        _rf(fake_hid)
        plugin = await _connected(fake_hid)
        try:
            fake_hid.interface.written.clear()
            future = plugin.set_usb_interface_domain_address("00FA:12345678")
            assert future.done() and future.result() is None
            assert not fake_hid.interface.written
            assert not plugin.usb_domain_address_busy
        finally:
            await plugin._disconnect_async()

    async def test_refused(self, fake_hid: FakeHIDModule) -> None:
        _rf(fake_hid)
        plugin = await _connected(fake_hid)
        fake_hid.interface.refused_writes[(19, 56)] = 0x0B
        try:
            with pytest.raises(ConnectionError, match="could not be set"):
                await asyncio.wrap_future(
                    plugin.set_usb_interface_domain_address("00FA:00000042")
                )
            assert not plugin.usb_domain_address_busy
            assert "Could not set the RF Domain Address" in plugin._usb_domain_error
            # still connected, with the domain address as it was
            assert plugin._state is ConnectionState.CONNECTED
            assert plugin.usb_info.rf_domain_address == DOMAIN_ADDRESS
            assert fake_hid.interface.properties[(19, 56)] == DOMAIN_ADDRESS
            # the connection went away meanwhile
            interface, plugin._interface = plugin._interface, None
            with pytest.raises(ConnectionError, match="Not connected"):
                await plugin._write_usb_domain_address(bytes(6))
            plugin._interface = interface
        finally:
            await plugin._disconnect_async()

    async def test_refused_before_it_starts(self, fake_hid: FakeHIDModule) -> None:
        plugin = await _connected(fake_hid)
        await plugin._disconnect_async()
        with pytest.raises(ValueError, match="is not an RF domain address"):
            plugin.set_usb_interface_domain_address("1.0.12")
        with pytest.raises(ValueError, match="Not connected through a USB interface"):
            plugin.set_usb_interface_domain_address("00FA:12345678")
        _rf(fake_hid)
        plugin = await _connected(fake_hid)
        try:
            plugin._usb_address_busy = True
            with pytest.raises(ValueError, match="being set or checked"):
                plugin.set_usb_interface_domain_address("00FA:00000042")
        finally:
            plugin._usb_address_busy = False
            await plugin._disconnect_async()


def _project(lines: dict[int, list[Any]]) -> Any:
    areas = [SimpleNamespace(id=area_id, area_number=area_id) for area_id in lines]
    return SimpleNamespace(
        revision=0,
        loading=False,
        is_open=True,
        get_areas=lambda: areas,
        get_lines=lambda area_id: lines[area_id],
    )


def test_the_project_rf_lines() -> None:
    plugin = object.__new__(ConnectionPlugin)
    plugin._api = SimpleNamespace(  # type: ignore[attr-defined]
        project=_project(
            {
                1: [
                    SimpleNamespace(
                        line_number=1,
                        name="TP",
                        medium_type=MEDIUM_TP,
                        domain_address=None,
                    ),
                    SimpleNamespace(
                        line_number=2,
                        name="Garden",
                        medium_type=MEDIUM_RF,
                        domain_address=0x00FA12345678,
                    ),
                    SimpleNamespace(
                        line_number=3,
                        name="",
                        medium_type=MEDIUM_RF,
                        domain_address=None,
                    ),
                ],
                2: [
                    SimpleNamespace(
                        line_number=4, name="", medium_type=MEDIUM_RF, domain_address=1
                    )
                ],
            }
        )
    )
    assert plugin.project_rf_domain_addresses() == [
        ("1.2 Garden", "00FA:12345678"),
        ("2.4", "0000:00000001"),
    ]


async def test_the_dialog(
    fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rf(fake_hid)
    plugin = await _connected(fake_hid)
    plugin._api.project = _project(
        {
            1: [
                SimpleNamespace(
                    line_number=2,
                    name="Garden",
                    medium_type=MEDIUM_RF,
                    domain_address=0x00FA00000042,
                )
            ]
        }
    )
    started: list[str] = []
    plugin.set_usb_interface_domain_address = started.append
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    clicks = Clicks(monkeypatch, plugin_module)
    pressed: set[str] = set()
    chosen: list[str] = []
    entered: list[bool] = []
    shown: list[str] = []
    real_button, real_input = imgui.button, imgui.input_text
    real_text, real_combo = imgui.text, plugin_module.begin_combo
    real_selectable = imgui.selectable

    def button(label: str, *args: Any) -> bool:
        disabled = bool(
            imgui.get_current_context().current_item_flags & imgui.ItemFlags_.disabled
        )
        if label in pressed and not disabled:
            pressed.discard(label)
            return True
        return bool(real_button(label, *args))

    def input_text(label: str, text: str, *args: Any) -> tuple[bool, str]:
        if label == "##usb_domain" and entered:
            entered.pop()
            return True, text
        return real_input(label, text, *args)

    def text(value: str) -> None:
        shown.append(value)
        real_text(value)

    def begin_combo(label: str, preview: str, *args: Any) -> bool:
        return bool(chosen) or bool(real_combo(label, preview, *args))

    def selectable(label: str, *args: Any) -> tuple[bool, bool]:
        if chosen and chosen[0] in label:
            chosen.pop()
            return True, True
        return real_selectable(label, *args)

    monkeypatch.setattr(imgui, "button", button)
    monkeypatch.setattr(imgui, "input_text", input_text)
    monkeypatch.setattr(imgui, "text", text)
    monkeypatch.setattr(plugin_module, "begin_combo", begin_combo)
    monkeypatch.setattr(plugin_module, "end_combo", lambda: None)
    monkeypatch.setattr(imgui, "selectable", selectable)

    def frames(count: int = 2) -> None:
        for _ in range(count):
            imgui.new_frame()
            plugin.render_window()
            imgui.end_frame()

    plugin._select_open = plugin._select_shown = plugin._progmode_open = False
    try:
        plugin.open_usb_interface()
        assert plugin._usb_domain_text == "00FA:12345678"
        frames()
        assert "Medium: RF" in shown
        assert "Domain Address" in shown
        # the address it has: nothing to apply
        pressed.add("Apply##usb_domain_apply")
        frames()
        assert started == []
        pressed.clear()
        # one of the project's RF lines fills it in, Apply or Enter sets it
        chosen.append("1.2 Garden")
        frames(1)
        assert plugin._usb_domain_text == "00FA:00000042"
        pressed.add("Apply##usb_domain_apply")
        frames()
        entered.append(True)
        frames()
        assert started == ["00FA:00000042", "00FA:00000042"]
        # what the dialog shows: an error, a running change
        plugin._usb_domain_error = "The domain address could not be set: x"
        frames()
        plugin._usb_domain_busy = True
        frames()
        plugin._usb_domain_busy = False
        # a reason it can't start is shown in the dialog
        plugin.set_usb_interface_domain_address = (
            ConnectionPlugin.set_usb_interface_domain_address.__get__(plugin)
        )
        plugin._usb_domain_text = "nonsense"
        pressed.add("Apply##usb_domain_apply")
        frames()
        assert "is not an RF domain address" in plugin._usb_domain_error
        clicks.button = "Close"
        frames()
        assert not plugin._usb_interface_open
    finally:
        imgui.destroy_context(ctx)
        await plugin._disconnect_async()


async def test_the_interface_gets_a_devices_domain_address(
    fake_hid: FakeHIDModule,
) -> None:
    from xknx import XKNX

    from editor_gui.plugins.connection.usb import ensure_rf_domain_address

    _rf(fake_hid)
    plugin = await _connected(fake_hid)
    new = bytes.fromhex("00fa00000042")
    try:
        fake_hid.interface.written.clear()
        assert usb.is_rf_usb_connection(plugin._xknx)
        assert await ensure_rf_domain_address(plugin._xknx, new)
        assert _property_writes(fake_hid) == ["f6001301381001" + new.hex()]
        assert plugin.usb_info.rf_domain_address == new
        # the one it has: nothing is written
        assert not await ensure_rf_domain_address(plugin._xknx, new)
        assert len(_property_writes(fake_hid)) == 1
    finally:
        await plugin._disconnect_async()
    # no USB connection: nothing to do
    assert not usb.is_rf_usb_connection(XKNX())
    assert not await ensure_rf_domain_address(XKNX(), new)
    # a TP interface: nothing to do
    fake_hid.interface.features[DeviceFeature.DEVICE_DESCRIPTOR_TYPE_0] = b"\x07\xb0"
    plugin = await _connected(fake_hid)
    try:
        assert not usb.is_rf_usb_connection(plugin._xknx)
        assert not await ensure_rf_domain_address(plugin._xknx, new)
    finally:
        await plugin._disconnect_async()
    # the interface is gone meanwhile
    interface = usb.ObservableUSBInterfaceThreaded(XKNX(), usb.UsbTarget(1, 2, "", "x"))
    with pytest.raises(CommunicationError, match="not connected"):
        await interface.set_rf_domain_address(new)


def test_an_rf_multi_usb_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from editor_gui.plugins.connection import usb as usb_module

    class _Interface:
        def __init__(self, info: object) -> None:
            self.info = info

        def usb_info(self) -> object:
            return self.info

    monkeypatch.setattr(usb_module, "ObservableUSBInterfaceThreaded", _Interface)
    xknx = MagicMock()
    xknx.knxip_interface = _Interface(
        SimpleNamespace(medium=usb_module.KNXMedium.RF, rf_multi=True)
    )
    assert usb_module.is_rf_multi_usb_connection(xknx)
    xknx.knxip_interface = _Interface(None)
    assert not usb_module.is_rf_multi_usb_connection(xknx)
    xknx.knxip_interface = object()
    assert not usb_module.is_rf_multi_usb_connection(xknx)
