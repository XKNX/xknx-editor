"""Tests for the plugin-parameter panel's display helpers and grouped (tree) rendering.

Points from issue #199: the "Vorgabe" (default) column must show an enum's label, not its raw
number; a long BLOB section must nest under a collapsible tree node per item instead of one flat
table.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from editor_gui.plugin_params import PluginParameter, PluginSectionView
from editor_gui.plugins.project.ui.plugin_params_panel import (
    PluginParamsPanel,
    _default_label,
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
