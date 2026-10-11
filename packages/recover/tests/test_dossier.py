"""Tests for Device Object dossier decoding."""

from __future__ import annotations

import pytest
from xknx.exceptions import ManagementConnectionError

from xknxeditor.recover.dossier import _read, printable_text


def test_ascii_extracts_order_number_stem_from_wrapped_bytes() -> None:
    # Hager AKH-0800: 0x22 '"' prefix, "0800", FFFF, structural tail. The longest
    # printable run (which includes the leading quote) carries the order number.
    assert printable_text(bytes.fromhex("2230383030FFFF020F31")) == '"0800'


def test_ascii_returns_whole_printable_value() -> None:
    assert printable_text(b"ITR524-16A\x00\x00") == "ITR524-16A"


def test_ascii_falls_back_to_hex_without_printable_run() -> None:
    assert printable_text(bytes([0x01, 0xFF, 0x02])) == "01FF02"


def test_ascii_empty_is_none() -> None:
    assert printable_text(b"\x00\x00") is None


class _FakeProgrammer:
    """Records read_property calls and replays a scripted sequence of results/exceptions."""

    def __init__(self, results: list[bytes | Exception]) -> None:
        self._results = results
        self.calls = 0

    async def read_property(self, object_index: int, property_id: int) -> bytes:
        result = self._results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_read_retries_transient_failure() -> None:
    # A telegram lost on the tunnel raises; the retry gets the real value instead of blanking it.
    programmer = _FakeProgrammer([ManagementConnectionError("lost"), b"\x01\x02"])
    result = await _read(programmer, 11, retry_delay=0.0)  # type: ignore[arg-type]
    assert result == b"\x01\x02"
    assert programmer.calls == 2


@pytest.mark.asyncio
async def test_read_does_not_retry_empty_success() -> None:
    # An absent property answers empty without erroring; that is final, no retry.
    programmer = _FakeProgrammer([b"", b"\xff"])
    result = await _read(programmer, 11, retry_delay=0.0)  # type: ignore[arg-type]
    assert result == b""
    assert programmer.calls == 1


@pytest.mark.asyncio
async def test_read_gives_up_after_attempts() -> None:
    programmer = _FakeProgrammer([ManagementConnectionError("x")] * 3)
    result = await _read(programmer, 11, attempts=3, retry_delay=0.0)  # type: ignore[arg-type]
    assert result == b""
    assert programmer.calls == 3
