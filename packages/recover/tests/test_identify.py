"""Tests for device application identification and catalog matching."""

from __future__ import annotations

from dataclasses import dataclass

from _fakes import FakeConnection
from xknx.exceptions import XKNXException
from xknx.telegram.apci import MemoryRead, PropertyValueRead

from xknxeditor.download import DeviceProgrammer
from xknxeditor.recover.identify import (
    BCU1_APPLICATION_ID_ADDRESS,
    OBJECT_TYPE_APPLICATION_PROGRAM,
    PID_PROGRAM_VERSION,
    AppId,
    is_bcu1_mask,
    match_application,
    parse_application_id,
    parse_bcu1_application_id,
    read_application_id,
)


def test_parse_application_id() -> None:
    # M-0083, application number 0x008A, version 0x25.
    data = bytes([0x00, 0x83, 0x00, 0x8A, 0x25])
    assert parse_application_id(data) == AppId(
        manufacturer_id="M-0083", application_number=0x008A, application_version=0x25
    )


def test_parse_application_id_short_returns_none() -> None:
    assert parse_application_id(b"\x00\x83\x00") is None


async def test_read_application_id_from_device() -> None:
    connection = FakeConnection(
        object_types={0: 0, 1: 1, 2: 2, 3: OBJECT_TYPE_APPLICATION_PROGRAM},
        properties={(3, PID_PROGRAM_VERSION): bytes([0x00, 0x02, 0xA0, 0x62, 0x14])},
    )
    programmer = DeviceProgrammer(connection)
    app_id = await read_application_id(programmer)
    assert app_id == AppId(
        manufacturer_id="M-0002", application_number=0xA062, application_version=0x14
    )


async def test_read_application_id_unprogrammed_returns_none() -> None:
    # No Application Program object present -> locate_object fails -> None.
    connection = FakeConnection(object_types={0: 0})
    programmer = DeviceProgrammer(connection)
    assert await read_application_id(programmer) is None


class _FlakyProgrammer:
    """Fails the object lookup ``fail_times`` times, then reads a valid app id."""

    def __init__(self, fail_times: int) -> None:
        self._remaining = fail_times
        self.locate_calls = 0

    async def locate_object(self, object_type: int, occurrence: int = 0) -> int:
        self.locate_calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise XKNXException("lost telegram")
        return 3

    async def read_property(
        self, object_index: int, property_id: int, **_: object
    ) -> bytes:
        return bytes([0x00, 0x83, 0x00, 0x8A, 0x25])


async def test_read_application_id_retries_transient_failure() -> None:
    programmer = _FlakyProgrammer(fail_times=2)
    app_id = await read_application_id(
        programmer,  # type: ignore[arg-type]
        attempts=3,
        retry_delay=0.0,
    )
    assert app_id == AppId("M-0083", 0x008A, 0x25)
    assert programmer.locate_calls == 3


async def test_read_application_id_gives_up_after_attempts() -> None:
    programmer = _FlakyProgrammer(fail_times=5)
    result = await read_application_id(
        programmer,  # type: ignore[arg-type]
        attempts=3,
        retry_delay=0.0,
    )
    assert result is None
    assert programmer.locate_calls == 3


@dataclass
class _Product:
    product_ref_id: str
    application_version: int | None


class _FakeCatalog:
    def __init__(self, exact: list[_Product], loose: list[_Product]) -> None:
        self._exact = exact
        self._loose = loose
        self.calls: list[int | None] = []

    def find_products_for_application(
        self,
        *,
        manufacturer_id: str,
        application_number: int,
        application_version: int | None = None,
    ) -> list[_Product]:
        self.calls.append(application_version)
        return self._exact if application_version is not None else self._loose


def test_match_application_prefers_exact_version() -> None:
    catalog = _FakeCatalog(exact=[_Product("P-1", 0x25)], loose=[_Product("P-2", None)])
    app_id = AppId("M-0083", 0x008A, 0x25)
    result = match_application(catalog, app_id)  # type: ignore[arg-type]
    assert [p.product_ref_id for p in result] == ["P-1"]
    assert catalog.calls == [0x25]


def test_match_application_falls_back_to_number_only() -> None:
    catalog = _FakeCatalog(exact=[], loose=[_Product("P-2", None)])
    app_id = AppId("M-0083", 0x008A, 0x99)
    result = match_application(catalog, app_id)  # type: ignore[arg-type]
    assert [p.product_ref_id for p in result] == ["P-2"]
    assert catalog.calls == [0x99, None]


def test_is_bcu1_mask() -> None:
    assert is_bcu1_mask(0x0010)
    assert is_bcu1_mask(0x0013)
    assert not is_bcu1_mask(0x0021)
    assert not is_bcu1_mask(0x0701)
    assert not is_bcu1_mask(None)


def test_parse_bcu1_application_id() -> None:
    # Manufacturer 02h, device type A01Eh, version 10h.
    assert parse_bcu1_application_id(bytes([0x02, 0xA0, 0x1E, 0x10])) == AppId(
        manufacturer_id="M-0002", application_number=0xA01E, application_version=0x10
    )


def test_parse_bcu1_application_id_without_application_returns_none() -> None:
    assert parse_bcu1_application_id(b"\xff\xff\xff\xff") is None
    assert parse_bcu1_application_id(b"\x00\x00\x00\x00") is None
    assert parse_bcu1_application_id(b"\x02\xa0") is None


async def test_read_application_id_of_bcu1_reads_memory() -> None:
    # A BCU 1 leaves a property read unanswered; its id is in the EEPROM header.
    connection = FakeConnection(
        descriptor=0x0013,
        property_services=False,
        memory={BCU1_APPLICATION_ID_ADDRESS: bytes([0x02, 0xA0, 0x1E, 0x10])},
    )
    programmer = DeviceProgrammer(connection)
    app_id = await read_application_id(programmer, mask_version=0x0013)
    assert app_id == AppId("M-0002", 0xA01E, 0x10)
    assert all(isinstance(request, MemoryRead) for request in connection.requests)


async def test_read_application_id_of_bcu1_without_mask_finds_nothing() -> None:
    # Without the mask version the property path is taken, which a BCU 1 does not answer.
    connection = FakeConnection(
        descriptor=0x0013,
        property_services=False,
        memory={BCU1_APPLICATION_ID_ADDRESS: bytes([0x02, 0xA0, 0x1E, 0x10])},
    )
    programmer = DeviceProgrammer(connection)
    assert await read_application_id(programmer, retry_delay=0.0) is None
    assert all(
        isinstance(request, PropertyValueRead) for request in connection.requests
    )


async def test_read_application_id_of_erased_bcu1_returns_none() -> None:
    connection = FakeConnection(
        descriptor=0x0012,
        property_services=False,
        memory={BCU1_APPLICATION_ID_ADDRESS: b"\xff\xff\xff\xff"},
    )
    programmer = DeviceProgrammer(connection)
    assert await read_application_id(programmer, mask_version=0x0012) is None


async def test_read_application_id_of_bcu1_retries_unanswered_read() -> None:
    connection = FakeConnection(descriptor=0x0013, property_services=False)
    programmer = DeviceProgrammer(connection)
    app_id = await read_application_id(
        programmer, mask_version=0x0013, attempts=2, retry_delay=0.0
    )
    assert app_id is None
    assert len(connection.requests) >= 2
