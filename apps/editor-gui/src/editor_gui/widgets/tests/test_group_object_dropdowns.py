"""Reselecting an override does not dispatch an edit."""

import pytest

from editor_gui.device import ComObject, ComObjectFlags, Device
from editor_gui.dpt import DPT_UNKNOWN, dpt_from_token
from editor_gui.widgets import group_objects_widgets as widgets
from xknxeditor.namespaces.intermediate import ComObjectInstanceRef, ComObjectPriority


@pytest.mark.parametrize(
    ("field", "override", "clicked", "expected"),
    [
        ("dpt", None, 0, []),
        ("dpt", "DPST-1-1", 1, []),
        ("dpt", None, 1, ["DPST-1-1"]),
        ("dpt", "DPST-1-1", 0, [None]),
        ("priority", None, 0, []),
        ("priority", "Low", 1, []),
        ("priority", None, 1, ["Low"]),
        ("priority", "Low", 0, [None]),
    ],
)
def test_only_a_different_override_dispatches(
    monkeypatch, field, override, clicked, expected
):
    device = Device(node_id=1, name="Button", app=None, individual_address="1.1.1")
    dpt = dpt_from_token("DPST-1-1")
    co = ComObject(
        id="o",
        name="Switch",
        dpt=dpt,
        flags=ComObjectFlags(),
        supported_dpts=[DPT_UNKNOWN, dpt],
        priority="Low",
    )
    if field == "dpt":
        co.datapoint_type_override = override
    elif override:
        device.com_object_instance_refs = [
            ComObjectInstanceRef(ref_id=co.id, priority=ComObjectPriority(override))
        ]
    calls = []
    table = widgets.GroupObjectsTable(
        lambda *_: None,
        lambda *_: None,
        lambda *_: None,
        on_set_dpt=lambda _d, _c, v: calls.append(v),
        on_set_priority=lambda _d, _c, v: calls.append(v),
    )
    selected = []

    def choose(_label, is_selected):
        index = len(selected)
        selected.append(is_selected)
        return index == clicked, is_selected

    monkeypatch.setattr(widgets.imgui, "set_next_item_width", lambda *_: None)
    monkeypatch.setattr(widgets.imgui, "selectable", choose)
    monkeypatch.setattr(widgets, "begin_combo", lambda *_: True)
    monkeypatch.setattr(widgets, "end_combo", lambda: None)
    getattr(table, f"_render_{field}_combo")(device, co)
    assert calls == expected
    assert selected[0] == (override is None)
    assert sum(selected) == 1
    if field == "dpt":
        assert len(selected) == 2


def test_main_type_is_not_changed_to_subtype_zero(monkeypatch):
    device = Device(node_id=1, name="Text", app=None, individual_address="1.1.1")
    main, subtype = [dpt_from_token(t) for t in ("DPT-16", "DPST-16-0")]
    co = ComObject(
        id="o",
        name="Text",
        dpt=main,
        flags=ComObjectFlags(),
        supported_dpts=[main, subtype],
        datapoint_type_override="DPT-16",
    )
    calls = []
    table = widgets.GroupObjectsTable(
        lambda *_: None,
        lambda *_: None,
        lambda *_: None,
        on_set_dpt=lambda _d, _c, v: calls.append(v),
    )
    monkeypatch.setattr(widgets.imgui, "set_next_item_width", lambda *_: None)
    selected = []

    def choose(_label, is_selected):
        selected.append(is_selected)
        return len(selected) == 2, is_selected

    monkeypatch.setattr(widgets.imgui, "selectable", choose)
    monkeypatch.setattr(widgets, "begin_combo", lambda *_: True)
    monkeypatch.setattr(widgets, "end_combo", lambda: None)
    table._render_dpt_combo(device, co)
    assert selected == [False, True, False]
    assert calls == []
    co.datapoint_type_override = "DPST-16-0"
    co.dpt = subtype
    selected.clear()
    table._render_dpt_combo(device, co)
    assert selected == [False, False, True]
    assert calls == ["DPT-16"]
