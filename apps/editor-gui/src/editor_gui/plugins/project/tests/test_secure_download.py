"""What a download of a KNX Data Secure device does: commission it securely with its Tool Key,
switch it to plain communication, or program it in the clear."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.project.service import ProjectService
from xknxeditor.proj.core.service import DeviceKeys

_TOOL_KEY = bytes(range(16))
_LOADED = bytes(range(16, 32))


def _service(keys: DeviceKeys | None) -> Any:
    service = cast(Any, SimpleNamespace())
    service._pid = "P"
    service.device_keys = lambda device: keys
    return service


def _device(secure_app: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(program=SimpleNamespace(is_secure_enabled=secure_app))
    )


def _decide(
    keys: DeviceKeys | None, secure_app: bool = True
) -> tuple[bytes | None, bool]:
    service, device = _service(keys), _device(secure_app)
    return (
        ProjectService.download_tool_key(service, device),
        ProjectService.download_disables_security(service, device),
    )


def test_secure_commissioning_activated() -> None:
    assert _decide(DeviceKeys(tool_key=_TOOL_KEY, loaded_tool_key=None)) == (
        _TOOL_KEY,
        False,
    )
    assert _decide(DeviceKeys(tool_key=_TOOL_KEY, loaded_tool_key=_TOOL_KEY)) == (
        _TOOL_KEY,
        False,
    )


def test_deactivated_on_a_device_that_holds_a_tool_key() -> None:
    # Reached with the key it holds once more, to switch it to plain communication.
    assert _decide(DeviceKeys(tool_key=None, loaded_tool_key=_LOADED)) == (None, True)


def test_programmed_in_the_clear() -> None:
    assert _decide(None) == (None, False)
    assert _decide(DeviceKeys(tool_key=None, loaded_tool_key=None, fdsk=_LOADED)) == (
        None,
        False,
    )
    # An application without KNX Data Secure never gets security.
    assert _decide(
        DeviceKeys(tool_key=_TOOL_KEY, loaded_tool_key=_LOADED), secure_app=False
    ) == (None, False)


def test_a_sender_needs_communication_and_a_sending_flag() -> None:
    def flags(**values: bool) -> SimpleNamespace:
        base = {
            "communication": True,
            "transmit": False,
            "read": False,
            "read_on_init": False,
        }
        return SimpleNamespace(**(base | values))

    objects = {
        1: flags(transmit=True),
        2: flags(read=True),
        3: flags(read_on_init=True),
        4: flags(),  # receives only
        5: flags(communication=False, transmit=True),
    }
    service = cast(Any, SimpleNamespace())
    service.devices = [
        SimpleNamespace(
            com_objects=[
                SimpleNamespace(db_id=db_id, flags=value)
                for db_id, value in objects.items()
            ]
        )
    ]
    can_send = {
        db_id: ProjectService._com_object_can_send(service, db_id)  # pyright: ignore[reportPrivateUsage]
        for db_id in (*objects, 99)
    }
    assert can_send == {1: True, 2: True, 3: True, 4: False, 5: False, 99: True}
