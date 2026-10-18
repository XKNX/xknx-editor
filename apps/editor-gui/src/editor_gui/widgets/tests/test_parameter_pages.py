from __future__ import annotations

from editor_gui.widgets.parameter_widgets import build_pages, filter_pages
from xknxeditor.namespaces.intermediate.parameter_block_layout_t import (
    ParameterBlockLayout,
)
from xknxeditor.prod.parser_v2.ui import (
    NumberWidget,
    UiNode,
    UiParameter,
    UiParameterBlock,
    UiTab,
)


def _param(ref_id: str, label: str) -> UiParameter:
    return UiParameter(
        ref_id=ref_id,
        label=label,
        value="0",
        default_value="0",
        widget=NumberWidget(min=0, max=10),
    )


def _block(
    block_id: str, text: str, *children: UiNode, **kwargs: object
) -> UiParameterBlock:
    return UiParameterBlock(id=block_id, text=text, children=children, **kwargs)  # type: ignore[arg-type]


def _tree() -> list[UiNode]:
    return [
        UiTab(
            id="general",
            name="General",
            independent=True,
            children=(_block("B-1", "Common", _param("P-1", "Startup delay")),),
        ),
        UiTab(
            id="CH-1",
            text="Presence",
            icon="sphere",
            children=(
                _block("B-2", "Settings", _param("P-2", "Lux threshold")),
                _block(
                    "B-3",
                    "PM 1",
                    _param("P-3", "Mode"),
                    _block("B-4", "Inputs", _param("P-4", "Input A")),
                    _block("B-5", "", _param("P-5", "Inline value"), inline=True),
                    icon="sphere",
                ),
            ),
        ),
    ]


def test_channels_and_pages() -> None:
    pages = build_pages(_tree(), "1")
    assert [p.label for p in pages] == ["Common", "Presence"]
    channel = pages[1]
    assert channel.icon == "sphere"
    assert not channel.selectable
    assert [p.label for p in channel.children] == ["Settings", "PM 1"]
    pm = channel.children[1]
    assert [p.label for p in pm.children] == ["Inputs"]
    shown = [n.ref_id for n in pm.content if isinstance(n, UiParameter)]
    assert shown == ["P-3"]
    assert any(isinstance(n, UiParameterBlock) and n.inline for n in pm.content)


def test_grid_page_keeps_its_cells() -> None:
    grid = _block(
        "B-6",
        "Grid",
        _block("B-7", "", _param("P-6", "Cell"), cell="1,1"),
        layout=ParameterBlockLayout.GRID,
    )
    (page,) = build_pages([grid], "1")
    assert page.children == ()
    assert page.content == grid.children


def test_parameters_outside_pages_get_a_general_page() -> None:
    pages = build_pages([_param("P-1", "Loose")], "1")
    assert len(pages) == 1
    assert pages[0].selectable
    assert pages[0].key == "1_root"


def test_duplicate_keys_are_made_unique() -> None:
    pages = build_pages([_block("B-1", "A"), _block("B-1", "B")], "1")
    assert len({p.key for p in pages}) == 2


def test_filter_keeps_matching_pages_and_their_parents() -> None:
    pages = filter_pages(build_pages(_tree(), "1"), "input a")
    assert [p.label for p in pages] == ["Presence"]
    (pm,) = pages[0].children
    assert pm.label == "PM 1"
    assert not pm.selectable
    assert [p.label for p in pm.children] == ["Inputs"]
    assert pm.children[0].selectable


def test_a_channel_whose_pages_sit_in_an_inline_list_opens_its_first_page() -> None:
    channel = UiTab(
        id="openknx",
        name="OpenKNX",
        children=(
            _block(
                "PB-2",
                "",
                _block("PB-3", "Allgemein", _param("P-1", "Startup delay")),
                _block("PB-4", "Erweitert", _param("P-2", "Heartbeat")),
                inline=True,
                layout=ParameterBlockLayout.LIST,
            ),
        ),
    )
    (page,) = build_pages([channel], "d")
    assert not page.selectable
    assert [c.label for c in page.children] == ["Allgemein", "Erweitert"]


def test_an_inline_block_with_parameters_keeps_its_channel_selectable() -> None:
    channel = UiTab(
        id="ch",
        name="Channel",
        children=(
            _block(
                "PB-1",
                "",
                _param("P-1", "Mode"),
                _block("PB-2", "Details", _param("P-2", "Delay")),
                inline=True,
                layout=ParameterBlockLayout.LIST,
            ),
        ),
    )
    (page,) = build_pages([channel], "d")
    assert page.selectable
    assert [c.label for c in page.children] == ["Details"]
