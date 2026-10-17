"""The device boxes of the topology map carry each device's description for their icon."""

from __future__ import annotations

from types import SimpleNamespace

from editor_gui.device import Device
from editor_gui.plugins.topology.ui import TopologyPanel


def _device(node_id: int, address: str, description: str = "") -> Device:
    return Device(node_id, f"Device {node_id}", None, address, description=description)


def _panel(devices: list[Device]) -> TopologyPanel:
    return TopologyPanel(lambda: devices, lambda: [], lambda _id: None, lambda: True)


def test_physical_boxes_carry_the_description() -> None:
    devices = [_device(1, "1.1.1", "  Under the stairs  "), _device(2, "1.1.2")]
    layout = _panel(devices)._build_physical_layout(devices)  # pyright: ignore[reportPrivateUsage]
    assert [(d.node_id, d.description) for d in layout.devices] == [
        (1, "Under the stairs"),
        (2, ""),
    ]


def test_building_boxes_carry_the_description() -> None:
    devices = [_device(1, "1.1.1", "In the cabinet")]
    space = SimpleNamespace(
        name="Building",
        space_type="Building",
        devices=[
            SimpleNamespace(id=1, name="", individual_address="1.1.1", description=""),
            SimpleNamespace(
                id=9, name="Other", individual_address="", description="Not resolved"
            ),
        ],
        children=[],
    )
    layout = _panel(devices)._build_building_layout([space])  # pyright: ignore[reportPrivateUsage]
    assert [d.description for d in layout.devices] == ["In the cabinet", "Not resolved"]
