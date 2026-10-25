"""Tests for recover orchestration (transport retry)."""

from __future__ import annotations

import pytest
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress

from xknxeditor.recover.recover import recover_device_at


class _FlakyManagement:
    """A management stub whose connect always fails with a transport error."""

    def __init__(self) -> None:
        self.connect_calls = 0

    async def connect(self, address: IndividualAddress) -> object:
        self.connect_calls += 1
        raise ManagementConnectionError("tunnel dropped")

    async def disconnect(self, address: IndividualAddress) -> None:
        pass


class _FlakyXknx:
    def __init__(self) -> None:
        self.management = _FlakyManagement()


async def test_recover_device_at_retries_then_raises() -> None:
    xknx = _FlakyXknx()
    with pytest.raises(ManagementConnectionError):
        await recover_device_at(
            xknx,  # type: ignore[arg-type]
            "1.1.9",
            object(),  # type: ignore[arg-type]  # never reached: connect fails first
            attempts=3,
            retry_delay=0.0,
        )
    # Reconnected on every attempt before giving up.
    assert xknx.management.connect_calls == 3


class _Programmer:
    """A DeviceProgrammer stand-in reporting ``device_max`` as its maximum APDU."""

    device_max = 55

    def __init__(self, connection: object) -> None:
        self.max_apdu_length = 15

    async def read_max_apdu_length(self) -> int:
        return self.device_max


class _Management:
    def __init__(self) -> None:
        self.connect_calls = 0

    async def connect(self, address: IndividualAddress) -> object:
        self.connect_calls += 1
        return object()

    async def disconnect(self, address: IndividualAddress) -> None:
        pass


class _Xknx:
    def __init__(self) -> None:
        self.management = _Management()


def _patch(monkeypatch: pytest.MonkeyPatch, recover: object) -> None:
    from xknxeditor.recover import recover as module

    async def connect(xknx: _Xknx, target: IndividualAddress) -> object:
        return await xknx.management.connect(target)

    monkeypatch.setattr(module, "connect", connect)
    monkeypatch.setattr(module, "DeviceProgrammer", _Programmer)
    monkeypatch.setattr(module, "recover_device", recover)


async def test_recover_device_at_falls_back_to_default_apdu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The device reports 55 octets but drops the connection on every long read, as
    # behind a tunnelling v1 interface that only passes standard frames.
    lengths: list[int] = []

    async def recover(programmer: _Programmer, application: object, **_: object) -> str:
        lengths.append(programmer.max_apdu_length)
        if programmer.max_apdu_length > 15:
            raise ManagementConnectionError("disconnected by the peer")
        return "recovered"

    _patch(monkeypatch, recover)
    xknx = _Xknx()
    result = await recover_device_at(
        xknx,  # type: ignore[arg-type]
        "1.1.21",
        object(),  # type: ignore[arg-type]
        attempts=3,
        retry_delay=0.0,
    )
    assert result == "recovered"
    assert lengths == [55, 15]
    assert xknx.management.connect_calls == 2


async def test_recover_device_at_caps_apdu_at_interface_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lengths: list[int] = []

    async def recover(programmer: _Programmer, application: object, **_: object) -> str:
        lengths.append(programmer.max_apdu_length)
        return "recovered"

    _patch(monkeypatch, recover)
    await recover_device_at(
        _Xknx(),  # type: ignore[arg-type]
        "1.1.21",
        object(),  # type: ignore[arg-type]
        interface_max_apdu_length=40,
    )
    assert lengths == [40]
