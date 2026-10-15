"""Programming takes a device's tool key from the project first, else from the loaded keyring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.service import ConnectionService
from xknxeditor.download.data_secure import DeviceSecurity
from xknxeditor.proj.core.service import DeviceKeys

_LOADED = bytes(range(16))
_PLANNED = bytes(range(16, 32))
_KEYRING = bytes(range(32, 48))


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.warnings.append(event)

    def info(self, event: str, **kwargs: object) -> None: ...
    def debug(self, event: str, **kwargs: object) -> None: ...
    def error(self, event: str, **kwargs: object) -> None: ...


class _Keyring:
    def is_loaded(self) -> bool:
        return True

    def device_security(self, individual_address: str) -> DeviceSecurity | None:
        return DeviceSecurity(IndividualAddress(individual_address), _KEYRING)


def _service(keys: DeviceKeys | None) -> tuple[ConnectionService, _Log]:
    service = ConnectionService()
    log = _Log()
    service.set_logger(cast(Any, log))
    service.keyring = cast(Any, _Keyring())
    service.project_keys = lambda device: keys
    return service, log


_DEVICE = cast(Any, SimpleNamespace(name="Dev", individual_address="1.1.5", node_id=1))


def test_the_loaded_project_key_wins_over_the_keyring() -> None:
    service, _ = _service(DeviceKeys(tool_key=_PLANNED, loaded_tool_key=_LOADED))
    security = service.security_for(_DEVICE)
    assert security is not None
    assert (security.tool_key, str(security.address)) == (_LOADED, "1.1.5")
    # A device found at another address holds the same key.
    current = service._current_address_security(_DEVICE, "1.1.250", security)  # pyright: ignore[reportPrivateUsage]
    assert current is not None
    assert (current.tool_key, str(current.address)) == (_LOADED, "1.1.250")


def test_a_never_loaded_tool_key_programs_in_the_clear() -> None:
    service, log = _service(DeviceKeys(tool_key=_PLANNED, loaded_tool_key=None))
    assert service.security_for(_DEVICE) is None
    assert len(log.warnings) == 1


@pytest.mark.parametrize("project", [True, False])
def test_without_project_keys_the_keyring_is_used(project: bool) -> None:
    service, _ = _service(None)
    if not project:
        service.project_keys = None
    security = service.security_for(_DEVICE)
    assert security is not None and security.tool_key == _KEYRING
