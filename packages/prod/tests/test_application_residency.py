"""Only the most recently used reloadable applications keep their parsed program in memory, as
many as their estimated memory fits in the budget."""

import gc
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from weakref import WeakValueDictionary

import pytest

import xknxeditor.prod.application as application_module
from xknxeditor.prod import parse_application_xml
from xknxeditor.prod.application import Application
from xknxeditor.prod.archive import Archive
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

_FIXTURE = Path(__file__).parent / "fixtures" / "gira_2gang_button_interface.knxprod"


@pytest.fixture(autouse=True)
def _isolated_residency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application_module, "_resident", OrderedDict())
    monkeypatch.setattr(application_module, "_loading", {})
    monkeypatch.setattr(application_module, "_evaluated", WeakValueDictionary())
    monkeypatch.setattr(application_module, "_memory", {})
    # A budget no application fits in: only the one used last stays.
    monkeypatch.setattr(application_module, "_budget", [0])


def _parse() -> Application:
    with Archive(str(_FIXTURE)) as archive:
        mid = min(archive.manufacturer_ids)
        _app_id, xml = next(iter(archive.get_application_xmls(mid).items()))
    return parse_application_xml(xml, mid)[0]


def _reloadable() -> tuple[Application, list[int]]:
    app = _parse()
    loads: list[int] = []

    def reload():
        loads.append(1)
        return _parse().program

    app.set_reloader(reload)
    return app, loads


def test_least_recently_used_application_is_dropped_and_reloaded() -> None:
    first, first_loads = _reloadable()
    app_id, name = first.id, first.name
    second, _ = _reloadable()
    assert first._program is None  # dropped for the more recently used one
    assert second._program is not None

    # id and name stay available without parsing again
    assert (first.id, first.name) == (app_id, name)
    assert first_loads == []

    assert first.program.id == app_id
    assert first_loads == [1]
    assert second._program is None


def test_program_and_tree_are_resolved_together() -> None:
    app, _ = _reloadable()
    program, tree_builder = app.resolved()
    assert app.tree_builder() is tree_builder
    assert app.program is program

    other, _ = _reloadable()
    other.resolved()
    assert app._tree_builder is None
    reloaded_program, reloaded_tree = app.resolved()
    assert reloaded_program is not program
    assert reloaded_tree is not tree_builder
    assert app.program is reloaded_program


def test_application_without_reloader_is_kept() -> None:
    plain = _parse()
    _reloadable()
    _reloadable()
    assert plain._program is not None
    assert application_module._resident.get(id(plain)) is None


def test_applications_stay_while_they_fit_in_the_budget() -> None:
    first, first_loads = _reloadable()
    estimate = application_module._memory[first.id]
    assert estimate > 0
    # Room for two of them: both stay.
    application_module.set_resident_budget(2 * estimate)
    second, _ = _reloadable()
    assert first._program is not None
    assert second._program is not None
    # Room for one: the least recently used goes at once, the one used last stays.
    application_module.set_resident_budget(estimate)
    assert first._program is None
    assert second._program is not None
    assert application_module.resident_budget() == estimate
    assert first.program.id == second.id
    assert first_loads == [1]
    assert second._program is None


def test_the_estimate_follows_the_size_of_the_xml() -> None:
    app = _parse()
    with Archive(str(_FIXTURE)) as archive:
        mid = min(archive.manufacturer_ids)
        _app_id, xml = next(iter(archive.get_application_xmls(mid).items()))
    assert application_module._memory[app.id] == len(xml) * 5


@pytest.mark.parametrize("fits", [False, True])
def test_lazy_load_makes_room_before_calling_loader(fits: bool) -> None:
    first, _ = _reloadable()
    first.resolved()
    estimate = application_module._memory[first.id]
    application_module.set_resident_budget((2 if fits else 1) * estimate)

    def load() -> Application:
        assert first.loaded is fits
        return _parse()

    second = _unloaded(first, load)
    second.resolved()
    assert second.loaded
    assert first.loaded is fits


def _unloaded(app: Application, loader: Callable[[], Application]) -> Application:
    return Application.unloaded(
        app.id, app.name, app.version, app.manufacturer_id, loader
    )


def test_evaluating_device_keeps_its_program_resident() -> None:
    first, loads = _reloadable()
    program, tree = first.resolved()
    ui = DynamicUI(program, tree_builder=tree)
    expected = ui.ui()

    def load() -> Application:
        assert first.loaded
        return _parse()

    second = _unloaded(first, load)
    second.resolved()
    assert first.loaded and second.loaded
    assert first._program is program
    assert first._tree_builder is tree
    assert ui.ui() == expected
    del ui
    second.resolved()
    assert not first.loaded
    assert loads == []


