"""A structural rebuild of the device list reuses devices whose stored data did not change."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.prod.parser_v2.ui import UiNode, UiParameter, UiParameterBlock, UiTab

_APP_ID = "M-0008_A-7072-21-5CC3-O000A"


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("gira_2gang_button_interface.knxprod not found")


def _project(tmp_path: Path) -> tuple[ProjectService, list[int]]:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    product = next(p for p in cat.get_products() if p.application_id == _APP_ID)
    app = cat.get_application(_APP_ID)
    assert app is not None
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    ids = []
    for name in ("A", "B"):
        node_id = proj.add_device(
            product.product_ref_id, product.hardware2program_ref_id, name, app
        )
        assert node_id is not None
        ids.append(node_id)
    return proj, ids


def test_address_change_reuses_devices(tmp_path: Path) -> None:
    proj, (a, b) = _project(tmp_path)
    before = {d.node_id: d for d in proj.devices}
    device_a = before[a]
    assert proj.set_device_individual_address(a, device_a.individual_address, "1.1.200")
    after = {d.node_id: d for d in proj.devices}
    assert after[a] is device_a
    assert after[b] is before[b]
    assert device_a.individual_address == "1.1.200"
    proj.close()


def test_changed_device_is_rebuilt(tmp_path: Path) -> None:
    proj, (a, b) = _project(tmp_path)
    before = {d.node_id: d for d in proj.devices}
    ref = next(
        node.ref_id
        for node in _walk(before[a].get_ui())
        if isinstance(node, UiParameter)
    )
    pid = proj._pid
    assert pid is not None
    # stored data of device a changes behind the device object, then a structural rebuild runs
    proj._svc.set_parameter(pid, a, ref, "1")
    assert proj.set_device_individual_address(
        b, before[b].individual_address, "1.1.201"
    )
    after = {d.node_id: d for d in proj.devices}
    assert after[b] is before[b]
    assert after[a] is not before[a]
    proj.close()


def test_undo_after_in_place_edit_rebuilds_device(tmp_path: Path) -> None:
    proj, (a, _b) = _project(tmp_path)
    device = next(d for d in proj.devices if d.node_id == a)
    co = next(c for c in device.com_objects if c.db_id is not None)
    original = co.flags.read
    proj.set_flag(device, co.id, "read", not original)
    assert proj.undo()
    rebuilt = next(d for d in proj.devices if d.node_id == a)
    assert rebuilt is not device
    restored = rebuilt.find_com_object(co.id)
    assert restored is not None
    assert restored.flags.read == original
    proj.close()


def test_parameter_edited_in_place_is_rebuilt(tmp_path: Path) -> None:
    proj, (a, b) = _project(tmp_path)
    before = {d.node_id: d for d in proj.devices}
    ref = next(
        node.ref_id
        for node in _walk(before[a].get_ui())
        if isinstance(node, UiParameter)
    )
    before[a].apply_param_values({ref: "1"})
    assert proj.set_device_individual_address(
        b, before[b].individual_address, "1.1.202"
    )
    after = {d.node_id: d for d in proj.devices}
    assert after[a] is not before[a]
    assert after[b] is before[b]
    proj.close()


def _walk(nodes: Sequence[UiNode]) -> Iterator[UiNode]:
    for node in nodes:
        yield node
        if isinstance(node, (UiTab, UiParameterBlock)):
            yield from _walk(node.children)
