"""Tests for the Tool-Key secured connection manager wiring.

Drives :class:`SecureConnectionManager` with a fake ``xknx`` whose connection
emulates the device's S-A_Sync answer through the installed CEMI securer, so
the install/restore of the hook and the sync handshake are covered without a
real bus.
"""

from __future__ import annotations

import pytest
from xknx.telegram import Telegram
from xknx.telegram.address import IndividualAddress
from xknx.telegram.apci import APCI

from xknxeditor.download import secure_session
from xknxeditor.download.data_secure import (
    DeviceSecurity,
    SecureProgrammingError,
    ToolKeyCemiSecure,
)
from xknxeditor.download.download import _connection_manager
from xknxeditor.download.secure_session import SecureConnectionManager

from .test_data_secure import DEVICE, TOOL, TOOL_KEY, _cemi, _device_sync_response


class _FakeCemiHandler:
    def __init__(self) -> None:
        self.data_secure: object = None


class _FakeConnection:
    """Emulates one connection-oriented request/response through the securer."""

    def __init__(self, cemi_handler: _FakeCemiHandler) -> None:
        self._cemi_handler = cemi_handler
        self.disconnected = False

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        hook = self._cemi_handler.data_secure
        assert hook is not None
        secured = hook.outgoing_cemi(  # type: ignore[attr-defined]
            _cemi(TOOL, DEVICE, sequence_number=0, payload=payload)
        )
        response = _device_sync_response(secured, sending=0x100, tool_expected=0x200)
        hook.received_cemi(response)  # type: ignore[attr-defined]
        return response.telegram()

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        raise AssertionError("not used in this test")


class _FakeManagement:
    def __init__(self, cemi_handler: _FakeCemiHandler) -> None:
        self._cemi_handler = cemi_handler
        self.connected: list[IndividualAddress] = []
        self.disconnected: list[IndividualAddress] = []

    async def connect(self, address: IndividualAddress) -> _FakeConnection:
        self.connected.append(address)
        return _FakeConnection(self._cemi_handler)

    async def disconnect(self, address: IndividualAddress) -> None:
        self.disconnected.append(address)


class _FakeXKNX:
    def __init__(self) -> None:
        self.cemi_handler = _FakeCemiHandler()
        self.current_address = TOOL
        self.management = _FakeManagement(self.cemi_handler)


async def test_open_installs_hook_syncs_and_close_restores() -> None:
    xknx = _FakeXKNX()
    sentinel = object()
    xknx.cemi_handler.data_secure = sentinel  # a pre-existing (e.g. group) securer

    manager = SecureConnectionManager(xknx, DEVICE, DeviceSecurity(DEVICE, TOOL_KEY))  # type: ignore[arg-type]

    await manager.open()
    # The securer is installed and the session synchronized during open.
    assert isinstance(xknx.cemi_handler.data_secure, ToolKeyCemiSecure)
    assert xknx.management.connected == [DEVICE]

    await manager.close()
    # The previous securer is restored and the connection is disconnected.
    assert xknx.cemi_handler.data_secure is sentinel
    assert xknx.management.disconnected == [DEVICE]


async def test_open_restores_hook_when_sync_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secure_session, "_SYNC_RETRY_DELAY", 0.0)
    xknx = _FakeXKNX()
    sentinel = object()
    xknx.cemi_handler.data_secure = sentinel

    # A connection that never delivers a valid sync response.
    class _SilentConnection(_FakeConnection):
        async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
            return Telegram(source_address=DEVICE, destination_address=TOOL)

    async def connect(address: IndividualAddress) -> _SilentConnection:
        return _SilentConnection(xknx.cemi_handler)

    xknx.management.connect = connect  # type: ignore[assignment]

    manager = SecureConnectionManager(xknx, DEVICE, DeviceSecurity(DEVICE, TOOL_KEY))  # type: ignore[arg-type]

    with pytest.raises(SecureProgrammingError):
        await manager.open()
    # Even on failure the previous securer is restored.
    assert xknx.cemi_handler.data_secure is sentinel


def test_connection_manager_rejects_mismatched_security_address() -> None:
    xknx = _FakeXKNX()
    security = DeviceSecurity(IndividualAddress("1.2.3"), TOOL_KEY)
    with pytest.raises(SecureProgrammingError, match="not the download target"):
        _connection_manager(xknx, DEVICE, security)  # type: ignore[arg-type]


async def test_a_new_session_waits_for_the_sync_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A device ignores an S-A_Sync request within 1 s of its last S-A_Sync response, also from
    # a new connection (a download opens several short sessions in a row).
    import time

    monkeypatch.setattr(secure_session, "_SYNC_INTERVAL", 0.3)
    xknx = _FakeXKNX()
    security = DeviceSecurity(DEVICE, TOOL_KEY)
    first = SecureConnectionManager(xknx, DEVICE, security)  # type: ignore[arg-type]
    await first.open()
    await first.close()
    started = time.monotonic()
    second = SecureConnectionManager(xknx, DEVICE, security)  # type: ignore[arg-type]
    await second.open()
    await second.close()
    assert time.monotonic() - started >= 0.25
