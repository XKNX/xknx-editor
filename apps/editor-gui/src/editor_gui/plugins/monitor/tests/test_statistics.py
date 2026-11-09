"""The Monitor Statistics aggregation: counts per source/destination, ranking, rate, and a
headless render of the Statistics tab."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui
from xknx.cemi import CEMIFrame, CEMILData, CEMIMessageCode
from xknx.dpt import DPTBinary
from xknx.telegram import GroupAddress, IndividualAddress, Telegram
from xknx.telegram.apci import DeviceDescriptorRead, GroupValueWrite

from editor_gui.plugins.monitor.formatting import (
    DeviceEntry,
    GroupAddressEntry,
    ProjectIndex,
)
from editor_gui.plugins.monitor.records import (
    TICKS_PER_SECOND,
    MonitorRecord,
    RecordKind,
)
from editor_gui.plugins.monitor.statistics import StatisticsView, compute


def _raw(
    code: CEMIMessageCode, destination: object, payload: object, src: str
) -> bytes:
    telegram = Telegram(destination_address=destination, payload=payload)
    data = CEMILData.init_from_telegram(telegram, src_addr=IndividualAddress(src))
    return CEMIFrame(code=code, data=data).to_knx()


def _group(src: str, dst: str, *, ticks: int) -> MonitorRecord:
    raw = _raw(
        CEMIMessageCode.L_DATA_IND,
        GroupAddress(dst),
        GroupValueWrite(DPTBinary(1)),
        src,
    )
    return MonitorRecord(kind=RecordKind.TELEGRAM, ticks=ticks, raw=raw)


def _index() -> ProjectIndex:
    index = ProjectIndex()
    index.devices[IndividualAddress("1.1.1").raw] = DeviceEntry(
        name="Switch", description="Hallway"
    )
    index.group_addresses[GroupAddress("1/2/3").raw] = GroupAddressEntry(
        address="1/2/3", name="Light On", description="Ceiling"
    )
    return index


def test_compute_counts_and_ranks() -> None:
    records = [
        _group("1.1.1", "1/2/3", ticks=0),
        _group("1.1.1", "1/2/3", ticks=1 * TICKS_PER_SECOND),
        _group("1.1.1", "1/2/4", ticks=2 * TICKS_PER_SECOND),
        _group("1.1.2", "1/2/3", ticks=3 * TICKS_PER_SECOND),
    ]
    stats = compute(records, _index())

    assert stats.total == 4
    # Sources ranked by count: 1.1.1 (3) before 1.1.2 (1); names/descriptions resolved.
    assert [(s.address, s.count) for s in stats.sources] == [("1.1.1", 3), ("1.1.2", 1)]
    top = stats.sources[0]
    assert (top.name, top.description) == ("Switch", "Hallway")
    assert stats.sources[1].name == ""  # unknown device: empty name and description
    # Destinations ranked: 1/2/3 (3) before 1/2/4 (1).
    assert [(d.address, d.count) for d in stats.destinations] == [
        ("1/2/3", 3),
        ("1/2/4", 1),
    ]
    assert (stats.destinations[0].name, stats.destinations[0].description) == (
        "Light On",
        "Ceiling",
    )


def test_compute_rate_over_span() -> None:
    # 4 telegrams spanning 3 seconds -> 4 / 3 * 60 = 80 per minute.
    records = [_group("1.1.1", "1/2/3", ticks=i * TICKS_PER_SECOND) for i in range(4)]
    stats = compute(records, ProjectIndex())
    assert stats.span_seconds == pytest.approx(3.0)
    assert stats.per_minute == pytest.approx(80.0)


def test_compute_peak_minute() -> None:
    minute = TICKS_PER_SECOND * 60
    # Minute 0: 2 telegrams; minute 1: 3 telegrams (the busiest).
    records = [
        _group("1.1.1", "1/2/3", ticks=0),
        _group("1.1.1", "1/2/3", ticks=5 * TICKS_PER_SECOND),
        _group("1.1.1", "1/2/3", ticks=minute),
        _group("1.1.1", "1/2/3", ticks=minute + 10 * TICKS_PER_SECOND),
        _group("1.1.1", "1/2/3", ticks=minute + 20 * TICKS_PER_SECOND),
    ]
    stats = compute(records, ProjectIndex())
    assert stats.peak_per_minute == 3
    assert stats.peak_ticks == minute  # start of the busiest clock-minute


def test_compute_skips_events_and_non_data() -> None:
    non_data = _raw(
        CEMIMessageCode.L_DATA_IND,
        IndividualAddress("1.1.5"),
        DeviceDescriptorRead(),
        "1.1.9",
    )
    records = [
        MonitorRecord(kind=RecordKind.START, ticks=0),
        MonitorRecord(kind=RecordKind.TELEGRAM, ticks=0, raw=b""),  # no frame
        MonitorRecord(kind=RecordKind.TELEGRAM, ticks=0, raw=non_data),  # not is_data
        _group("1.1.1", "1/2/3", ticks=0),
    ]
    stats = compute(records, ProjectIndex())
    assert stats.total == 1
    assert [s.address for s in stats.sources] == ["1.1.1"]


def test_empty_log_is_zeroed() -> None:
    stats = compute([], ProjectIndex())
    assert stats.total == 0
    assert stats.per_minute == 0.0
    assert stats.sources == [] and stats.destinations == []


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1024, 768)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _Channel:
    def __init__(self, log: object) -> None:
        self.log = log


class _Service:
    """A MonitorService stand-in exposing just what StatisticsView reads."""

    def __init__(self, records: list[MonitorRecord]) -> None:
        from editor_gui.plugins.monitor.records import TelegramLog
        from editor_gui.plugins.monitor.service import MonitorKind

        log = TelegramLog(0)
        log.replace(records)
        self.channels = {MonitorKind.GROUP: _Channel(log)}

    def bus_load(self, window: float = 3.0) -> float:
        return 1.5


@pytest.mark.usefixtures("context")
def test_statistics_tab_renders_headless() -> None:
    records = [_group("1.1.1", "1/2/3", ticks=i * TICKS_PER_SECOND) for i in range(5)]
    view = StatisticsView(_Service(records))  # type: ignore[arg-type]
    index = _index()
    for _ in range(3):  # a few frames: the first computes, the rest hit the cache
        imgui.new_frame()
        if imgui.begin("Monitor"):
            view.render(index)
        imgui.end()
        imgui.end_frame()
    # The cache holds the computed statistics after rendering.
    assert view._stats.total == 5  # type: ignore[attr-defined]


@pytest.mark.usefixtures("context")
def test_statistics_tab_empty_renders_headless() -> None:
    view = StatisticsView(_Service([]))  # type: ignore[arg-type]
    imgui.new_frame()
    if imgui.begin("Monitor"):
        view.render(ProjectIndex())
    imgui.end()
    imgui.end_frame()
    assert view._stats.total == 0  # type: ignore[attr-defined]


@pytest.mark.usefixtures("context")
def test_statistics_tab_persist_toggle() -> None:
    # The Persist toggle is shared with the Group Monitor: rendering it reads the getter, and
    # both the empty and the populated view show it (so it can be enabled before recording).
    state = {"persist": False}
    view = StatisticsView(
        _Service([]),  # type: ignore[arg-type]
        persist_get=lambda: state["persist"],
        persist_set=lambda v: state.__setitem__("persist", v),
    )
    for _ in range(2):
        imgui.new_frame()
        if imgui.begin("Monitor"):
            view.render(ProjectIndex())
        imgui.end()
        imgui.end_frame()
    # The getter is read without error; a view without callbacks simply omits the checkbox.
    plain = StatisticsView(_Service([]))  # type: ignore[arg-type]
    imgui.new_frame()
    if imgui.begin("Monitor"):
        plain.render(ProjectIndex())
    imgui.end()
    imgui.end_frame()
    assert state["persist"] is False  # no interaction simulated, state unchanged
