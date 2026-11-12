"""The values Assigns write in an edit, and the defaults of the targets whose Assigns stop applying,
are stored with the edit as one undo step; an undo sets back the stored values without such
resets. A device's first evaluation after the project opened and the one after an undo or redo
of its parameters calculate it anew: every Assign that applies writes its target once, stored as
no undo step. The rebuilds after edits go on from the stored values."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import (
    ProjectService,
    _parameter_devices,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml

APP = "M-00FA_A-0007-01-0000"
ENABLED = f"{APP}_P-1_R-1"
TYPE = f"{APP}_P-2_R-2"
MODE = f"{APP}_P-3_R-3"


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


def _stored(proj: ProjectService, node_id: int) -> dict[str, str]:
    proj._refresh_device(node_id)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    return {p.ref_id: p.value for p in device.parameter_instance_refs}


def _value(proj: ProjectService, node_id: int, ref_id: str) -> str | None:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    device.get_ui()
    return device.get_param_value(ref_id)


def _objects(proj: ProjectService, node_id: int) -> list[str]:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    return sorted(co.id.rsplit("_", 2)[-2] for co in device.com_objects)


def test_an_edit_stores_what_its_assigns_did(
    proj: ProjectService, app: Application
) -> None:
    node_id = proj.add_device("P-1", "HP-1", "Fan Coil", app)
    assert node_id is not None
    assert _value(proj, node_id, TYPE) == "0"
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, ENABLED, "1")
    # Type's Assign stopped applying: its default; Type 1 assigns Mode 2.
    assert _stored(proj, node_id) == {ENABLED: "1", TYPE: "1", MODE: "2"}
    assert "O-3" in _objects(proj, node_id)

    assert proj.undo()
    assert _stored(proj, node_id) == {}
    assert _value(proj, node_id, TYPE) == "0"
    assert proj.redo()
    assert _stored(proj, node_id) == {ENABLED: "1", TYPE: "1", MODE: "2"}


def test_an_undo_keeps_the_stored_values(
    proj: ProjectService, app: Application
) -> None:
    node_id = proj.add_device("P-1", "HP-1", "Fan Coil", app)
    assert node_id is not None
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, ENABLED, "1")
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, TYPE, "2")
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, ENABLED, "0")
    assert _stored(proj, node_id) == {ENABLED: "0", TYPE: "0", MODE: "1"}
    # Back to Type 2 with the channel on: no reset of Type, although its Assign stops applying.
    assert proj.undo()
    assert _stored(proj, node_id) == {ENABLED: "1", TYPE: "2", MODE: "2"}
    assert _value(proj, node_id, TYPE) == "2"


def test_a_target_set_directly_keeps_its_value_after_the_rebuild(
    proj: ProjectService, app: Application
) -> None:
    # The device is built again from the stored values after each edit: the Assign of Type 1
    # writes Mode only when it starts applying again, not at each rebuild.
    node_id = proj.add_device("P-1", "HP-1", "Fan Coil", app)
    assert node_id is not None
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, ENABLED, "1")
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, MODE, "1")
    assert _stored(proj, node_id)[MODE] == "1"
    assert _value(proj, node_id, MODE) == "1"
    assert "O-3" not in _objects(proj, node_id)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, f"{APP}_P-4_R-4", "1")
    assert _value(proj, node_id, MODE) == "1"
    # Off and on again: the Assign starts applying and writes Mode.
    for enabled in ("0", "1"):
        device = proj.find_device_by_node_id(node_id)
        assert device is not None
        proj.set_param(device, ENABLED, enabled)
    assert _value(proj, node_id, MODE) == "2"
    assert "O-3" in _objects(proj, node_id)


def _row_values(proj: ProjectService, node_id: int) -> dict[str, str]:
    """The values the project stores for the device."""
    assert proj._pid is not None
    row = next(
        r for r in proj._svc.devices(proj._pid, with_contents=True) if r.id == node_id
    )
    return {p.ref_id: p.value for p in row.parameters}


def _set(proj: ProjectService, node_id: int, ref_id: str, value: str) -> None:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.set_param(device, ref_id, value)


def _mode_set_directly(proj: ProjectService, app: Application) -> int:
    """A device whose Mode, the target of the Assign of Type 1, was set directly."""
    node_id = proj.add_device("P-1", "HP-1", "Fan Coil", app)
    assert node_id is not None
    _set(proj, node_id, ENABLED, "1")
    _set(proj, node_id, MODE, "1")
    assert _value(proj, node_id, MODE) == "1"
    return node_id


def _reopened(proj: ProjectService, app: Application, path: Path) -> ProjectService:
    proj.close()
    service = ProjectService(proj._catalog)
    service.set_logger(Logger(LogService(), "project"))
    service._resolve_app = lambda program_ref, application_ref=None: app  # type: ignore[method-assign]
    service.open(path)
    return service


def test_a_device_first_used_after_opening_is_calculated_anew(
    proj: ProjectService, app: Application, tmp_path: Path
) -> None:
    # Every Assign that applies writes its target once: Mode gets the value of the Assign of
    # Type 1 again. It is stored, but as no undo step.
    node_id = _mode_set_directly(proj, app)
    proj = _reopened(proj, app, tmp_path / "p.xknx")
    history = len(proj.history())
    assert _row_values(proj, node_id)[MODE] == "1"
    assert _value(proj, node_id, MODE) == "2"
    proj.take_calculated_values()
    assert _row_values(proj, node_id)[MODE] == "2"
    assert len(proj.history()) == history
    assert proj.undo()
    assert _row_values(proj, node_id)[MODE] == "2"
    # Built again after an edit, the device goes on from its stored values.
    assert proj.redo()
    _set(proj, node_id, MODE, "1")
    _set(proj, node_id, f"{APP}_P-4_R-4", "1")
    assert _value(proj, node_id, MODE) == "1"


def test_an_undo_or_redo_calculates_the_device_anew(
    proj: ProjectService, app: Application
) -> None:
    node_id = _mode_set_directly(proj, app)
    _set(proj, node_id, f"{APP}_P-4_R-4", "1")
    assert _value(proj, node_id, MODE) == "1"
    history = len(proj.history())
    # The undo of the other edit: Mode gets the Assign's value, stored as no undo step.
    assert proj.undo()
    assert _value(proj, node_id, MODE) == "2"
    assert _row_values(proj, node_id)[MODE] == "2"
    assert len(proj.history()) == history
    # The value set directly comes back with its redo, and is written over at once.
    assert proj.undo()
    assert proj.redo()
    assert _value(proj, node_id, MODE) == "2"
    assert _row_values(proj, node_id)[MODE] == "2"


def test_an_undo_of_a_composite_step_calculates_the_device_anew(
    proj: ProjectService, app: Application
) -> None:
    # Switching the channel off stores the defaults of the targets with the group objects it
    # changes; its undo sets Mode back to the value set directly, which the Assign writes over.
    node_id = _mode_set_directly(proj, app)
    _set(proj, node_id, ENABLED, "0")
    assert proj._pid is not None
    peek = proj._svc.peek_undo(proj._pid)
    assert peek is not None and peek[0] == "Composite"
    assert proj.undo()
    assert _value(proj, node_id, MODE) == "2"
    assert _row_values(proj, node_id)[MODE] == "2"
    assert "O-3" in _objects(proj, node_id)


def test_a_jump_in_the_history_calculates_the_device_anew(
    proj: ProjectService, app: Application
) -> None:
    node_id = _mode_set_directly(proj, app)
    _set(proj, node_id, f"{APP}_P-4_R-4", "1")
    _set(proj, node_id, f"{APP}_P-4_R-4", "0")
    # Back to the step that set Mode directly (the history lists the newest first).
    proj.jump_to(proj.history()[2].id)
    assert _value(proj, node_id, MODE) == "2"
    proj.take_calculated_values()
    assert _row_values(proj, node_id)[MODE] == "2"


def test_an_undo_over_several_devices_calculates_those_in_use_at_once(
    proj: ProjectService, app: Application
) -> None:
    # Both devices were used: the undo of one step over both stores what their Assigns write
    # at once, without either being shown again.
    nodes = [_mode_set_directly(proj, app) for _ in range(2)]
    with proj.one_undo_step():
        for node_id in nodes:
            _set(proj, node_id, f"{APP}_P-4_R-4", "1")
    assert [_row_values(proj, n)[MODE] for n in nodes] == ["1", "1"]
    assert proj.undo()
    assert [_row_values(proj, n)[MODE] for n in nodes] == ["2", "2"]
    assert [_value(proj, n, MODE) for n in nodes] == ["2", "2"]


def test_an_undone_application_update_or_change_calculates_the_device_anew() -> None:
    # Both set the device's parameter values, also inside a composite step.
    for event_type in ("UpdateDeviceApplication", "ChangeDeviceApplication"):
        assert _parameter_devices(event_type, {"device_id": 7}) == {7}
        step = {"events": [{"type": event_type, "data": {"device_id": 8}}]}
        assert _parameter_devices("Composite", step) == {8}
    assert _parameter_devices("SetComment", {"device_id": 7}) == set()
