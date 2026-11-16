"""Protected group addresses: no frame to them leaves the interface, reads included; the setting
is kept, and an MCP client may add to it but not take anything out."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from xknx.dpt import DPTBinary
from xknx.telegram import apci

from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.plugin import ConnectionPlugin
from editor_gui.plugins.connection.write_guard import (
    ProtectedRefused,
    parse_protected,
    protected_patterns,
    protected_reason,
    set_protected,
)

from .test_write_guard import _frame, _interface, _plugin


@pytest.fixture(autouse=True)
def _none_afterwards() -> Iterator[None]:
    yield
    set_protected([])


def test_patterns_are_exact_addresses_or_groups() -> None:
    assert parse_protected("1/2/3, 7/0/  6/0/\n1/2/3") == ["1/2/3", "6/0/", "7/0/"]
    set_protected(["1/2/3", "7/0/"])
    assert protected_patterns() == ["1/2/3", "7/0/"]
    assert protected_reason("1/2/3") == "1/2/3 is a protected group address"
    assert protected_reason("1/2/30") == ""
    assert protected_reason("7/0/9") == "7/0/9 is in the protected group 7/0/"
    assert protected_reason("4/1/0") == ""


def test_the_interface_refuses_reads_and_writes_to_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface, sent = _interface([False], monkeypatch)
    set_protected(["1/2/"])
    for payload in (apci.GroupValueRead(), apci.GroupValueWrite(DPTBinary(1))):
        with pytest.raises(ProtectedRefused, match="protected group 1/2/"):
            asyncio.run(interface.send_cemi(_frame(payload)))
    assert sent == []
    # Frames to devices, and to other group addresses, pass.
    asyncio.run(interface.send_cemi(_frame(apci.DeviceDescriptorRead())))
    set_protected(["1/2/4"])
    asyncio.run(interface.send_cemi(_frame(apci.GroupValueRead())))
    assert len(sent) == 2


def _protecting_plugin() -> Any:
    plugin = _plugin()
    plugin._api = SimpleNamespace(
        connection=SimpleNamespace(use_serial_number=True, block_bus_writes=False)
    )
    return plugin


def test_the_setting_is_kept_and_mcp_may_only_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _protecting_plugin()
    setting = {s.key: s for s in plugin.settings()}["protected_group_addresses"]
    setting.set("7/0/ 1/7/101")
    assert setting.get() == "1/7/101 7/0/"
    stored = plugin_module.load_settings("connection")["protected_group_addresses"]
    assert stored == "1/7/101 7/0/"
    assert setting.mcp_may_set("1/7/101 7/0/ 6/0/")
    assert not setting.mcp_may_set("7/0/")
    set_protected([])
    ConnectionPlugin._load_saved_settings(_protecting_plugin())  # pyright: ignore[reportPrivateUsage]
    assert protected_patterns() == ["1/7/101", "7/0/"]


def test_the_cemi_handler_refuses_a_telegram_to_them_before_data_secure() -> None:
    from xknx.telegram import GroupAddress, Telegram

    from editor_gui.plugins.connection.write_guard import check_telegram

    set_protected(["7/0/"])
    telegram = Telegram(
        destination_address=GroupAddress("7/0/1"), payload=apci.GroupValueRead()
    )
    with pytest.raises(ProtectedRefused, match="protected group 7/0/"):
        check_telegram(None, telegram)
    with pytest.raises(ProtectedRefused):
        check_telegram(lambda: False, telegram)
    # Other group addresses pass while Block Bus Writes is off.
    check_telegram(
        lambda: False, Telegram(GroupAddress("1/0/1"), payload=apci.GroupValueRead())
    )
