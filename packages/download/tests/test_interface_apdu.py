"""Frames stay within the connecting interface's maximum APDU length, and a long memory read
that gets no response is repeated with standard-size blocks (issue #95: a device reporting a
254-octet APDU behind an interface that drops long frames)."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.apci import MemoryRead, MemoryResponse

from xknxeditor.download.download import (
    _apdu_settings,  # pyright: ignore[reportPrivateUsage]
    preflight,
)
from xknxeditor.download.errors import (
    MemoryReadNotAnswered,
    MemoryWriteNotAcknowledged,
    PartialDownloadError,
    VerificationError,
)
from xknxeditor.download.programmer import DeviceProgrammer, negotiated_apdu_length
from xknxeditor.download.scope import DownloadScope

download_module = importlib.import_module("xknxeditor.download.download")


def test_negotiated_apdu_length_caps_at_the_interface() -> None:
    # No interface limit: the device maximum, capped at the communication ceiling.
    assert negotiated_apdu_length(55) == 55
    assert negotiated_apdu_length(254) == 239
    # The interface limit wins when it is the smaller.
    assert negotiated_apdu_length(254, 55) == 55
    assert negotiated_apdu_length(55, 15) == 15
    # Never below the mandatory standard frame, whatever the device or interface reports.
    assert negotiated_apdu_length(8) == 15
    assert negotiated_apdu_length(254, 10) == 15


def test_negotiation_stays_within_the_interface() -> None:
    assert _apdu_settings(None) == (239, True)
    assert _apdu_settings(None, 55) == (55, True)
    assert _apdu_settings(None, 254) == (239, True)
    # An interface reporting less than the standard frame still gets the standard length.
    assert _apdu_settings(None, 10) == (15, True)
    # A fixed length is used as given.
    assert _apdu_settings(60, 55) == (60, False)


class _DroppingConnection:
    """Answers A_Memory_Read up to ``longest`` octets; a longer response never arrives, as
    when the interface drops a frame longer than it accepts."""

    def __init__(self, longest: int) -> None:
        self.longest = longest
        self.counts: list[int] = []

    async def send_data(self, payload: object, wait_for_ack: bool = True) -> None:
        raise AssertionError("unexpected send_data")

    async def request(self, payload: object, expected: object) -> Telegram:
        assert isinstance(payload, MemoryRead)
        self.counts.append(payload.count)
        if payload.count > self.longest:
            raise ManagementConnectionError(
                "Timeout while waiting for <class 'xknx.telegram.apci.MemoryResponse'>"
            )
        return Telegram(
            destination_address=IndividualAddress("1.1.1"),
            payload=MemoryResponse(address=payload.address, data=bytes(payload.count)),
        )


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xknxeditor.download.programmer._TRANSIENT_RETRY_DELAY", 0.0)


async def test_a_long_read_without_response_is_reported_for_a_retry() -> None:
    connection = _DroppingConnection(longest=26)
    programmer = DeviceProgrammer(connection, max_apdu_length=239)
    with pytest.raises(MemoryReadNotAnswered, match="0x018c"):
        await programmer.read_memory(0x018C, 1990)
    assert connection.counts[0] == 63


async def test_a_short_read_without_response_stays_a_connection_error() -> None:
    connection = _DroppingConnection(longest=0)
    programmer = DeviceProgrammer(connection, max_apdu_length=239)
    with pytest.raises(ManagementConnectionError):
        await programmer.read_memory(0x0100, 8)


async def test_standard_blocks_are_answered() -> None:
    connection = _DroppingConnection(longest=26)
    programmer = DeviceProgrammer(connection, max_apdu_length=239)
    programmer.max_memory_service_count = 12
    data = await programmer.read_memory(0x018C, 1990)
    assert len(data) == 1990
    assert max(connection.counts) == 12


class _Runner:
    """Records how each preflight attempt is set up; the first attempt's long read gets no
    response."""

    attempts: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: object, **kwargs: Any) -> None:
        self.kwargs = kwargs
        _Runner.attempts.append(kwargs)

    async def preflight(self) -> str:
        if self.kwargs["memory_service_count"] is None:
            raise MemoryReadNotAnswered("no response to a 63-octet read")
        return "report"


class _Manager:
    closed = 0

    async def close(self) -> None:
        _Manager.closed += 1


async def test_preflight_retries_with_smaller_blocks_within_the_interface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Runner.attempts = []
    _Manager.closed = 0

    async def same_image(*args: object) -> object:
        return args[3]

    async def same_scope(*args: Any) -> object:
        return download_module._ScopeCheck(args[5])  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(download_module, "LoadProcedureRunner", _Runner)
    monkeypatch.setattr(download_module, "_connection_manager", lambda *a: _Manager())
    monkeypatch.setattr(download_module, "_device_association_format", same_image)
    monkeypatch.setattr(download_module, "_partial_scope", same_scope)
    monkeypatch.setattr(download_module, "_resolve_controls", lambda *a, **k: [])
    monkeypatch.setattr(download_module, "_object_types", lambda *a: {})
    monkeypatch.setattr(download_module, "_authorize_levels", lambda *a: 0)

    report = await preflight(
        cast(Any, None),
        "1.1.1",
        cast(Any, None),
        image=cast(Any, object()),
        interface_max_apdu_length=55,
    )

    assert report == "report"
    assert [a["memory_service_count"] for a in _Runner.attempts] == [None, 15]
    assert all(a["max_apdu_length"] == 55 for a in _Runner.attempts)
    assert _Manager.closed == 2


class _LinkDroppingRunner:
    """A download run that negotiates ``negotiated`` octets: with more than the standard
    frame the link drops (as knxd's TPUART link does on a long frame), with the standard
    frame it works."""

    attempts: ClassVar[list[int]] = []
    memory_caps: ClassVar[list[int | None]] = []
    negotiated: ClassVar[int] = 63
    # What a long frame fails with: the link going down, or the write going unacknowledged.
    failure: ClassVar[type[Exception]] = ManagementConnectionError
    # Whether standard frames fail as well (the retry does not help either).
    standard_fails: ClassVar[bool] = False
    restarted = False

    def __init__(self, *args: object, **kwargs: Any) -> None:
        self.ceiling = cast(int, kwargs["max_apdu_length"])
        self.negotiated_apdu_length: int | None = None
        self.state_mutated = False
        _LinkDroppingRunner.attempts.append(self.ceiling)
        _LinkDroppingRunner.memory_caps.append(kwargs.get("memory_service_count"))

    async def run(self, progress: object = None) -> None:
        self.negotiated_apdu_length = min(_LinkDroppingRunner.negotiated, self.ceiling)
        if self.negotiated_apdu_length > 15 or _LinkDroppingRunner.standard_fails:
            self.state_mutated = True
            raise _LinkDroppingRunner.failure("No ACK received for repeated telegram.")

    def captured_property(self, object_type: int, property_id: int) -> None:
        return None


async def _download_through(
    monkeypatch: pytest.MonkeyPatch,
    interface_max_apdu_length: int | None,
    found: list[int] | None = None,
) -> Any:
    _LinkDroppingRunner.attempts = []
    _LinkDroppingRunner.memory_caps = []
    _Manager.closed = 0

    async def same_image(*args: Any) -> Any:
        return args[3]

    async def same_scope(*args: Any) -> Any:
        return download_module._ScopeCheck(args[5])  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(download_module, "LoadProcedureRunner", _LinkDroppingRunner)
    monkeypatch.setattr(download_module, "_connection_manager", lambda *a: _Manager())
    monkeypatch.setattr(download_module, "_device_association_format", same_image)
    monkeypatch.setattr(download_module, "_partial_scope", same_scope)
    monkeypatch.setattr(download_module, "_resolve_controls", lambda *a, **k: [])
    connected = asyncio.Event()
    connected.set()
    xknx = SimpleNamespace(connection_manager=SimpleNamespace(connected=connected))
    return await download_module.download(
        cast(Any, xknx),
        "1.2.1",
        cast(
            Any,
            SimpleNamespace(
                program=SimpleNamespace(mask_version="MV-07B0", is_secure_enabled=False)
            ),
        ),
        image=cast(Any, object()),
        scope=DownloadScope.PARAMETERS,
        interface_max_apdu_length=interface_max_apdu_length,
        interface_limit_found=None if found is None else found.append,
    )


async def test_a_lost_long_frame_is_retried_with_standard_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The interface does not report its maximum (tunnelling v1, e.g. knxd).
    result = await _download_through(monkeypatch, None)
    assert _LinkDroppingRunner.attempts == [239, 15]
    # The caller learns the limit, so later downloads through it start there.
    assert result.interface_max_apdu_length == 15


async def test_an_interface_reporting_its_maximum_is_not_second_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(PartialDownloadError):
        await _download_through(monkeypatch, 55)
    assert _LinkDroppingRunner.attempts == [55]


async def test_a_successful_download_learns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_LinkDroppingRunner, "negotiated", 15)
    result = await _download_through(monkeypatch, None)
    assert _LinkDroppingRunner.attempts == [239]
    assert result.interface_max_apdu_length is None


def test_only_link_failures_with_long_frames_are_retried() -> None:
    may_drop = download_module._interface_may_drop_long_frames  # pyright: ignore[reportPrivateUsage]
    link = PartialDownloadError("partially loaded")
    link.__cause__ = ManagementConnectionError("No ACK received for repeated telegram.")
    assert may_drop(link, None, True, [63])
    # The interface reported its maximum, the APDU was fixed, or only standard frames went.
    assert not may_drop(link, 55, True, [63])
    assert not may_drop(link, None, False, [63])
    assert not may_drop(link, None, True, [15])
    # A device refusing something is not a link failure.
    assert not may_drop(VerificationError("refused"), None, True, [63])


async def test_the_limit_is_reported_before_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found: list[int] = []
    await _download_through(monkeypatch, None, found)
    assert found == [15]


async def test_the_limit_is_kept_when_the_retry_fails_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The device stops answering whatever the frames: the limit was still found, so the
    # next download starts with standard frames instead of failing on a long one first.
    monkeypatch.setattr(_LinkDroppingRunner, "standard_fails", True)
    found: list[int] = []
    with pytest.raises(PartialDownloadError):
        await _download_through(monkeypatch, None, found)
    assert found == [15]
    assert _LinkDroppingRunner.attempts == [239, 15]


async def test_a_failing_callback_does_not_end_the_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    class _BrokenStore(list[int]):
        """A caller whose store fails (e.g. a settings folder that is not writable)."""

        def append(self, length: int) -> None:
            calls.append(length)
            raise OSError("settings not writable")

    result = await _download_through(monkeypatch, None, _BrokenStore())
    assert calls == [15]
    assert result.interface_max_apdu_length == 15


async def test_an_unacknowledged_long_write_goes_straight_to_standard_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Through knxd the long write goes unacknowledged. A lower memory cap would still send
    # long frames (and take knxd's TPUART link down again), so the download skips the
    # memory-cap ladder and retries with standard frames at once.
    monkeypatch.setattr(_LinkDroppingRunner, "failure", MemoryWriteNotAcknowledged)
    found: list[int] = []
    result = await _download_through(monkeypatch, None, found)
    assert _LinkDroppingRunner.attempts == [239, 15]
    assert _LinkDroppingRunner.memory_caps == [None, None]
    assert found == [15]
    assert result.interface_max_apdu_length == 15


async def test_an_interface_reporting_its_maximum_keeps_the_memory_cap_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A device rejecting long memory writes behind an interface that reports long frames:
    # the memory-cap ladder still applies, and no interface limit is reported.
    monkeypatch.setattr(_LinkDroppingRunner, "failure", MemoryWriteNotAcknowledged)
    found: list[int] = []
    with pytest.raises(PartialDownloadError):
        await _download_through(monkeypatch, 55, found)
    assert _LinkDroppingRunner.attempts == [55, 55, 55]
    assert _LinkDroppingRunner.memory_caps == [None, 15, 12]
    assert found == []
