"""Logic tests for the DALI commissioning tab (gating + async op-runner state machine).

The imgui rendering isn't unit-tested; this covers the parts that decide behaviour: which devices
show the tab, and that a started operation toggles busy / stashes a scan result / surfaces errors.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from editor_gui.plugins.project.ui.dali_commissioning import (
    DaliCommissioningPanel,
    _assignments_from,
    _scenes_from,
)
from editor_gui.programming_dali import (
    dali_channel_count,
    dali_object_index_base,
    is_dali_dca_app,
)
from xknxeditor.dali.model import (
    DaliBusState,
    EcgAssignment,
    EcgState,
    Scene,
    SceneValue,
)


def test_gating_matches_only_dali_dca_apps() -> None:
    assert is_dali_dca_app("M-0083_A-0154-40-0F69-O00EF")  # MDT
    assert is_dali_dca_app("M-0083_A-0153-10-297A-O00EF")  # MDT
    assert is_dali_dca_app("M-0048_A-DA11-21-9400-O00EF")  # Theben P64
    assert not is_dali_dca_app("M-0083_A-9999-40")  # other MDT app
    assert not is_dali_dca_app("M-0001_A-0154")  # other manufacturer
    assert not is_dali_dca_app(None)


def test_object_index_base_is_vendor_specific() -> None:
    assert dali_object_index_base("M-0083_A-0154-40-0F69-O00EF") == 6  # MDT
    assert dali_object_index_base("M-0048_A-DA11-21-9400-O00EF") == 7  # Theben
    assert dali_object_index_base("M-0001_A-0154") == 6  # unknown -> default


def test_channel_count_from_order_number() -> None:
    assert dali_channel_count("SCN-DA641.04") == 1  # 1x64 gateway
    assert dali_channel_count("SCN-DA642.04") == 2  # 2x64 gateway
    assert dali_channel_count("scn-da642") == 2  # case-insensitive
    assert dali_channel_count("Something Else") == 1  # unknown -> single channel
    assert dali_channel_count("") == 1
    assert dali_channel_count(None) == 1


def _device() -> Any:
    return SimpleNamespace(individual_address="1.1.1", name="Gateway")


def _runner(commissioner: Any, *, started: bool = True, texts: list[str] | None = None):
    """A fake of the queue the panel hands its operations to: runs the wrapped coroutine (like
    the async loop would), swallowing exceptions (the real future captures them), then reports
    the end; ``started=False`` ends it without running it (canceled while it waited)."""

    def run(
        device: Any, wrapped: Any, channel: int, text: str, ended: Callable[[], None]
    ) -> None:
        if texts is not None:
            texts.append(text)
        if started:
            with contextlib.suppress(Exception):  # the real future captures the error
                asyncio.run(wrapped(commissioner))
        ended()

    return run


def test_scan_stashes_result_and_clears_busy() -> None:
    bus = DaliBusState(channel=0, ecgs=[EcgState(slot=0, present=True, group_index=5)])
    commissioner = SimpleNamespace(scan=lambda: _coro(bus))
    texts: list[str] = []
    panel = DaliCommissioningPanel(_runner(commissioner, texts=texts))

    panel._start(_device(), "scan", "Scan Bus", lambda c: c.scan(), store_scan=True)

    state = panel._state("1.1.1", 0)
    assert state.busy == ""  # released
    assert state.error == ""
    assert state.scan is bus
    # Named in the Operations panel as the button that started it.
    assert texts == ["Scan Bus"]


def test_error_is_surfaced_and_busy_released() -> None:
    async def boom(_c: Any) -> object:
        raise RuntimeError("no ack")

    commissioner = SimpleNamespace()
    panel = DaliCommissioningPanel(_runner(commissioner))
    panel._start(_device(), "commission", "Program Groups…", boom)

    state = panel._state("1.1.1", 0)
    assert state.busy == ""
    assert "no ack" in state.error


def test_released_when_it_never_ran() -> None:
    panel = DaliCommissioningPanel(_runner(SimpleNamespace(), started=False))
    panel._start(_device(), "scan", "Scan Bus", lambda c: c.scan(), store_scan=True)
    assert panel._state("1.1.1", 0).busy == ""  # canceled while it waited in the queue


def test_the_end_of_an_older_operation_keeps_a_newer_one() -> None:
    ends: list[Callable[[], None]] = []

    def run(
        device: Any, wrapped: Any, channel: int, text: str, ended: Callable[[], None]
    ) -> None:
        ends.append(ended)

    panel = DaliCommissioningPanel(run)
    panel._start(_device(), "scan", "Scan Bus", lambda c: c.scan())
    state = panel._state("1.1.1", 0)
    assert state.busy == "scan"
    # A second start while the first is queued is ignored.
    panel._start(_device(), "broadcast", "All Off", lambda c: c.broadcast(False))
    assert len(ends) == 1
    ends[0]()
    assert state.busy == ""
    panel._start(_device(), "broadcast", "All Off", lambda c: c.broadcast(False))
    ends[0]()  # the first one's end, once more
    assert state.busy == "broadcast"
    ends[1]()
    assert state.busy == ""


async def _coro(value: Any) -> Any:
    return value


def test_assignments_from_applies_group_edits() -> None:
    bus = DaliBusState(
        channel=0,
        ecgs=[
            EcgState(slot=0, present=True, group_index=5, ets_ecg_index=0),
            EcgState(slot=3, present=True, group_index=2, ets_ecg_index=3),
            EcgState(slot=7, present=False, group_index=255, ets_ecg_index=255),
        ],
    )
    assignments = _assignments_from(bus, {0: 7})  # move slot 0 to group 7
    assert assignments == [
        EcgAssignment(slot=0, ets_index=0, group_index=7),  # edited
        EcgAssignment(slot=3, ets_index=3, group_index=2),  # kept
    ]  # absent slot 7 excluded


def test_scenes_from_builds_sorted_values_and_skips_empty() -> None:
    scenes = _scenes_from({0: {5: 254, 2: 100}, 1: {}})
    assert scenes == [
        Scene(
            index=0,
            knx_scene=1,
            values=[
                SceneValue(scene=0, target_index=2, level=100),
                SceneValue(scene=0, target_index=5, level=254),
            ],
        )
    ]  # scene 1 (empty) dropped, values sorted by group


def test_new_scan_clears_stale_group_edits() -> None:
    bus = DaliBusState(channel=0, ecgs=[EcgState(slot=0, present=True, group_index=5)])
    commissioner = SimpleNamespace(scan=lambda: _coro(bus))
    panel = DaliCommissioningPanel(_runner(commissioner))
    panel._state("1.1.1", 0).group_edits[0] = 9  # stale edit from a prior scan

    panel._start(_device(), "scan", "Scan Bus", lambda c: c.scan(), store_scan=True)

    assert panel._state("1.1.1", 0).group_edits == {}  # dropped on fresh bus state
