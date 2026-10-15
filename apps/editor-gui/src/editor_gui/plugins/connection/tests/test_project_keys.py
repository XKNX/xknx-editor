"""Programming takes a device's tool key from the project first, else from the loaded keyring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.telegram import IndividualAddress

from editor_gui.plugins.connection.service import (
    ConnectionService,
    DeviceCertificateRequired,
)
from xknxeditor.download.data_secure import DeviceSecurity
from xknxeditor.proj.core.service import DeviceKeys

_LOADED = bytes(range(16))
_PLANNED = bytes(range(16, 32))
_KEYRING = bytes(range(32, 48))
_FDSK = bytes(range(48, 64))


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
    # Secure commissioning gives the device its Tool Key (see _download_security); any other
    # access reaches a device that holds none in the clear.
    service, log = _service(DeviceKeys(tool_key=_PLANNED, loaded_tool_key=None))
    assert service.security_for(_DEVICE) is None
    assert log.warnings == []


@pytest.mark.parametrize("project", [True, False])
def test_without_project_keys_the_keyring_is_used(project: bool) -> None:
    service, _ = _service(None)
    if not project:
        service.project_keys = None
    security = service.security_for(_DEVICE)
    assert security is not None and security.tool_key == _KEYRING


def test_commissioning_starts_with_the_factory_key() -> None:
    service, _ = _service(
        DeviceKeys(tool_key=_PLANNED, loaded_tool_key=None, fdsk=_FDSK)
    )
    security = service._download_security(_DEVICE, _PLANNED)  # pyright: ignore[reportPrivateUsage]
    assert security is not None
    assert security.keys == (_FDSK, _PLANNED, _KEYRING)


def test_a_commissioned_device_starts_with_its_tool_key() -> None:
    service, _ = _service(
        DeviceKeys(tool_key=_PLANNED, loaded_tool_key=_LOADED, fdsk=_FDSK)
    )
    security = service._download_security(_DEVICE, _PLANNED)  # pyright: ignore[reportPrivateUsage]
    assert security is not None
    assert security.keys == (_LOADED, _PLANNED, _FDSK, _KEYRING)


def test_commissioning_uses_a_keyring_key_the_project_lacks() -> None:
    service, _ = _service(DeviceKeys(tool_key=_PLANNED, loaded_tool_key=None))
    security = service._download_security(_DEVICE, _PLANNED)  # pyright: ignore[reportPrivateUsage]
    assert security is not None
    assert security.keys == (_KEYRING, _PLANNED)


def test_commissioning_needs_the_device_certificate() -> None:
    service, _ = _service(DeviceKeys(tool_key=_PLANNED, loaded_tool_key=None))
    service.keyring = None
    with pytest.raises(DeviceCertificateRequired):
        service._download_security(_DEVICE, _PLANNED)  # pyright: ignore[reportPrivateUsage]


def test_a_device_without_a_tool_key_is_reached_in_the_clear() -> None:
    # Its factory key is only used to give it its Tool Key: a device commissioned in the clear
    # (or not yet) is reached without security.
    service, log = _service(
        DeviceKeys(tool_key=_PLANNED, loaded_tool_key=None, fdsk=_FDSK)
    )
    assert service.security_for(_DEVICE) is None
    assert log.warnings == []


def test_the_factory_key_is_the_alternative_after_a_master_reset() -> None:
    service, _ = _service(DeviceKeys(tool_key=None, loaded_tool_key=_LOADED, fdsk=_FDSK))
    security = service.security_for(_DEVICE)
    assert security is not None and security.tool_key == _LOADED
    assert security.alternative_keys == (_FDSK,)
