"""Choosing a group object's DPT, priority or description it has already is no edit: no undo
step, and the device can still be test-programmed (#308)."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.catalog.tests.test_delete_product import _project_with_device
from editor_gui.plugins.project.service import ProjectService


def _opened(tmp_path: Path) -> ProjectService:
    _, proj, _ = _project_with_device(tmp_path)
    path = tmp_path / "p.xknx"
    proj.close()
    proj.open(path)
    assert not proj.edited_since_open()
    return proj


def test_the_values_already_set_are_no_edit(tmp_path: Path) -> None:
    proj = _opened(tmp_path)
    (device,) = proj.devices
    co = next(c for c in device.com_objects if c.db_id is not None)
    steps = len(proj.history())

    proj.set_com_object_datapoint_type(device, co.id, None)
    proj.set_com_object_priority(device, co.id, None)
    proj.set_com_object_description(device, co.id, "")
    assert len(proj.history()) == steps
    assert not proj.edited_since_open()

    proj.set_com_object_datapoint_type(device, co.id, "1.001")
    proj.set_com_object_datapoint_type(device, co.id, "DPST-1-1")
    assert len(proj.history()) == steps + 1
    assert proj.edited_since_open()
    proj.close()


def test_the_group_address_type_already_set_is_no_edit(tmp_path: Path) -> None:
    proj = _opened(tmp_path)
    ga = proj.create_group_address("1/2/3", "x", dpt="1.001")
    assert ga is not None
    steps = len(proj.history())
    revision = proj.revision
    proj.set_group_address_dpt(ga, "DPST-1-1")
    assert len(proj.history()) == steps
    assert proj.revision == revision
    proj.close()


def test_a_plugin_object_keeps_its_value_without_an_edit(tmp_path, monkeypatch) -> None:
    from editor_gui.plugins.project.tests.test_powertool import current, native_project

    proj, node_id = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, node_id))
    co = current(proj, node_id).com_objects[1]
    proj.set_com_object_priority(current(proj, node_id), co.id, "High")
    proj.set_flag(current(proj, node_id), co.id, "read", False)
    steps = len(proj.history())
    proj.set_com_object_priority(current(proj, node_id), co.id, "High")
    proj.set_flag(current(proj, node_id), co.id, "read", False)
    assert len(proj.history()) == steps
    proj.set_com_object_priority(current(proj, node_id), co.id, "Low")
    assert len(proj.history()) == steps + 1
    proj.close()


@pytest.mark.parametrize("field", ["flag", "description"])
@pytest.mark.parametrize("native", [False, True])
def test_unchanged_values_keep_the_opened_state(tmp_path, monkeypatch, field, native):
    from editor_gui.plugins.project.tests.test_powertool import current, native_project

    if native:
        proj, node = native_project(tmp_path, monkeypatch)
        proj.setup_plugin(current(proj, node))
        path = tmp_path / "project.xknx"
    else:
        proj = _opened(tmp_path)
        node = proj.devices[0].node_id
        path = tmp_path / "p.xknx"
    ref = next(c.id for c in current(proj, node).com_objects if c.db_id is not None)
    setter = proj.set_flag if field == "flag" else proj.set_com_object_description
    args = ("read",) if field == "flag" else ()
    value = False if field == "flag" else "Note"
    changed = True if field == "flag" else "Changed"
    setter(current(proj, node), ref, *args, value)
    proj.record_download(node, "full")
    proj.close()
    proj.open(path)
    try:
        device = current(proj, node)
        history, revision = proj.history(), proj.revision
        pending = proj.pending_downloads()
        blobs = [(b.name, b.data) for b in device.binary_data]
        setter(device, ref, *args, value)
        assert (proj.history(), proj.revision) == (history, revision)
        assert not proj.edited_since_open()
        assert not device.edited_in_place
        assert proj.pending_downloads() == pending
        assert [(b.name, b.data) for b in current(proj, node).binary_data] == blobs
        setter(current(proj, node), ref, *args, changed)
        assert proj.edited_since_open()
        proj.undo()
        history, revision = proj.history(), proj.revision
        setter(current(proj, node), ref, *args, value)
        assert (proj.history(), proj.revision) == (history, revision)
        assert not proj.edited_since_open()
        assert proj.redo()
        assert proj.edited_since_open()
    finally:
        proj.close()