def test_concurrent_loads_preserve_pairs_and_reserve_their_combined_estimate() -> None:
    resident, _ = _reloadable()
    resident.resolved()
    estimate = application_module._memory[resident.id]
    application_module.set_resident_budget(2 * estimate)
    started, finish = Event(), Event()
    loads: list[Application] = []

    def load_first() -> Application:
        assert resident.loaded
        started.set()
        assert finish.wait(10)
        loaded = _parse()
        loads.append(loaded)
        return loaded

    def load_second() -> Application:
        assert not resident.loaded
        return _parse()

    first = _unloaded(resident, load_first)
    second = _unloaded(resident, load_second)
    with ThreadPoolExecutor(3) as workers:
        pending = workers.submit(first.resolved)
        try:
            assert started.wait(10)
            same = workers.submit(first.resolved)
            other = workers.submit(second.resolved)
            second_program, second_tree = other.result(timeout=10)
            assert second.resolved() == (second_program, second_tree)
        finally:
            finish.set()
        first_program, first_tree = pending.result(timeout=10)
        assert same.result(timeout=10) == (first_program, first_tree)

    assert len(loads) == 1
    assert first_program is loads[0].program
    assert first.resolved() == (first_program, first_tree)
    assert first.loaded and second.loaded


def test_failed_load_releases_its_reservation() -> None:
    first, _ = _reloadable()
    first.resolved()
    estimate = application_module._memory[first.id]
    application_module.set_resident_budget(2 * estimate)

    def fail() -> Application:
        raise ValueError("broken application")

    broken = _unloaded(first, fail)
    with pytest.raises(ValueError, match="broken application"):
        broken.resolved()
    second = _unloaded(first, _parse)
    second.resolved()
    assert first.loaded and second.loaded


@pytest.mark.parametrize("tree_already_loaded", [False, True])
def test_dynamic_ui_pins_the_program_before_its_constructor(
    monkeypatch: pytest.MonkeyPatch, tree_already_loaded: bool
) -> None:
    first, loads = _reloadable()
    if tree_already_loaded:
        first.resolved()
    second = _unloaded(first, _parse)
    original_init = DynamicUI.__init__

    def init(ui: DynamicUI, program, **kwargs) -> None:
        second.resolved()  # Trigger a pre-eviction between resolve and construction.
        assert first.loaded
        assert first.program is program
        original_init(ui, program, **kwargs)

    monkeypatch.setattr(DynamicUI, "__init__", init)
    ui = first.dynamic_ui()
    assert ui is not None
    assert loads == []


def test_xml_metadata_resizes_a_warm_load_before_decoding() -> None:
    first, _ = _reloadable()
    first.resolved()
    estimate = application_module._memory[first.id]
    unknown = application_module._UNKNOWN_MEMORY
    application_module.set_resident_budget(2 * estimate + unknown)
    second, _ = _reloadable()
    second.resolved()
    app_id = first.id + "-new"
    program = replace(first.program, id=app_id)
    # Keep the oldest entry on the left after obtaining the test program.
    second.resolved()
    assert app_id not in application_module._memory

    def load():
        assert first.loaded and second.loaded
        application_module.note_application_size(
            app_id, (unknown + estimate) // application_module._MEMORY_PER_XML_OCTET
        )
        assert not first.loaded
        assert second.loaded  # The resized estimate is counted once.
        return program

    third = Application.unloaded(
        app_id, first.name, first.version, first.manufacturer_id, load
    )
    assert third.program is program


@pytest.mark.parametrize("eager", [False, True])
def test_pre_eviction_error_releases_its_reservation(
    monkeypatch: pytest.MonkeyPatch, eager: bool
) -> None:
    app = _parse()

    def fail(*, keep_last: bool) -> None:
        assert not keep_last
        assert list(application_module._loading.values()) == [app.id]
        raise RuntimeError("eviction failed")

    monkeypatch.setattr(application_module, "_evict", fail)
    with pytest.raises(RuntimeError, match="eviction failed"):
        if eager:
            with application_module.reserve_application_memory(app.id, xml_size=100):
                pytest.fail("entered the reservation")
        else:
            _unloaded(app, _parse).resolved()
    assert not application_module._loading


def test_resident_program_cannot_be_unloaded_during_tree_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _ = _reloadable()
    program = first.program
    other = _unloaded(first, _parse)
    entered, finish = Event(), Event()
    original_init = DynamicTreeBuilder.__init__

    def init(builder: DynamicTreeBuilder, building_program) -> None:
        if building_program is program:
            entered.set()
            assert finish.wait(10)
        original_init(builder, building_program)

    monkeypatch.setattr(DynamicTreeBuilder, "__init__", init)
    with ThreadPoolExecutor(1) as workers:
        pending = workers.submit(first.resolved)
        try:
            assert entered.wait(10)
            assert application_module._loading[id(first)] == first.id
            other.resolved()
            first.unload()
            assert first._program is program
        finally:
            finish.set()
        assert pending.result(timeout=10)[0] is program
    assert not application_module._loading


def test_collecting_an_evaluator_cycle_releases_the_pin() -> None:
    class CyclicUI(DynamicUI):
        __slots__ = ("cycle",)

    first, _ = _reloadable()
    program, tree = first.resolved()
    other = _unloaded(first, _parse)
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        ui = CyclicUI(program, tree_builder=tree)
        ui.cycle = ui
        del ui
        other.resolved()
        assert first.loaded
        gc.collect()
        assert not application_module._evaluated
        assert first.loaded  # Release alone does not evict from the LRU.
        other.resolved()
        assert not first.loaded
    finally:
        if gc_was_enabled:
            gc.enable()
