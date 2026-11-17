"""Read-only pre-flight self-test gate: what counts as "edited since open".

Regression: after programming a device, the app records its commissioning state
(``SetDeviceCommissioning``: last-download timestamp + loaded ticks) into the same event store as
real edits. That bookkeeping never changes the generated image, so it must NOT flip
``edited_since_open()`` and lock the user out of the read-only self-test on a device they only
(re-)programmed. A genuine configuration edit must still count.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService


def _project(tmp_path: Path) -> ProjectService:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    return proj


def test_commissioning_record_does_not_count_as_edit(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    p = tmp_path / "p.xknx"
    proj.new(p)
    proj.close()
    proj.open(p)  # baseline captured here, the way the app does on open
    assert not proj.edited_since_open()

    # Recording commissioning after a program (device id need not exist: apply is a no-op, the
    # event is still appended to history) must leave the project "unedited" for the gate.
    proj.set_device_commissioning(
        999, last_download="2026-01-01T00:00:00.0Z", parameters_loaded=True
    )
    assert not proj.edited_since_open()

    # A real configuration edit still trips the gate.
    proj.create_group_address(address="1/2/3", name="x")
    assert proj.edited_since_open()


@pytest.mark.parametrize("field", ["datapoint_type", "priority"])
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_reselecting_object_override_keeps_test_programming_available(
    tmp_path, monkeypatch, field, explicit, native
):
    from .test_device_resolution import _open, _project_with_device
    from .test_powertool import current, native_project

    if native:
        proj, node = native_project(tmp_path, monkeypatch, variant="02")
        proj.setup_plugin(current(proj, node))
        path = tmp_path / "project.xknx"
    else:
        path, node, _ = _project_with_device(tmp_path)
        proj = _open(CatalogService(tmp_path / "c.xknxcatalog"), path)
    setter = getattr(proj, f"set_com_object_{field}")
    value = "1.001" if field == "datapoint_type" else "High"
    device = current(proj, node)
    co = next(c for c in device.get_visible_com_objects() if c.db_id is not None)
    initial = "DPST-1-1" if field == "datapoint_type" else value
    setter(device, co.id, initial if explicit else None)
    if explicit and field == "datapoint_type":
        assert device.find_com_object(co.id).dpt.token == initial
    proj.record_download(node, "full")
    proj.close()
    proj.open(path)
    try:
        device = current(proj, node)
        before = proj.history()
        revision = proj.revision
        pending = proj.pending_downloads()
        blobs = [(b.name, b.data) for b in device.binary_data]
        assert not proj.edited_since_open()
        setter(device, co.id, value if explicit else None)
        assert proj.history() == before
        assert proj.revision == revision
        assert proj.pending_downloads() == pending
        assert not proj.edited_since_open()
        assert not device.edited_in_place
        assert [(b.name, b.data) for b in current(proj, node).binary_data] == blobs

        setter(device, co.id, None if explicit else value)
        assert proj.edited_since_open()
        assert len(proj.history()) == len(before) + 1
        proj.undo()
        assert not proj.edited_since_open()
        setter(current(proj, node), co.id, value if explicit else None)
        assert not proj.edited_since_open()
        assert proj.redo()
        assert proj.edited_since_open()
    finally:
        proj.close()
