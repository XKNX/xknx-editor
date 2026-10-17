"""A device whose snapshot another editor version made opens from it at once and is evaluated again
in the background: replaced when its group objects came out different, and stored with a snapshot
of this version either way."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from editor_gui.device_snapshot import code_fingerprint
from editor_gui.plugins.project import service as service_module
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.tests.test_device_snapshots import (
    _evaluated,
    _project,
    _service,
    _state,
)


def _from_other_code(proj: ProjectService, change: Any = None) -> None:
    """Make every stored snapshot look made by another editor version (``change`` may alter the
    data too)."""
    pid = proj._pid
    assert pid is not None
    snapshots = proj._svc.device_snapshots(pid)
    proj._svc.store_device_snapshots(
        pid,
        {
            device_id: (
                f"{key.partition('.')[0]}.{'f' * 64}",
                change(data) if change is not None else data,
            )
            for device_id, (key, data) in snapshots.items()
        },
    )


def _rechecked(proj: ProjectService, timeout: float = 30.0) -> None:
    """Wait for the background evaluation and take its results over, as the frame loop does."""
    end = time.monotonic() + timeout
    while True:
        proj.take_rechecked_devices()
        with proj._recheck_lock:
            if (
                not proj._recheck_queue
                and not proj._rechecked
                and proj._recheck_generation == -1
            ):
                return
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


def _stored_fingerprints(proj: ProjectService) -> set[str]:
    pid = proj._pid
    assert pid is not None
    return {
        key.partition(".")[2] for key, _data in proj._svc.device_snapshots(pid).values()
    }


def test_opens_from_the_snapshot_and_keeps_the_device_when_it_is_unchanged(
    tmp_path: Any,
) -> None:
    cat, path, ids = _project(tmp_path)
    expected = _evaluated(cat, path)
    proj = _service(cat)
    proj.open(path)
    _from_other_code(proj)
    proj.close()

    proj = _service(cat)
    proj.open(path)
    opened = {d.node_id: d for d in proj.devices}
    assert sorted(opened) == sorted(ids)
    for node_id, device in opened.items():
        assert _state(device) == expected[node_id][0]
    _rechecked(proj)
    assert {d.node_id: d for d in proj.devices} == opened
    assert all(proj.devices[i] is opened[d.node_id] for i, d in enumerate(proj.devices))
    assert _stored_fingerprints(proj) == {code_fingerprint()}
    proj.close()


def test_a_device_that_comes_out_different_is_replaced(tmp_path: Any) -> None:
    cat, path, (a, _b) = _project(tmp_path)
    expected = _evaluated(cat, path)

    def fewer_visible(data: str) -> str:
        value = json.loads(data)
        value["visible"] = value["visible"][:-1]
        return json.dumps(value, separators=(",", ":"))

    proj = _service(cat)
    proj.open(path)
    _from_other_code(proj, fewer_visible)
    proj.close()

    proj = _service(cat)
    proj.open(path)
    opened = next(d for d in proj.devices if d.node_id == a)
    assert _state(opened) != expected[a][0]
    revision = proj.revision
    _rechecked(proj)
    replaced = next(d for d in proj.devices if d.node_id == a)
    assert replaced is not opened
    assert _state(replaced) == expected[a][0]
    assert proj.revision > revision
    assert _stored_fingerprints(proj) == {code_fingerprint()}
    proj.close()
    # Reopened, it comes from its new snapshot.
    proj = _service(cat)
    proj.open(path)
    device = next(d for d in proj.devices if d.node_id == a)
    assert device.app is not None and not device.app.loaded
    assert _state(device) == expected[a][0]
    with proj._recheck_lock:
        assert not proj._recheck_queue
    proj.close()


def test_the_selected_device_is_evaluated_first(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cat, path, (a, b) = _project(tmp_path)
    proj = _service(cat)
    proj.open(path)
    _from_other_code(proj)
    proj.close()

    order: list[int] = []
    compute = service_module._compute_device

    def recorded(inputs: Any, app: Any) -> Any:
        order.append(inputs.node_id)
        return compute(inputs, app)

    monkeypatch.setattr(service_module, "_compute_device", recorded)
    # The evaluation starts once the devices are built: select the last queued device by then.
    proj = _service(cat)
    start = ProjectService._recheck
    selected: list[int] = []

    def select_then_start(self: ProjectService) -> None:
        with self._recheck_lock:
            selected.append(list(self._recheck_queue)[-1])
        self._selected_node_id = selected[0]
        start(self)

    monkeypatch.setattr(ProjectService, "_recheck", select_then_start)
    proj.open(path)
    _ = proj.devices
    _rechecked(proj)
    assert sorted(order) == sorted([a, b])
    assert order[0] == selected[0]
    proj.close()


def test_a_device_changed_meanwhile_is_not_replaced(tmp_path: Any) -> None:
    cat, path, (a, _b) = _project(tmp_path)

    def fewer_visible(data: str) -> str:
        value = json.loads(data)
        value["visible"] = value["visible"][:-1]
        return json.dumps(value, separators=(",", ":"))

    proj = _service(cat)
    proj.open(path)
    _from_other_code(proj, fewer_visible)
    proj.close()

    proj = _service(cat)
    proj.open(path)
    opened = next(d for d in proj.devices if d.node_id == a)
    # As a parameter change rebuilding the device records what it was built from now.
    proj._build_keys[a] = ("changed",)
    _rechecked(proj)
    assert next(d for d in proj.devices if d.node_id == a) is opened
    proj.close()
