"""The read-only Generic Plug-in tab: its texts, filter and a headless render (made-up values)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugin_blob_readers.siemens_generic import (
    ComparePage,
    CompareRow,
    GenericView,
    MemoryCheck,
    Page,
    Row,
)
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import generic_plugin_panel as panel_module
from editor_gui.plugins.project.ui.generic_plugin_panel import (
    GenericPluginPanel,
    filter_compare_rows,
    filter_rows,
    memory_text,
    page_title,
    summary_lines,
    value_text,
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
        S.GENERIC_PLUGIN_LAST_DOWNLOAD.format(date="2025-01-02 03:04:05"),
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
    device = cast("Any", SimpleNamespace(individual_address="1.1.1"))
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
    panel._compare["1.1.1"] = 2
    panel._show_unfitted = True
    _frames(panel, device)
    assert (
        S.GENERIC_PLUGIN_COMPARE_DIFFERENCES.format(device="1.1.2", count=1) in colored
    )
    assert asked[-1] is True
    panel._only_differences = True
    _frames(panel, device)
    no_twins = GenericPluginPanel(view_for, lambda _d: [])
    _frames(no_twins, device, 1)


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
