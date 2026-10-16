"""Tests for multi-device parameter editing: the pure ``differing_param_refs`` diff, the
Configure panel's same-app joint selection + cached differing set, and the plugin's edit dispatch.

The Configure logic lives on ``ConfigurePanel`` but the parts under test only touch injected
callbacks; we build the panel via ``__new__`` (skipping the heavy imgui wiring) and set the few
attributes those methods read.
"""

from __future__ import annotations

from types import SimpleNamespace

from editor_gui.plugins.project.ui.configure import ConfigurePanel
from editor_gui.widgets.parameter_widgets import differing_param_refs
from xknxeditor.prod.parser_v2.ui import UiParameter, UiParameterBlock, UiTab
from xknxeditor.prod.parser_v2.ui.parameter import TextWidget


def _param(ref_id: str, value: str) -> UiParameter:
    return UiParameter(ref_id=ref_id, label=ref_id, value=value, widget=TextWidget())


def _device(node_id: int, app_id: str, params: dict[str, str], counter=None):
    ui = [_param(rid, val) for rid, val in params.items()]

    def get_ui():
        if counter is not None:
            counter.append(node_id)
        return ui

    return SimpleNamespace(
        node_id=node_id, app=SimpleNamespace(id=app_id), get_ui=get_ui
    )


# --- differing_param_refs (pure) ------------------------------------------


def test_differing_none_when_all_equal():
    a = _device(1, "APP", {"p1": "0", "p2": "5"})
    b = _device(2, "APP", {"p1": "0", "p2": "5"})
    assert differing_param_refs([a, b]) == frozenset()


def test_differing_flags_only_diverging_refs():
    a = _device(1, "APP", {"p1": "0", "p2": "5"})
    b = _device(2, "APP", {"p1": "1", "p2": "5"})
    assert differing_param_refs([a, b]) == frozenset({"p1"})


def test_differing_finds_nested_params():
    tree_a = [
        UiTab(
            id="t",
            name="t",
            text="t",
            children=(
                UiParameterBlock(
                    id="b", name="b", text="b", children=(_param("n1", "0"),)
                ),
            ),
        )
    ]
    tree_b = [
        UiTab(
            id="t",
            name="t",
            text="t",
            children=(
                UiParameterBlock(
                    id="b", name="b", text="b", children=(_param("n1", "9"),)
                ),
            ),
        )
    ]
    a = SimpleNamespace(node_id=1, app=SimpleNamespace(id="A"), get_ui=lambda: tree_a)
    b = SimpleNamespace(node_id=2, app=SimpleNamespace(id="A"), get_ui=lambda: tree_b)
    assert differing_param_refs([a, b]) == frozenset({"n1"})


def test_differing_single_device_is_empty():
    a = _device(1, "APP", {"p1": "0"})
    assert differing_param_refs([a]) == frozenset()


# --- ConfigurePanel joint selection + cache -------------------------------


def _panel(devices, selected_ids):
    p = ConfigurePanel.__new__(ConfigurePanel)
    p._get_devices = lambda: devices
    p._get_selected_node_ids = lambda: set(selected_ids)
    p._on_param_change_selected = lambda *a: None
    p._diff_refs = frozenset()
    p._diff_selection = frozenset()
    p._holding = False
    return p


def test_joint_devices_filters_by_app_and_includes_primary():
    a = _device(1, "APP", {"p": "0"})
    b = _device(2, "APP", {"p": "1"})
    other = _device(3, "OTHER", {"p": "0"})
    panel = _panel([a, b, other], {1, 2, 3})
    joint = panel._joint_devices(a)
    assert {d.node_id for d in joint} == {1, 2}  # OTHER app ignored


def test_joint_devices_single_selection_is_primary_only():
    a = _device(1, "APP", {"p": "0"})
    panel = _panel([a], {1})
    assert panel._joint_devices(a) == [a]


def test_differing_refs_cached_until_selection_changes():
    calls: list[int] = []
    a = _device(1, "APP", {"p": "0"}, counter=calls)
    b = _device(2, "APP", {"p": "1"}, counter=calls)
    panel = _panel([a, b], {1, 2})
    joint = panel._joint_devices(a)
    first = panel._differing_refs(joint)
    assert first == frozenset({"p"})
    n = len(calls)
    panel._differing_refs(joint)  # same selection -> no recompute
    assert len(calls) == n
