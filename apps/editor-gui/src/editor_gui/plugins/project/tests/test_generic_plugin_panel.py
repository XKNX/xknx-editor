"""Plugin configuration comparison, filtering and rendering with synthetic values."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.local_time import stamp_text
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import configure as configure_module
from editor_gui.plugins.project.ui import generic_plugin_panel as panel_module
from editor_gui.plugins.project.ui.configure import ConfigurePanel
from editor_gui.plugins.project.ui.generic_plugin_panel import (
    GenericPluginPanel,
    filter_compare_rows,
    filter_rows,
    memory_text,
    page_title,
    summary_lines,
    value_text,
)
from xknxeditor.vendor.siemens_generic import (
    ComparePage,
    CompareRow,
    GenericView,
    MemoryCheck,
    Page,
    Row,
)


def _row(ref_id: str, memory: MemoryCheck, **kw: Any) -> Row:
    fields: dict[str, Any] = {
        "ref_id": ref_id,
        "parameter": ref_id,
        "label": f"Label {ref_id}",
        "value": "dm_Yes",
        "value_text": "Yes",
        "default_value": "dm_No",
        "stored": True,
        "assigned": False,
        "memory": memory,
    } | kw
    return Row(**fields)


def _view(**kw: Any) -> GenericView:
    rows = [
        _row("a", MemoryCheck.MATCH),
        _row(
            "b",
            MemoryCheck.MISMATCH,
            value="600",
            value_text="600",
            memory_value="300",
            memory_value_text="300",
        ),
        _row("s", MemoryCheck.NOT_IN_MEMORY, label="Timing", value="", separator=True),
        _row("c", MemoryCheck.UNCHECKED, stored=False, value_text="Mode C"),
    ]
    fields: dict[str, Any] = {
        "application_id": "id-DEMO",
        "application_matches": True,
        "stored_application_id": "id-DEMO",
        "pages": [
            Page(id="b1", title="Channel 1", channel=1, rows=rows),
            Page(id="", title="", channel=0, rows=[_row("d", MemoryCheck.MATCH)]),
        ],
        "has_image": True,
        "image_layout_known": True,
        "modified": "",
        "last_download": "2025-01-02T03:04:05.678",
    } | kw
    return GenericView(**fields)


def test_column_texts() -> None:
    match, mismatch, separator, unchecked = _view().pages[0].rows
    assert memory_text(match) == S.GENERIC_PLUGIN_MATCH
    assert memory_text(mismatch) == S.GENERIC_PLUGIN_MISMATCH.format(value="300")
    assert memory_text(separator) == S.GENERIC_PLUGIN_NOT_IN_MEMORY
    assert memory_text(unchecked) == S.GENERIC_PLUGIN_UNCHECKED
    enum = _row("e", MemoryCheck.MISMATCH, memory_value="dm_No", memory_value_text="No")
    assert memory_text(enum) == S.GENERIC_PLUGIN_MISMATCH.format(value="No (dm_No)")
    assert value_text(match) == "Yes (dm_Yes)"
    assert value_text(mismatch) == "600"
    assert page_title(_view().pages[1]) == S.GENERIC_PLUGIN_GENERAL


def test_summary_lines() -> None:
    lines = summary_lines(_view())
    assert lines[0] == (
        "warning",
        S.GENERIC_PLUGIN_SUMMARY.format(match=2, mismatch=1, unchecked=1),
    )
    assert lines[1] == (
        "info",
        S.GENERIC_PLUGIN_LAST_DOWNLOAD.format(date=stamp_text(_view().last_download)),
    )
    other = summary_lines(
        _view(
            application_matches=False,
            stored_application_id="X",
            has_image=False,
            last_download="",
        )
    )
    assert other == [
        ("warning", S.GENERIC_PLUGIN_OTHER_APPLICATION.format(stored="X")),
        ("info", S.GENERIC_PLUGIN_NO_IMAGE),
    ]
    assert summary_lines(_view(image_layout_known=False, last_download=""))[0] == (
        "warning",
        S.GENERIC_PLUGIN_LAYOUT_UNKNOWN,
    )


def test_filter_rows() -> None:
    page = _view().pages[0]
    assert len(filter_rows(page, "", False)) == 4
    assert [r.ref_id for r in filter_rows(page, "", True)] == ["b"]
    assert [r.ref_id for r in filter_rows(page, "mode c", False)] == ["c"]
    assert len(filter_rows(page, "channel", False)) == 4  # the page title matches
    assert filter_rows(page, "nothing", False) == []


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frames(panel: GenericPluginPanel, device: Any, count: int = 2) -> None:
    for _ in range(count):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        imgui.begin("Generic")
        panel.render(device)
        imgui.end()
        imgui.end_frame()


def test_render_headless(context: None, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(
        panel_module.imgui, "text_disabled", lambda text: shown.append(text)
    )
    device = cast("Any", SimpleNamespace(individual_address="1.1.1", node_id=1))
    empty = GenericPluginPanel(lambda _d, **_k: None)
    _frames(empty, device, 1)
    assert S.GENERIC_PLUGIN_NONE in shown
    panel = GenericPluginPanel(lambda _d, **_k: _view())
    _frames(panel, device)
    panel._only_mismatches = True
    _frames(panel, device)
    panel._filter = "no such setting"
    shown.clear()
    _frames(panel, device, 1)
    assert S.PLUGIN_PARAMS_FILTER_EMPTY in shown


def test_read_memory_is_inside_comparison(context, monkeypatch):
    from concurrent.futures import Future

    from editor_gui.plugins.project.program_queue import QueueItem

    device = cast(Any, SimpleNamespace(node_id=1, individual_address="1.1.1"))
    opened = []
    hints = []
    item = QueueItem(1, "1.1.1", "", None, ended=Future())
    panel = GenericPluginPanel(
        lambda *_a, **_kw: _view(),
        _read_memory=lambda d: opened.append(d) or item,
        _memory_operation=lambda _d: item if opened else None,
    )
    monkeypatch.setattr(imgui, "button", lambda label, *_args: label == S.MEMORY_READ)
    monkeypatch.setattr(imgui, "text_wrapped", hints.append)
    expanded = [False]
    monkeypatch.setattr(panel_module, "collapsing_header", lambda *_a: expanded[0])
    for state in (False, True, True):
        expanded[0] = state
        imgui.new_frame()
        imgui.begin("Plugin Config")
        panel.render_section(device)
        imgui.end()
        imgui.end_frame()
        assert opened == ([device] if state else [])
    assert S.MEMORY_COMPARE_HINT in hints


def test_memory_read_error_does_not_follow_device(context, monkeypatch):
    errors = []

    def refuse(_device):
        raise ValueError("Unreadable memory layout")

    panel = GenericPluginPanel(lambda *_a, **_kw: _view(), _read_memory=refuse)
    monkeypatch.setattr(imgui, "button", lambda label, *_args: label == S.MEMORY_READ)
    monkeypatch.setattr(imgui, "text_colored", lambda _color, text: errors.append(text))
    first = cast(Any, SimpleNamespace(node_id=1, individual_address="1.1.1"))
    _frames(panel, first, 1)
    assert "Unreadable memory layout" in errors
    errors.clear()
    monkeypatch.setattr(imgui, "button", lambda *_args: False)
    second = cast(Any, SimpleNamespace(node_id=2, individual_address="1.1.2"))
    _frames(panel, second, 1)
    assert "Unreadable memory layout" not in errors


def test_scaled_and_unfitted_texts() -> None:
    pct = _row("p", MemoryCheck.MATCH, value="102", value_text="40 %", scaled=True)
    assert value_text(pct) == S.GENERIC_PLUGIN_SCALED.format(value="40 %", raw="102")
    held = _row(
        "q",
        MemoryCheck.MISMATCH,
        value="102",
        value_text="40 %",
        memory_value="51",
        memory_value_text="20 %",
        scaled=True,
    )
    assert memory_text(held) == S.GENERIC_PLUGIN_MISMATCH.format(
        value=S.GENERIC_PLUGIN_SCALED.format(value="20 %", raw="51")
    )
    module = _row("m", MemoryCheck.UNCHECKED, fitted=False)
    assert memory_text(module) == S.GENERIC_PLUGIN_NOT_FITTED
    assert value_text(None) == S.GENERIC_PLUGIN_NOT_SHOWN
    page = Page(id="b9", title="Module 2", channel=3, fitted=False)
    assert page_title(page) == S.GENERIC_PLUGIN_UNFITTED_PAGE.format(title="Module 2")


def test_summary_counts_unfitted_settings() -> None:
    module = Page(
        id="m",
        title="Module",
        channel=2,
        fitted=False,
        rows=[_row("m1", MemoryCheck.UNCHECKED, fitted=False)],
    )
    view = _view(show_unfitted=True)
    view.pages.append(module)
    lines = summary_lines(view)
    # The module's setting is not counted as "not checked".
    assert lines[0] == (
        "warning",
        S.GENERIC_PLUGIN_SUMMARY.format(match=2, mismatch=1, unchecked=1),
    )
    assert ("info", S.GENERIC_PLUGIN_UNFITTED_COUNT.format(count=1)) in lines


def test_filter_compare_rows() -> None:
    same = CompareRow(_row("a", MemoryCheck.MATCH), _row("a", MemoryCheck.MATCH))
    other = CompareRow(
        _row("b", MemoryCheck.MATCH),
        _row("b", MemoryCheck.MISMATCH, value="dm_No", value_text="No"),
    )
    missing = CompareRow(None, _row("c", MemoryCheck.MATCH, value_text="Mode C"))
    page = ComparePage("p", "Channel 1", 1, True, [same, other, missing])
    assert len(filter_compare_rows(page, "", False, False)) == 3
    assert filter_compare_rows(page, "", False, True) == [other, missing]
    assert filter_compare_rows(page, "", True, False) == [other]
    assert filter_compare_rows(page, "mode c", False, False) == [missing]
    assert len(filter_compare_rows(page, "channel", False, False)) == 3


def test_render_comparison_headless(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = cast("Any", SimpleNamespace(individual_address="1.1.1", node_id=1))
    twin = cast(
        "Any",
        SimpleNamespace(
            individual_address="1.1.2", node_id=2, display_name="Twin", name="Twin"
        ),
    )
    second = _view()
    second.pages[0].rows[0] = _row(
        "a", MemoryCheck.MATCH, value="dm_No", value_text="No"
    )
    asked: list[bool] = []

    def view_for(d: Any, show_unfitted: bool = False) -> GenericView:
        asked.append(show_unfitted)
        return second if d is twin else _view()

    colored: list[str] = []
    monkeypatch.setattr(
        panel_module.imgui, "text_colored", lambda _c, text: colored.append(text)
    )
    panel = GenericPluginPanel(view_for, lambda _d: [twin])
    _frames(panel, device)
    assert not any("1.1.2" in t for t in colored)  # nothing chosen yet
    panel._compare[1] = 2
    panel._show_unfitted = True
    _frames(panel, device)
    assert (
        S.GENERIC_PLUGIN_COMPARE_DIFFERENCES.format(device="1.1.2  Twin", count=1)
        in colored
    )
    assert asked[-1] is True
    panel._only_differences = True
    _frames(panel, device)
    no_twins = GenericPluginPanel(view_for, lambda _d: [])
    _frames(no_twins, device, 1)


def test_unassigned_devices_keep_independent_comparisons(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = SimpleNamespace(node_id=1, individual_address="", display_name="First")
    second = SimpleNamespace(node_id=2, individual_address="", display_name="Second")
    other = SimpleNamespace(node_id=3, individual_address="", display_name="Other")
    devices = [first, second, other]
    panel = GenericPluginPanel(
        lambda _d, **_k: _view(),
        lambda device: cast("Any", [d for d in devices if d is not device]),
    )
    panel._compare[first.node_id] = other.node_id
    shown: list[str] = []
    monkeypatch.setattr(
        panel_module.imgui, "text_colored", lambda _c, text: shown.append(text)
    )
    _frames(panel, first)
    assert S.GENERIC_PLUGIN_COMPARE_DIFFERENCES.format(device="Other", count=0) in shown
    shown.clear()
    _frames(panel, second)
    assert (
        S.GENERIC_PLUGIN_COMPARE_DIFFERENCES.format(device="Other", count=0)
        not in shown
    )


def test_clock_texts() -> None:
    timed = _row(
        "t",
        MemoryCheck.MISMATCH,
        value="900",
        value_text="15:00 (900 s)",
        memory_value="300",
        memory_value_text="05:00 (300 s)",
        clock=True,
    )
    assert value_text(timed) == "15:00 (900 s)"
    assert memory_text(timed) == S.GENERIC_PLUGIN_MISMATCH.format(value="05:00 (300 s)")


@pytest.mark.parametrize(
    ("mapped", "comparable", "visible"),
    [
        (True, True, True),
        (True, False, True),
        (False, True, True),
        (False, False, False),
    ],
)
def test_single_plugin_tab_includes_comparison_only_devices(
    monkeypatch: pytest.MonkeyPatch, mapped: bool, comparable: bool, visible: bool
) -> None:
    device = SimpleNamespace(
        app=object(), get_ui=lambda: [], get_visible_com_objects=lambda: []
    )
    rendered: list[object] = []
    tabs: list[str] = []

    def tab(label: str) -> tuple[bool, bool]:
        tabs.append(label)
        return label.endswith("###editor_tab_plugin_params"), False

    monkeypatch.setattr(configure_module, "begin_tab_bar", lambda _id: True)
    monkeypatch.setattr(configure_module.imgui, "begin_tab_item", tab)
    for name in ("separator", "end_tab_item", "end_tab_bar"):
        monkeypatch.setattr(configure_module.imgui, name, lambda: None)
    monkeypatch.setattr(
        configure_module,
        "mapping_for_device",
        lambda _device: object() if mapped else None,
    )
    panel = SimpleNamespace(
        _current_device=lambda: device,
        _begin_details=lambda: None,
        _render_title_row=lambda _device: None,
        _ready=lambda _device: True,
        _ui_derived_tables=lambda _ui: (0, []),
        _render_dali=None,
        _render_siemens_dali=None,
        _render_plugin_params=rendered.append,
        _has_plugin_params=lambda _device: mapped or comparable,
    )
    ConfigurePanel.render(cast("Any", panel))
    assert rendered == ([device] if visible else [])
    assert len(tabs) == (3 if visible else 2)
    assert not any("editor_tab_generic_plugin" in label for label in tabs)


def test_comparison_section_is_optional_and_does_not_hide_editor(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui.plugins.project import plugin as plugin_module

    device = SimpleNamespace(node_id=1)
    events: list[str] = []
    comparison = GenericPluginPanel(lambda _device, **_kw: _view())
    project = SimpleNamespace(plugin_mapping=lambda _device: object())
    plugin = SimpleNamespace(
        _poll_triton_prj_dialog=lambda: None,
        _poll_triton_prj_save=lambda: None,
        _api=SimpleNamespace(project=project, connection=SimpleNamespace(master=None)),
        _native_plugin_panel=SimpleNamespace(
            render=lambda *_args: events.append("actions")
        ),
        _generic_plugin_panel=comparison,
        _plugin_params_panel=SimpleNamespace(
            render=lambda _device: events.append("edit")
        ),
    )
    monkeypatch.setattr(
        GenericPluginPanel, "render", lambda *_args: events.append("compare")
    )

    def frame() -> None:
        imgui.new_frame()
        imgui.begin("Unified Plugin Config")
        plugin_module.ProjectPlugin._render_plugin_params_tab(
            cast("Any", plugin), cast("Any", device)
        )
        imgui.end()
        imgui.end_frame()

    frame()
    assert events == ["actions", "edit"]
    events.clear()
    collapse = panel_module.collapsing_header

    def expanded(label: str) -> bool:
        imgui.set_next_item_open(True)
        return collapse(label)

    monkeypatch.setattr(panel_module, "collapsing_header", expanded)
    frame()
    assert events == ["actions", "compare", "edit"]
    events.clear()
    monkeypatch.setattr(project, "plugin_mapping", lambda _device: None)
    frame()
    assert events == ["actions", "compare"]


def test_comparison_is_reused_until_a_view_changes(monkeypatch):
    compared = []
    compare = panel_module.compare_views
    monkeypatch.setattr(
        panel_module,
        "compare_views",
        lambda first, second: (
            compared.append((first, second)) or compare(first, second)
        ),
    )
    first, second = _view(), _view()
    panel = GenericPluginPanel(lambda _device: first)
    initial = panel._compare_views(first, second)
    assert panel._compare_views(first, second) is initial
    updated = _view()
    updated.pages[0].rows[0] = _row("a", MemoryCheck.MATCH, value="dm_No")
    assert panel._compare_views(first, updated).differences == 1
    assert len(compared) == 2


def test_selection_survives_readdressing_but_not_project_switch(context):
    generation = [1]
    first = SimpleNamespace(node_id=1, individual_address="1.1.1", display_name="First")
    second = SimpleNamespace(
        node_id=2, individual_address="1.1.2", display_name="Second"
    )
    panel = GenericPluginPanel(
        lambda _d, **_k: _view(),
        lambda _d: [second],
        _generation=lambda: generation[0],
    )
    _frames(panel, first, 1)
    panel._compare[1] = 2
    panel._closed[1] = {"b1"}
    second.individual_address = "1.2.20"
    first.individual_address = "1.2.21"
    _frames(panel, first, 1)
    assert panel._compare == {1: 2}
    assert panel._closed[1] == {"b1"}
    generation[0] += 1
    _frames(panel, first, 1)
    assert panel._compare == {}
    assert not panel._closed.get(1)


def test_comparison_filters_use_shared_checkboxes(context, monkeypatch):
    seen = []
    checkbox = panel_module.checkbox
    monkeypatch.setattr(
        panel_module,
        "checkbox",
        lambda label, value: seen.append(label) or checkbox(label, value),
    )
    first = SimpleNamespace(node_id=1, individual_address="1.1.1", display_name="First")
    second = SimpleNamespace(
        node_id=2, individual_address="1.1.2", display_name="Second"
    )
    panel = GenericPluginPanel(lambda _d, **_kw: _view(), lambda _d: [second])
    panel._compare[1] = 2
    _frames(panel, first, 1)
    assert seen == [
        S.GENERIC_PLUGIN_ONLY_MISMATCHES,
        S.GENERIC_PLUGIN_SHOW_UNFITTED,
        S.GENERIC_PLUGIN_ONLY_DIFFERENCES,
    ]


def test_native_import_error_stays_with_its_device(context, tmp_path, monkeypatch):
    from editor_gui.plugin_params import PluginMapping
    from editor_gui.plugin_providers import PluginCapabilities
    from editor_gui.plugins.project.ui.native_plugin import NativePluginPanel

    first = SimpleNamespace(node_id=1, native_error=None, last_lost_links=[])
    second = SimpleNamespace(node_id=2, native_error=None, last_lost_links=[])
    imported = []
    shown = []
    path = tmp_path / "configuration.json"
    path.write_bytes(b"invalid")

    def import_config(device, data):
        imported.append((device.node_id, data))
        raise ValueError("Invalid configuration")

    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _: first,
        import_plugin_config=import_config,
        plugin_mapping=lambda _: PluginMapping(
            (), "PowerTool", (), provider="powertool"
        ),
        plugin_capabilities=lambda _: PluginCapabilities(True, True, True, None),
    )
    monkeypatch.setattr(imgui, "text_colored", lambda _color, text: shown.append(text))
    panel = NativePluginPanel()

    def render(device):
        imgui.new_frame()
        imgui.begin("Native")
        try:
            panel.render(device, project)
        finally:
            imgui.end()
            imgui.end_frame()

    panel._target = (1, 1)
    dialog = SimpleNamespace(ready=lambda: True, result=lambda: [str(path)])
    panel._open = dialog
    render(second)
    assert imported == [(1, b"invalid")] and not shown
    render(first)
    assert shown == ["Invalid configuration"]
    shown.clear()
    project.generation = 2
    panel._open = dialog
    render(first)
    assert not shown and imported == [(1, b"invalid")]


def test_orphaned_native_configuration_still_shows_error(context, monkeypatch):
    from editor_gui.plugins.project.ui.native_plugin import NativePluginPanel

    shown = []

    def capabilities(_):
        raise ValueError("Native configuration has no matching product provider")

    project = SimpleNamespace(
        generation=1,
        plugin_mapping=lambda _: None,
        plugin_capabilities=capabilities,
    )
    monkeypatch.setattr(imgui, "text_colored", lambda _color, text: shown.append(text))
    imgui.new_frame()
    imgui.begin("Native")
    try:
        NativePluginPanel().render(SimpleNamespace(node_id=1), project)
    finally:
        imgui.end()
        imgui.end_frame()
    assert shown == ["Native configuration has no matching product provider"]
