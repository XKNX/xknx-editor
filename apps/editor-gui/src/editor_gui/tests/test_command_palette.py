"""The command palette lists devices and group addresses as their lists name them, each setting
where it is, and finds them by any of their words."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from editor_gui.device import UnloadedDevice
from editor_gui.main import KnxGuiApp, PaletteItem
from editor_gui.settings_window import Setting, SettingsPage, SettingsWindow, matches
from editor_gui.strings import S


class _Service:
    def __init__(self) -> None:
        self.is_open = True
        self.loading = False
        self.generation = 1
        self.revision = 1
        self.devices = [
            UnloadedDevice(1, "", "Glastaster II Smart", "1.0.5", None),
            UnloadedDevice(2, "Kitchen light", "Switch actuator", "1.0.6", None),
            UnloadedDevice(3, "", "Power supply", "", None),
        ]
        self.group_addresses = [
            SimpleNamespace(id=10, address="1/2/3", name="Kitchen on/off"),
            SimpleNamespace(id=11, address="1/2/4", name=""),
        ]
        self.selected_device: Any = None
        self.focused: list[str] = []
        self.requested: list[int] = []

    def focus_editor(self) -> None:
        self.focused.append("editor")

    def focus_group_addresses(self) -> None:
        self.focused.append("group_addresses")

    def request_group_address(self, ga_id: int) -> None:
        self.requested.append(ga_id)


def _app(service: _Service) -> Any:
    return SimpleNamespace(
        _project_service=service, _palette_project_items=((-1, -1), [])
    )


def test_devices_and_group_addresses_are_named_as_in_their_lists() -> None:
    service = _Service()
    app = _app(service)
    items = KnxGuiApp._palette_project(app)  # pyright: ignore[reportPrivateUsage]
    labels = [item.label for item in items]
    # An unnamed device shows its product, as in the topology; one without an address its name.
    assert labels == [
        "1.0.5  Glastaster II Smart",
        "1.0.6  Kitchen light",
        "Power supply",
        "1/2/3  Kitchen on/off",
        "1/2/4",
    ]
    items[1].action()
    assert service.selected_device is service.devices[1]
    items[3].action()
    assert service.requested == [10]
    assert service.focused == ["editor", "group_addresses"]


def test_the_project_entries_are_built_again_only_after_a_change() -> None:
    service = _Service()
    app = _app(service)
    first = KnxGuiApp._palette_project(app)  # pyright: ignore[reportPrivateUsage]
    assert KnxGuiApp._palette_project(app) is first  # pyright: ignore[reportPrivateUsage]
    service.revision += 1
    assert KnxGuiApp._palette_project(app) is not first  # pyright: ignore[reportPrivateUsage]
    service.loading = True
    assert KnxGuiApp._palette_project(app) == []  # pyright: ignore[reportPrivateUsage]


def test_each_setting_opens_its_page() -> None:
    page = SettingsPage(
        "connection",
        lambda: "Connection",
        [Setting("serial", lambda: "Use Serial Number", "bool", get=lambda: True)],
    )
    window = SettingsWindow(lambda: [page])
    opened: list[str] = []
    window.open = lambda key="": opened.append(key)  # type: ignore[method-assign]
    app = SimpleNamespace(_settings_window=window)
    (item,) = KnxGuiApp._palette_settings(app)  # pyright: ignore[reportPrivateUsage]
    assert item.label == "Use Serial Number"
    assert item.where == f"{S.SETTINGS_TITLE} > Connection"
    item.action()
    assert opened == ["connection"]


def test_an_entry_is_found_by_its_words_and_where_it_is() -> None:
    item = PaletteItem("1.0.5  Glastaster II Smart", "Device", lambda: None)
    assert matches("glas 1.0.5", (item.label, item.where))
    assert matches("device smart", (item.label, item.where))
    assert not matches("aktor", (item.label, item.where))
