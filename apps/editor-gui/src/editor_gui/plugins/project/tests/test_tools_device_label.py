"""The Tools device lists show a device the user named by its name only; an unnamed one also
shows its product."""

from __future__ import annotations

from editor_gui.device import Device
from editor_gui.plugins.project.ui.tools import ToolsPanel


def _label(device: Device, description: str) -> str:
    panel = object.__new__(ToolsPanel)
    panel._describe = lambda _node_id: description  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    return panel._device_label(device)  # pyright: ignore[reportPrivateUsage]


def test_a_named_device_shows_only_its_name() -> None:
    device = Device(1, "Kitchen Light", None, "1.1.1", product_name="Switch Actuator")
    assert _label(device, "Switch Actuator") == "1.1.1  Kitchen Light"


def test_an_unnamed_device_shows_its_product_once() -> None:
    device = Device(1, "", None, "1.1.1", product_name="Switch Actuator")
    assert _label(device, "Switch Actuator") == "1.1.1  Switch Actuator"


def test_an_unnamed_device_without_product_shows_its_order_number() -> None:
    device = Device(1, "", None, "1.1.1")
    assert _label(device, "ORD-1") == "1.1.1  ?  ·  ORD-1"
