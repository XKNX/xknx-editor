"""Compare Devices compares nothing until the user has chosen both devices, and while their
applications load in the background it shows a bar instead of blocking the program."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.ui import device_compare
from editor_gui.plugins.project.ui.device_compare import DeviceComparePanel
from editor_gui.plugins.project.ui.device_diff import DeviceDiffResult


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1200, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _device(node_id: int) -> Any:
    return SimpleNamespace(
        node_id=node_id,
        display_name=f"Device {node_id}",
        individual_address=f"1.1.{node_id}",
        description="",
    )


class _Setup:
    """A panel over three devices; what it compared and what its bar said."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.devices = [_device(1), _device(2), _device(3)]
        self.ready: set[int] = {1, 2, 3}
        self.snapshots: list[int] = []
        self.bars: list[str] = []
        monkeypatch.setattr(
            device_compare,
            "snapshot_device",
            lambda device, _links, _address: self.snapshots.append(device.node_id),
        )
        monkeypatch.setattr(
            device_compare,
            "diff_devices",
            lambda _a, _b: DeviceDiffResult(header=[], parameters=[], com_objects=[]),
        )
        monkeypatch.setattr(
            device_compare,
            "busy_bar",
            lambda _id, _size, text="": self.bars.append(text),
        )
        self.panel = DeviceComparePanel(
            get_devices=lambda: self.devices,
            get_links=lambda _db_id: [],
            get_address=lambda _ga_id: "",
            get_revision=lambda: 0,
            application_ready=lambda device: device.node_id in self.ready,
        )

    def frame(self) -> None:
        imgui.new_frame()
        imgui.begin("Compare")
        self.panel.render()
        imgui.end()
        imgui.end_frame()

    def chosen(self) -> tuple[int | None, int | None]:
        return (self.panel._a_id, self.panel._b_id)  # pyright: ignore[reportPrivateUsage]

    def choose(self, a: int | None, b: int | None) -> None:
        self.panel._a_id, self.panel._b_id = a, b  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def setup(context: None, monkeypatch: pytest.MonkeyPatch) -> _Setup:
    return _Setup(monkeypatch)


def test_nothing_is_compared_until_both_devices_are_chosen(setup: _Setup) -> None:
    setup.frame()
    # No device is chosen at the start, so nothing is compared.
    assert setup.chosen() == (None, None)
    assert setup.snapshots == []
    # "Compare with…" on a device chooses the first one only.
    setup.panel.set_primary(2)
    setup.frame()
    assert setup.chosen() == (2, None)
    assert setup.snapshots == []
    setup.choose(2, 3)
    setup.frame()
    assert setup.snapshots == [2, 3]
    # Compared once, not on every frame.
    setup.frame()
    assert setup.snapshots == [2, 3]


def test_a_bar_shows_while_the_applications_load(setup: _Setup) -> None:
    setup.ready = {1}
    setup.choose(1, 2)
    setup.frame()
    assert setup.snapshots == []
    assert setup.bars == ["Loading application programs… 1/2"]
    # Compared as soon as both are loaded.
    setup.ready = {1, 2}
    setup.frame()
    assert setup.snapshots == [1, 2]


def test_a_device_chosen_twice_or_gone_is_chosen_no_more(setup: _Setup) -> None:
    setup.choose(1, 1)
    setup.frame()
    assert setup.chosen() == (1, None)
    setup.choose(1, 2)
    setup.devices = [_device(2), _device(3)]
    setup.frame()
    assert setup.chosen() == (None, 2)
    assert setup.snapshots == []
    # "Compare with…" on the device chosen second keeps it as the first only.
    setup.panel.set_primary(2)
    assert setup.chosen() == (2, None)


def test_each_list_leaves_out_the_device_chosen_in_the_other(
    setup: _Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed: dict[str, list[int]] = {}

    def combo(
        combo_id: str, current: int | None, devices: list[Any], _by_id: Any
    ) -> Any:
        listed[combo_id] = [d.node_id for d in devices]
        return current

    monkeypatch.setattr(setup.panel, "_device_combo", combo)
    setup.choose(1, 3)
    setup.frame()
    assert listed == {"##cmp_a": [1, 2], "##cmp_b": [2, 3]}
