"""Parameter changes with calculations: persistence, undo and the per-path modes."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from editor_gui.device import Device
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.button_runner import ButtonRunner
from editor_gui.plugins.project.service import ProjectService, _history_label
from editor_gui.plugins.project.strings import S
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.ui import UiButton
from xknxeditor.prod.script import CalculationError, ParameterValidationError

APP = "M-00FA_A-0001-01-0000"


def _ref(n: int) -> str:
    return f"{APP}_P-{n}_R-{n}"


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent
            / "packages"
            / "prod"
            / "tests"
            / "fixtures"
            / "calculations_application.xml"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("calculations_application.xml not found")


@pytest.fixture
def app() -> Application:
    return parse_application_xml(_fixture().read_bytes(), "M-00FA")[0]


@pytest.fixture
def proj(tmp_path: Path) -> ProjectService:
    service = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.new(tmp_path / "p.xknx")
    return service


def _add(proj: ProjectService, app: Application) -> int:
    node_id = proj.add_device("P-1", "HP-1", "Calc", app)
    assert node_id is not None
    return node_id


def _stored(proj: ProjectService, node_id: int) -> dict[str, str]:
    proj._refresh_device(node_id)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    return {p.ref_id: p.value for p in device.parameter_instance_refs}


def test_edit_persists_calculated_values_as_one_step(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.record_download(node_id, "full")
    assert proj.pending_downloads() == {}
    proj.set_param(device, _ref(1), "5")
    assert proj.pending_downloads()[node_id] == ("parameters",)
    stored = _stored(proj, node_id)
    assert stored[_ref(1)] == "5"
    assert stored[_ref(2)] == "11"
    assert stored[_ref(4)] == "15"

    assert proj.undo()
    assert proj.pending_downloads() == {}
    stored = _stored(proj, node_id)
    assert _ref(1) not in stored
    assert _ref(2) not in stored
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    assert device.get_param_value(_ref(2)) == "2"


def test_rejected_edit_stores_nothing(proj: ProjectService, app: Application) -> None:
    node_id = _add(proj, app)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    with pytest.raises(CalculationError):
        proj.set_param(device, _ref(5), "9")
    assert proj.param_error(node_id, _ref(5)) == ("bad")
    assert _ref(5) not in _stored(proj, node_id)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    with pytest.raises(ValueError):
        proj.set_param(device, _ref(1), "5000")
    assert proj.param_error(node_id, _ref(1)) is not None
    proj.set_param(device, _ref(1), "2")
    assert proj.param_error(node_id, _ref(1)) is None


def test_transfer_runs_calculations_and_skips_rejected(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    assert proj._apply_params(node_id, [(_ref(5), "9"), (_ref(3), "20")]) == 1
    stored = _stored(proj, node_id)
    assert stored[_ref(3)] == "20"
    assert stored[_ref(4)] == "21"
    assert _ref(5) not in stored


def test_raw_mode_skips_calculations(proj: ProjectService, app: Application) -> None:
    node_id = _add(proj, app)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.edit_params(device, [(_ref(1), "7")], mode="raw")
    stored = _stored(proj, node_id)
    assert stored[_ref(1)] == "7"
    assert _ref(2) not in stored


def test_batched_edits_run_each_calculation_once(
    proj: ProjectService, app: Application
) -> None:
    # ref2 = 2*ref1+1 (PC-1), ref3 = ref3 + ref2 (PC-2, accumulator), ref4 = ref3 + 1 (PC-3).
    # One plan per edit would recompute PC-2 twice (ref3 = 3+11, then 14+11 = 25); the batch applies
    # both direct edits first and runs each calculation once (ref3 = 3+11 = 14).
    node_id = _add(proj, app)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.edit_params(device, [(_ref(1), "5"), (_ref(2), "11")], mode="edit")
    stored = _stored(proj, node_id)
    assert stored[_ref(3)] == "14"
    assert stored[_ref(4)] == "15"


def test_script_changes_dropped_after_project_switch(
    proj: ProjectService, app: Application, tmp_path: Path
) -> None:
    # A bus operation captures the project generation; once a different project is open its deferred
    # writes must be dropped instead of landing in the wrong project.
    node_id = _add(proj, app)
    generation = proj.generation
    proj.new(tmp_path / "other.xknx")
    assert proj.generation != generation
    proj.persist_script_changes(
        node_id, {_ref(1): (None, "5")}, None, generation=generation
    )
    proj.finish_script_changes(node_id, set(), generation=generation)
    # The stale writes were dropped: the freshly opened project has no such device.
    assert proj.find_device_by_node_id(node_id) is None


def test_recalculate_persists_derived_values(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    proj.edit_params(device, [(_ref(1), "7")], mode="raw")
    proj.recalculate_params(node_id, [_ref(1)])
    assert _stored(proj, node_id)[_ref(2)] == "15"


def test_labelled_composite_history() -> None:
    assert _history_label(
        "Composite", {"events": [], "label": "Button X executed."}
    ) == ("Button X executed.")


def test_validation_rejects_edit_but_not_transfer(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    with pytest.raises(ParameterValidationError):
        proj.set_param(device, _ref(3), "13")
    assert device.param_errors[_ref(3)] == S.VALIDATION_FAILED
    assert device.param_inputs[_ref(3)] == "13"
    assert _ref(3) not in _stored(proj, node_id)
    assert proj._apply_params(node_id, [(_ref(3), "13")]) == 1
    assert _stored(proj, node_id)[_ref(3)] == "13"


def _run_button(
    proj: ProjectService, node_id: int, handler: str, params: str = ""
) -> Device:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    runner = ButtonRunner(proj, Logger(LogService(), "project"))
    button = UiButton(
        id=f"{APP}_B-1", text="Run", handler=handler, handler_parameters=params or None
    )
    runner.start(device, button)
    deadline = time.monotonic() + 10
    while runner.busy and time.monotonic() < deadline:
        time.sleep(0.01)
        runner.poll()
    assert not runner.busy
    assert not device.script_running
    return device


def test_offline_button_is_one_labelled_undo_step(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    _run_button(proj, node_id, "offlineButton", '{"n": 2}')
    stored = _stored(proj, node_id)
    assert (stored[_ref(1)], stored[_ref(2)]) == ("3", "7")
    assert proj.history()[0].display_text == S.BUTTON_EXECUTED.format("Run")
    assert proj.undo()
    stored = _stored(proj, node_id)
    assert _ref(1) not in stored and _ref(2) not in stored


def test_failing_offline_button_changes_nothing(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    entries = len(proj.history())
    device = _run_button(proj, node_id, "failingButton")
    assert device.param_errors[f"{APP}_B-1"] == "stop"
    assert device.get_param_value(_ref(3)) == "3"
    assert _ref(3) not in _stored(proj, node_id)
    assert len(proj.history()) == entries


def test_rejected_input_is_submitted_again_after_other_edits(
    proj: ProjectService, app: Application
) -> None:
    node_id = _add(proj, app)
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    with pytest.raises(ParameterValidationError):
        proj.set_param(device, _ref(1), "499")
    assert device.param_inputs[_ref(1)] == "499"
    proj.set_param(device, _ref(3), "4")
    assert _ref(1) in device.param_inputs
    proj.edit_params(device, [(_ref(2), "0"), (_ref(3), "0")], mode="raw")
    proj.set_param(device, _ref(7), "K1")
    assert _ref(1) not in device.param_inputs
    assert _ref(1) not in device.param_errors
    assert device.get_param_value(_ref(1)) == "499"
