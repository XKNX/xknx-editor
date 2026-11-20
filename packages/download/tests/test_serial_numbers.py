"""Serial numbers that cannot tell a device apart."""

from __future__ import annotations

import pytest

from xknxeditor.download.serial_numbers import suspicious_serial_number


def test_suspicious_serial_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not suspicious_serial_number(bytes.fromhex("00FA12345678"))
    assert suspicious_serial_number(bytes(6))
    assert suspicious_serial_number(bytes.fromhex("00FAFFFFFFFF"))
    # Not six octets.
    assert suspicious_serial_number(bytes.fromhex("00FA1234"))
    # A text manufacturer part, or a placeholder one.
    assert suspicious_serial_number(b"11" + bytes.fromhex("12345678"))
    assert suspicious_serial_number(bytes.fromhex("0300 00123456"))
    monkeypatch.setenv("XKNX_NO_SERIAL_NUMBER_CHECK", "1")
    assert not suspicious_serial_number(bytes(6))


@pytest.mark.parametrize(
    ("serial", "suspicious"),
    [
        # Serial numbers devices of their manufacturer are known to share: by range, by
        # value, from a list and by a mask.
        ("000201362FC6", True),
        ("000228052017", True),
        ("000A257D2B11", True),
        ("007600FFFFFF", True),
        # Real ones of the same manufacturers.
        ("000200000009", False),
        ("000774148BF8", False),
        ("000A00000001", False),
    ],
)
def test_serial_numbers_devices_share(serial: str, suspicious: bool) -> None:
    assert suspicious_serial_number(bytes.fromhex(serial)) is suspicious
