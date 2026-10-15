"""A group address in Auto Security mode is secured when every linked device is commissioned
securely."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.project.service import ProjectService
from xknxeditor.proj.core.service import DeviceKeys

_KEY = bytes(range(16))


def _device(db_id: int, *, secure_app: bool, tool_key: bool) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(program=SimpleNamespace(is_secure_enabled=secure_app)),
        com_objects=[SimpleNamespace(db_id=db_id)],
        tool_key=tool_key,
    )


def _auto_secure(*devices: SimpleNamespace) -> bool:
    """Run the service's Auto rule on a stand-in holding only what it uses."""
    service = cast(Any, SimpleNamespace())
    service.devices = list(devices)
    service.get_assignments_for_ga = lambda ga_id: [
        SimpleNamespace(com_object_id=d.com_objects[0].db_id) for d in devices
    ]
    service.device_keys = lambda device: (
        DeviceKeys(tool_key=_KEY, loaded_tool_key=None) if device.tool_key else None
    )
    return ProjectService._ga_auto_secure(service, 1)  # pyright: ignore[reportPrivateUsage]


def test_all_devices_commissioned_securely() -> None:
    assert _auto_secure(
        _device(1, secure_app=True, tool_key=True),
        _device(2, secure_app=True, tool_key=True),
    )


def test_one_device_in_the_clear_keeps_it_plain() -> None:
    assert not _auto_secure(
        _device(1, secure_app=True, tool_key=True),
        _device(2, secure_app=True, tool_key=False),
    )
    assert not _auto_secure(
        _device(1, secure_app=True, tool_key=True),
        _device(2, secure_app=False, tool_key=False),
    )


def test_an_unlinked_group_address_is_plain() -> None:
    assert not _auto_secure()
