"""Evaluated parameters of devices outside the selection (read by MCP, a check or another panel)
are released once nothing has used them for a frame, like those that fall out of the selection's
last-viewed devices (Settings > General > Memory Use)."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import pytest

from editor_gui.device import Device, collect_ui_parameters
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.prod import Application
from xknxeditor.prod import application as residency
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.ui.parameter import EnumWidget


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent
            / "packages"
            / "prod"
            / "tests"
            / "fixtures"
            / "fan_coil_application.xml"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("fan_coil_application.xml not found")


@pytest.fixture
def app() -> Application:
    return parse_application_xml(_fixture().read_bytes(), "M-00FA")[0]


@pytest.fixture
def proj(tmp_path: Path) -> ProjectService:
    service = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.new(tmp_path / "p.xknx")
    return service


def _devices(proj: ProjectService, app: Application, count: int) -> list[Device]:
    ids = [proj.add_device("P-1", "HP-1", f"Fan Coil {i}", app) for i in range(count)]
    devices = [proj.find_device_by_node_id(i) for i in ids if i is not None]
    assert len(devices) == count and all(d is not None for d in devices)
    return [d for d in devices if d is not None]


def test_parameters_read_outside_the_selection_are_released(
    proj: ProjectService, app: Application
) -> None:
    selected, other, third = _devices(proj, app, 3)
    proj.set_resident_devices(1)
    proj.selected_device = selected
    selected.get_ui()
    before = other.get_ui()
    third.get_ui()
    assert other.evaluated and third.evaluated
    # Used in this frame: kept over its end.
    proj.release_unused_dynamic_ui()
    assert other.evaluated and third.evaluated
    # Unused for a whole frame: released; the selected device stays.
    proj.release_unused_dynamic_ui()
    assert not other.evaluated and not third.evaluated
    assert selected.evaluated
    # Read again later: evaluated again with the same values.
    assert before and other.get_ui() == before
    assert other.evaluated


def test_enum_choices_are_shared_between_project_devices(
    proj: ProjectService, app: Application
) -> None:
    first, second = _devices(proj, app, 2)
    first_params = collect_ui_parameters(first.get_ui())
    second_params = collect_ui_parameters(second.get_ui())
    assert [(p.label, p.value) for p in first_params] == [
        ("Fan Coil", "0"),
        ("Lock", "0"),
        ("Output", "0"),
    ]
    assert second_params == first_params
    widget = first_params[0].widget
    assert isinstance(widget, EnumWidget)
    assert [(c.value, c.label) for c in widget.choices] == [(0, "No"), (1, "Yes")]
    assert all(p.widget is widget for p in first_params + second_params)


def test_an_evaluated_device_pins_its_application(
    proj: ProjectService, app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(residency, "_resident", OrderedDict())
    monkeypatch.setattr(residency, "_budget", [0])

    def load() -> Application:
        return parse_application_xml(_fixture().read_bytes(), "M-00FA")[0]

    app.set_reloader(load)
    (device,) = _devices(proj, app, 1)
    before = device.get_ui()
    other = Application.unloaded(
        app.id, app.name, app.version, app.manufacturer_id, load
    )
    other.resolved()
    assert app.loaded
    assert before and device.get_ui() == before

    device.release_dynamic_ui()
    other.resolved()
    assert not app.loaded


def test_a_device_used_every_frame_stays_evaluated(
    proj: ProjectService, app: Application
) -> None:
    selected, shown = _devices(proj, app, 2)
    proj.selected_device = selected
    for _frame in range(5):
        shown.get_ui()
        proj.release_unused_dynamic_ui()
    assert shown.evaluated


def test_the_multi_selection_stays_evaluated(
    proj: ProjectService, app: Application
) -> None:
    first, second, third = _devices(proj, app, 3)
    proj.set_resident_devices(1)
    proj.set_multi_selection(first.node_id, [first.node_id, second.node_id])
    for device in (first, second, third):
        device.get_ui()
    proj.release_unused_dynamic_ui()
    proj.release_unused_dynamic_ui()
    assert first.evaluated and second.evaluated
    assert not third.evaluated


def test_a_device_selected_after_a_read_follows_the_last_viewed_devices(
    proj: ProjectService, app: Application
) -> None:
    """A device read outside the selection and then selected is kept as one of the last viewed
    devices, not released at the end of the next frame."""
    first, second = _devices(proj, app, 2)
    proj.set_resident_devices(2)
    proj.selected_device = first
    first.get_ui()
    second.get_ui()
    proj.selected_device = second
    proj.release_unused_dynamic_ui()
    proj.release_unused_dynamic_ui()
    assert first.evaluated and second.evaluated
