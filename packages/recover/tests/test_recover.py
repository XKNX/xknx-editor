"""Tests for recover orchestration (transport retry, APDU cap and fallback)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress

import xknxeditor.recover.recover as recover_mod
from xknxeditor.download import DeviceProgrammer
from xknxeditor.download.errors import MemoryReadNotAnswered
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


class _OkManagement:
    """Connect/disconnect that always succeed, handing back a dummy connection."""

    async def connect(self, address: IndividualAddress) -> object:
        return object()

    async def disconnect(self, address: IndividualAddress) -> None:
        pass


class _OkXknx:
    def __init__(self) -> None:
        self.management = _OkManagement()


@pytest.fixture
def _device_reports_55(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make the probed device advertise a 55-octet APDU and record the length each
    recover attempt actually uses, failing the first attempt so the fallback runs."""

    async def fake_read_max_apdu_length(self: DeviceProgrammer) -> int:
        return 55

    monkeypatch.setattr(
        DeviceProgrammer, "read_max_apdu_length", fake_read_max_apdu_length
    )
    used: list[int] = []

    async def fake_recover_device(  # type: ignore[no-untyped-def]
        programmer,
        application,
        *,
        address,
        progress=None,
        project_code_page=None,
        reads_done=None,
        effort=None,
        cancel=None,
    ):
        used.append(programmer.max_apdu_length)
        if len(used) == 1:
            # The real 731 failure: a long read the interface dropped surfaces as
            # MemoryReadNotAnswered (a DownloadError, not an XKNXException).
            raise MemoryReadNotAnswered("no response to a long read")
        return object()

    monkeypatch.setattr(recover_mod, "recover_device", fake_recover_device)
    return used


async def test_first_attempt_caps_at_interface_then_falls_back_to_15(
    _device_reports_55: list[int],
) -> None:
    # No interface limit: the first attempt uses the device's 55, and because it fails the
    # retry drops to the mandatory 15 octets.
    await recover_device_at(
        _OkXknx(),  # type: ignore[arg-type]
        "1.1.9",
        object(),  # type: ignore[arg-type]
        interface_max_apdu_length=None,
        attempts=3,
        retry_delay=0.0,
    )
    assert _device_reports_55 == [55, 15]


async def test_interface_limit_caps_the_first_attempt(
    _device_reports_55: list[int],
) -> None:
    # The interface only carries 15 octets, so even a device advertising 55 is used at 15
    # from the first attempt (and the fallback keeps it there).
    await recover_device_at(
        _OkXknx(),  # type: ignore[arg-type]
        "1.1.9",
        object(),  # type: ignore[arg-type]
        interface_max_apdu_length=15,
        attempts=3,
        retry_delay=0.0,
    )
    assert _device_reports_55 == [15, 15]


async def test_the_connection_is_closed_before_the_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Decoding a large application takes seconds; the device would close the idle
    # connection meanwhile, and closing it afterwards failed (issue #207).
    events: list[str] = []

    class Management:
        async def connect(self, address: IndividualAddress) -> object:
            events.append("connect")
            return object()

        async def disconnect(self, address: IndividualAddress) -> None:
            events.append("disconnect")

    class Xknx:
        management = Management()

    async def read(*_args: Any) -> Any:
        events.append("read")
        return SimpleNamespace(
            device_address=None, group_addresses=[], links=[], group_objects={}
        )

    async def read_properties(*_args: Any) -> dict[Any, bytes]:
        events.append("read")
        return {}

    async def descriptor(_self: DeviceProgrammer) -> int:
        return 0x07B0

    def decode(*_args: Any) -> tuple[Any, dict[int, str]]:
        events.append("decode")
        return SimpleNamespace(values={}, unknown=[]), {}

    monkeypatch.setattr(DeviceProgrammer, "read_device_descriptor", descriptor)
    for name in (
        "read_dossier",
        "read_group_communication",
        "read_parameter_memory",
    ):
        monkeypatch.setattr(recover_mod, name, read)
    monkeypatch.setattr(recover_mod, "read_property_values", read_properties)
    monkeypatch.setattr(recover_mod, "_decode_configuration", decode)
    await recover_device_at(
        Xknx(),  # type: ignore[arg-type]
        "1.1.41",
        SimpleNamespace(id="M-0083_A-008A-28-7B00"),  # type: ignore[arg-type]
        negotiate_apdu=False,
    )
    assert events == ["connect", "read", "read", "read", "read", "disconnect", "decode"]
