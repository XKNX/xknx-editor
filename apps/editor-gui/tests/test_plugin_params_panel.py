"""Tests for the plugin-parameter panel's display helpers and grouped (tree) rendering.

Points from issue #199: the "Vorgabe" (default) column must show an enum's label, not its raw
number; a long BLOB section must nest under a collapsible tree node per item instead of one flat
table.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import ClassVar

import editor_gui.plugins.project.ui.plugin_params_panel as ppp
import pytest
from editor_gui.plugin_params import PluginParameter, PluginSectionView
from editor_gui.plugins.project.ui.plugin_params_panel import (
    PluginParamsPanel,
    _default_label,
    _display_type_index,
    _filter_rows,
    _SectionRow,
)
from imgui_bundle import imgui


def _row(**kw: object) -> _SectionRow:
    base: dict[str, object] = {
        "ref_id": "r",
        "label": "L",
        "value": "v",
        "default": "",
        "writable": True,
        "choices": (),
        "group": "",
    }
    base.update(kw)
    return _SectionRow(**base)  # type: ignore[arg-type]


def test_default_label_resolves_enum_token_to_label() -> None:
    row = _row(default="3", choices=(("0", "Off"), ("3", "RGBW Colour")))
    assert _default_label(row) == "RGBW Colour"


def test_default_label_falls_back_for_non_enum_and_empty() -> None:
    assert _default_label(_row(default="42", choices=())) == "42"
    assert _default_label(_row(default="", choices=())) == "-"
    # A default not present in the choice set is shown verbatim, not swallowed.
    assert _default_label(_row(default="9", choices=(("0", "Off"),))) == "9"


def test_filter_rows_carries_group_and_matches_on_it() -> None:
    section = PluginSectionView(
        section="DALI Scenes",
        parameters=[
            PluginParameter(
                ref_id="a",
                name="a",
                label="Value",
                value="20",
                default_value="",
                section="",
                display_order=0,
                writable=True,
                group="[2] Group 'Sauna'",
            ),
            PluginParameter(
                ref_id="b",
                name="b",
                label="Value",
                value="30",
                default_value="",
                section="",
                display_order=0,
                writable=True,
                group="[3] Group 'Voute'",
            ),
        ],
    )
    # The group survives into the render row.
    assert [r.group for r in _filter_rows(section, "")] == [
        "[2] Group 'Sauna'",
        "[3] Group 'Voute'",
    ]
    # Searching a group name keeps only that group's rows (the field label alone never matches).
    matched = _filter_rows(section, "sauna")
    assert [r.group for r in matched] == ["[2] Group 'Sauna'"]


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1024, 768)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _StubDevice:
    individual_address = "1.1.5"


def _render(
    panel: PluginParamsPanel,
    rows: list[_SectionRow],
    *,
    open_first: bool = False,
    filtering: bool = False,
) -> None:
    device = _StubDevice()
    for _ in range(2):  # two frames: tree-node open state settles across frames
        imgui.new_frame()
        if imgui.begin("w"):
            if open_first:
                # Force the first tree node open so the child-row render path (the editable
                # widgets nested inside a tree inside a table) is actually exercised.
                imgui.set_next_item_open(True)
            panel._render_standard_table(  # type: ignore[arg-type]
                device, "DALI Scenes", rows, filtering=filtering
            )
        imgui.end()
        imgui.end_frame()


def test_grouped_rows_render_without_error(context: None) -> None:
    # A grouped section (every row carries a group) must render through the tree path; a plain
    # section (no group) through the clipped flat table. Both must not raise against the real
    # imgui table / tree API. The rows include an editable combo and an editable scalar so the
    # widget branches of _render_row run too.
    panel = PluginParamsPanel(_on_edit=lambda *_: None)
    grouped = [
        _row(
            ref_id=f"dali_backup:b:scenes:0:leaf:{i}:value",
            label="Value",
            choices=(("0", "Off"), ("1", "On")),
            group="[0] Scene",
            group_key="scenes:0",
        )
        for i in range(3)
    ] + [
        _row(
            ref_id=f"dali_backup:b:scenes:1:leaf:{i}:color",
            label="Colour",
            group="[1] Scene",
            group_key="scenes:1",
        )
        for i in range(3)
    ]
    _render(panel, grouped, open_first=True)
    # While filtering, nodes are force-opened (set_next_item_open) so matches are visible; the
    # child rows must render through that path without error too.
    _render(panel, grouped, filtering=True)
    flat = [
        _row(
            ref_id="p0",
            label="P0",
            writable=False,
            default="3",
            choices=(("3", "RGBW"),),
        ),
        _row(ref_id="p1", label="P1", choices=(("0", "Off"), ("1", "On")), value="1"),
        _row(ref_id="p2", label="P2", value="x"),
    ]
    _render(panel, flat)


@pytest.mark.parametrize("count", [2, 8, 12, 13, 200])
@pytest.mark.parametrize("scale", [1.0, 2.0])
@pytest.mark.parametrize("mode", ["editable", "readonly", "mixed"])
def test_short_sections_expand_and_long_sections_scroll(
    context: None, monkeypatch: pytest.MonkeyPatch, count: int, scale: float, mode: str
) -> None:
    from imgui_bundle.imgui import internal

    style = imgui.get_style()
    style.scale_all_sizes(scale)
    style.font_scale_main = scale
    panel = PluginParamsPanel(_on_edit=lambda *_: None)
    rows = [
        _row(
            ref_id=f"p{i}",
            label=f"P{i}",
            choices=(("0", "Off"), ("1", "On")),
            writable=mode == "editable" or (mode == "mixed" and i % 2 == 1),
        )
        for i in range(count)
    ]
    tables: list[tuple[bool, float, float]] = []
    end_table = imgui.end_table

    def record_table() -> None:
        table = internal.get_current_table()
        scrolling = bool(table.flags & imgui.TableFlags_.scroll_y)
        scroll_max = imgui.get_scroll_max_y()
        end_table()
        tables.append((scrolling, table.outer_rect.get_height(), scroll_max))

    monkeypatch.setattr(imgui, "end_table", record_table)
    row_height = 0.0
    natural_height = 0.0
    for _ in range(3):
        imgui.new_frame()
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        imgui.begin("Sizing")
        row_height = imgui.get_frame_height() + 2 * style.cell_padding.y
        natural_height = sum(
            (imgui.get_frame_height() if row.writable else imgui.get_text_line_height())
            + 2 * style.cell_padding.y
            for row in rows
        )
        panel._render_standard_table(_StubDevice(), "Section", rows)  # type: ignore[arg-type]
        imgui.end()
        imgui.end_frame()
    scrolling, height, scroll_max = tables[-1]
    assert scrolling is (count > 12)
    if scrolling:
        assert scroll_max > 0
        assert height < count * row_height
    else:
        assert height >= natural_height


def test_display_type_index_defaults_to_triton_6325() -> None:
    assert _display_type_index("6136UP") == 0
    assert _display_type_index("triton-6323") == 1
    assert _display_type_index("triton-6325") == 2
    # Unknown / None fall back to the triton 6325 (index 2).
    assert _display_type_index(None) == 2
    assert _display_type_index("something-else") == 2


def _render_setup(panel: PluginParamsPanel, device: object) -> None:
    for _ in range(2):
        imgui.new_frame()
        if imgui.begin("w"):
            panel._render_triton_setup(device)  # type: ignore[arg-type]
        imgui.end()
        imgui.end_frame()


def test_triton_setup_renders_and_seeds_from_detection(context: None) -> None:
    # The setup area must render without error and pre-select the detected display type.
    panel = PluginParamsPanel(
        _on_edit=lambda *_: None,
        _on_setup_display=lambda *_: None,
        _on_import_prj=lambda *_: None,
        _detect_display_type=lambda _device: "triton-6323",
    )
    device = _StubDevice()
    _render_setup(panel, device)
    assert panel._setup_type_idx["1.1.5"] == 1


def _render_panel(panel: PluginParamsPanel, device: object) -> None:
    for _ in range(2):
        imgui.new_frame()
        if imgui.begin("w"):
            panel.render(device)  # type: ignore[arg-type]
        imgui.end()
        imgui.end_frame()


class _TritonStub:
    individual_address = "1.1.5"
    app = SimpleNamespace(id="app")
    parameter_instance_refs: ClassVar[list[object]] = []


def test_render_shows_setup_only_without_sections(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The setup area replaces the empty-state only for a blob-only triton mapping with no
    # sections; a device with a normal section list renders those instead.
    panel = PluginParamsPanel(
        _on_edit=lambda *_: None, _on_setup_display=lambda *_: None
    )
    calls: list[object] = []
    monkeypatch.setattr(
        panel, "_render_triton_setup", lambda device: calls.append(device)
    )
    mapping = SimpleNamespace(
        blob_reader="triton_lcd", title="LCD Display", provider=""
    )
    monkeypatch.setattr(ppp, "mapping_for_device", lambda _device: mapping)
    device = _TritonStub()

    monkeypatch.setattr(panel, "_cached_sections", lambda _d, _m: [])
    _render_panel(panel, device)
    assert calls and all(c is device for c in calls)

    calls.clear()
    section = PluginSectionView(
        section="Section",
        parameters=[
            PluginParameter(
                ref_id="r",
                name="r",
                label="L",
                value="v",
                default_value="",
                section="",
                display_order=0,
                writable=True,
            )
        ],
    )
    monkeypatch.setattr(panel, "_cached_sections", lambda _d, _m: [section])
    _render_panel(panel, device)
    assert calls == []


def test_native_panel_omits_standard_download_hint(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = PluginParamsPanel(_on_edit=lambda *_: None)
    mapping = ppp.PluginMapping((), "Native", (), provider="example")
    monkeypatch.setattr(ppp, "mapping_for_device", lambda _: mapping)
    monkeypatch.setattr(
        panel, "_cached_sections", lambda *_: [PluginSectionView("Section")]
    )
    texts: list[str] = []
    monkeypatch.setattr(imgui, "text_wrapped", texts.append)
    monkeypatch.setattr(imgui, "text_disabled", texts.append)
    _render_panel(panel, _TritonStub())
    assert ppp.S.PLUGIN_PARAMS_EDIT_NOTE not in texts
    assert ppp.S.PLUGIN_PARAMS_FILTER_EMPTY not in texts


def test_parameter_filter_has_a_hint_and_can_be_cleared(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import editor_gui.widgets.filter_box as filter_widget

    panel = PluginParamsPanel(_on_edit=lambda *_: None)
    mapping = ppp.PluginMapping((), "PowerTool", (), provider="powertool")
    section = PluginSectionView(
        "Parameters",
        [
            PluginParameter(
                ref_id=name,
                name=name,
                label=name,
                value="",
                default_value="",
                section="Parameters",
                display_order=0,
                writable=True,
            )
            for name in ["LED colour", "Switching"]
        ],
    )
    monkeypatch.setattr(ppp, "mapping_for_device", lambda _: mapping)
    monkeypatch.setattr(panel, "_cached_sections", lambda *_: [section])
    hints: list[str] = []
    rendered: list[list[str]] = []
    input_text = imgui.input_text_with_hint
    search = "LED"
    clear = False

    def type_search(widget_id: str, hint: str, text: str) -> tuple[bool, str]:
        hints.append(hint)
        input_text(widget_id, hint, text)
        return True, search

    monkeypatch.setattr(imgui, "input_text_with_hint", type_search)
    monkeypatch.setattr(filter_widget, "x_button", lambda *_: clear)
    monkeypatch.setattr(
        panel,
        "_render_section",
        lambda _device, _section, rows, **_kwargs: rendered.append(
            [r.label for r in rows]
        ),
    )
    _render_panel(panel, _TritonStub())
    assert hints and all(hint == ppp.S.CONFIGURE_PARAM_FILTER_HINT for hint in hints)
    assert panel._filter == "LED"
    assert rendered[-1] == ["LED colour"]
    clear = True
    _render_panel(panel, _TritonStub())
    assert panel._filter == ""
    assert rendered[-1] == ["LED colour", "Switching"]


def test_native_setup_omits_static_parameter_empty_hint(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = PluginParamsPanel(_on_edit=lambda *_: None)
    mapping = ppp.PluginMapping((), "Native", (), provider="example")
    monkeypatch.setattr(ppp, "mapping_for_device", lambda _: mapping)
    monkeypatch.setattr(panel, "_cached_sections", lambda *_: [])
    texts: list[str] = []
    monkeypatch.setattr(imgui, "text_disabled", texts.append)
    _render_panel(panel, _TritonStub())
    assert ppp.S.PLUGIN_PARAMS_EMPTY not in texts


def test_missing_provider_shows_error_without_crashing(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = PluginParamsPanel(_on_edit=lambda *_: None)
    mapping = ppp.PluginMapping((), "Unavailable", (), provider="missing")
    monkeypatch.setattr(ppp, "mapping_for_device", lambda _: mapping)
    monkeypatch.setattr(
        panel,
        "_cached_sections",
        lambda device, mapping: ppp.collect_from_device(device, mapping),
    )
    texts: list[str] = []
    monkeypatch.setattr(imgui, "text_colored", lambda _, text: texts.append(text))
    _render_panel(panel, _TritonStub())
    assert any("unavailable" in text for text in texts)


def test_triton_actions_row_renders_import_and_export(context: None) -> None:
    # Above a configured display the import/export ".prj" actions stay reachable (the setup area
    # is gone once a config exists), and they render without error.
    panel = PluginParamsPanel(
        _on_edit=lambda *_: None,
        _on_import_prj=lambda *_: None,
        _on_export_prj=lambda *_: None,
    )
    device = _StubDevice()
    for _ in range(2):
        imgui.new_frame()
        if imgui.begin("w"):
            panel._render_triton_actions(device)  # type: ignore[arg-type]
        imgui.end()
        imgui.end_frame()


def test_internal_sections_restore_collapse_after_search(context, monkeypatch):
    panel = PluginParamsPanel(_on_edit=lambda *_: None)
    opened = []
    monkeypatch.setattr(imgui, "set_next_item_open", lambda value: opened.append(value))
    monkeypatch.setattr(imgui, "collapsing_header", lambda *_: opened[-1])
    monkeypatch.setattr(panel, "_render_standard_table", lambda *args, **kwargs: None)
    device = _StubDevice()
    panel._render_section(device, "Internal", [_row()], collapsed=True)
    panel._render_section(device, "Internal", [_row()], collapsed=True, filtering=True)
    panel._render_section(device, "Internal", [_row()], collapsed=True)
    assert opened == [False, True, False]


def test_group_identity_and_page_scrolling(context, monkeypatch):
    panel = PluginParamsPanel(_on_edit=lambda *_: None)
    rows = [
        _row(ref_id=f"p{i}", group="Same Label", group_key="one" if i < 15 else "two")
        for i in range(17)
    ]
    nodes = []
    tables = []
    monkeypatch.setattr(
        imgui, "tree_node_ex", lambda label, flags: nodes.append(label) or True
    )
    monkeypatch.setattr(imgui, "tree_pop", lambda: None)
    original = panel._render_standard_table

    def record(device, label, page, **kwargs):
        if all(not row.group for row in page):
            tables.append(len(page))
        return original(device, label, page, **kwargs)

    monkeypatch.setattr(panel, "_render_standard_table", record)
    _render(panel, rows, filtering=True)
    assert set(tables) == {15, 2}
    assert len(set(nodes)) == 2
