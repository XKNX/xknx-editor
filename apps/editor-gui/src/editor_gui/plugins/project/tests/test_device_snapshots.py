"""A reopened project restores its devices from their snapshots without loading their applications,
identical to evaluating them; a device whose data changed is evaluated again."""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from editor_gui.device import Device
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


def _service(cat: CatalogService) -> ProjectService:
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    return proj


def _project(tmp_path: Path) -> tuple[CatalogService, Path, list[int]]:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    product = next(p for p in cat.get_products() if p.application_id == _APP_ID)
    app = cat.get_application(_APP_ID)
    assert app is not None
    proj = _service(cat)
    path = tmp_path / "p.xknx"
    proj.new(path)
    ids = []
    for name in ("A", "B"):
        node_id = proj.add_device(
            product.product_ref_id, product.hardware2program_ref_id, name, app
        )
        assert node_id is not None
        ids.append(node_id)
    _ = proj.devices  # evaluated, and the snapshots stored
    proj.close()
    return cat, path, ids


def _state(device: Device) -> tuple[Any, ...]:
    return (
        device.app.id if device.app else None,
        device.app.name if device.app else None,
        device.app.version if device.app else None,
        device.com_objects,
        device.get_visible_com_objects(),
        device.download_plugin_required,
        device.configuration_app,
    )


def _evaluated(cat: CatalogService, path: Path) -> dict[int, tuple[Any, ...]]:
    """Every device evaluated from its application (no snapshots)."""
    proj = _service(cat)
    proj._store_snapshots = lambda: None
    proj.open(path)
    proj._snapshots = {}
    result = {d.node_id: (_state(d), repr(d.get_ui())) for d in proj.devices}
    proj.close()
    return result


def test_reopen_restores_devices_without_loading_their_application(
    tmp_path: Path,
) -> None:
    cat, path, ids = _project(tmp_path)
    expected = _evaluated(cat, path)
    proj = _service(cat)
    proj.open(path)
    devices = {d.node_id: d for d in proj.devices}
    assert sorted(devices) == sorted(ids)
    for node_id, device in devices.items():
        assert device.app is not None
        assert not device.app.loaded
        assert _state(device) == expected[node_id][0]
    # Used, the application loads and the device evaluates exactly as without a snapshot.
    for node_id, device in devices.items():
        assert repr(device.get_ui()) == expected[node_id][1]
    assert all(d.app is not None and d.app.loaded for d in devices.values())
    proj.close()


def test_changed_device_is_evaluated_again(tmp_path: Path) -> None:
    cat, path, (a, b) = _project(tmp_path)
    proj = _service(cat)
    proj.open(path)
    device = next(d for d in proj.devices if d.node_id == a)
    ref = next(
        node.ref_id for node in _walk(device.get_ui()) if isinstance(node, UiParameter)
    )
    pid = proj._pid
    assert pid is not None
    proj._svc.set_parameter(pid, a, ref, "1")
    proj.close()
    expected = _evaluated(cat, path)
    proj = _service(cat)
    proj.open(path)
    devices = {d.node_id: d for d in proj.devices}
    # The changed device is evaluated (its application loads), the other one restored.
    assert devices[a].app is not None and devices[a].app.loaded
    assert _state(devices[a]) == expected[a][0]
    assert _state(devices[b]) == expected[b][0]
    proj.close()
    proj = _service(cat)
    proj.open(path)
    restored = next(d for d in proj.devices if d.node_id == a)
    assert restored.app is not None and not restored.app.loaded
    assert _state(restored) == expected[a][0]
    proj.close()


@pytest.mark.parametrize("data", ["not json", '{"format": 0}', "[]"])
def test_unusable_snapshot_is_evaluated_again(tmp_path: Path, data: str) -> None:
    cat, path, (a, _b) = _project(tmp_path)
    expected = _evaluated(cat, path)
    proj = _service(cat)
    proj.open(path)
    pid = proj._pid
    assert pid is not None
    key = proj._svc.device_snapshots(pid)[a][0]
    proj._svc.store_device_snapshots(pid, {a: (key, data)})
    proj.close()
    proj = _service(cat)
    proj.open(path)
    device = next(d for d in proj.devices if d.node_id == a)
    assert _state(device) == expected[a][0]
    proj.close()


def test_snapshot_of_other_content_is_not_used(tmp_path: Path) -> None:
    cat, path, (a, b) = _project(tmp_path)
    proj = _service(cat)
    proj.open(path)
    pid = proj._pid
    assert pid is not None
    snapshots = proj._svc.device_snapshots(pid)
    # Device b's data under device a's id: the key does not match device a, so it is evaluated.
    proj._svc.store_device_snapshots(pid, {a: ("0" * 64, snapshots[b][1])})
    proj.close()
    proj = _service(cat)
    proj.open(path)
    device = next(d for d in proj.devices if d.node_id == a)
    assert device.app is not None and device.app.loaded
    proj.close()


def test_application_loads_in_the_background(tmp_path: Path) -> None:
    cat, path, (a, _b) = _project(tmp_path)
    proj = _service(cat)
    proj.open(path)
    device = next(d for d in proj.devices if d.node_id == a)
    assert not proj.application_ready(device)
    _wait(lambda: proj.application_ready(device))
    assert device.app is not None and device.app.loaded
    proj.close()


def test_application_that_fails_to_load_leaves_its_devices_unloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cat, path, ids = _project(tmp_path)

    def broken(*_args: object) -> None:
        raise LookupError("product data missing")

    monkeypatch.setattr(cat._service, "load_application_program", broken)
    monkeypatch.setattr(cat._service, "get_application", broken)
    proj = _service(cat)
    proj.open(path)
    device = next(d for d in proj.devices if d.node_id == ids[0])
    assert not proj.application_ready(device)
    _wait(lambda: device.app is not None and device.app.id in proj._failed_loads)
    assert not proj.application_ready(device)
    assert proj.devices == []
    assert sorted(d.node_id for d in proj.unloaded_devices) == sorted(ids)
    proj.close()


def _wait(condition: Any, timeout: float = 30.0) -> None:
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


def _walk(nodes: Sequence[UiNode]) -> Iterator[UiNode]:
    for node in nodes:
        yield node
        if isinstance(node, (UiTab, UiParameterBlock)):
            yield from _walk(node.children)
