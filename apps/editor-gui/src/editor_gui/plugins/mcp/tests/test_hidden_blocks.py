"""A block the parameter view never shows (Access None) is not shown through MCP either: its
parameters are neither listed, found nor editable, and the tree reads leave it out while the index
paths of its siblings stay as they are."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from editor_gui.device import collect_ui_parameters
from editor_gui.plugins.mcp.tools.project import (
    _count_leaves,
    _find_subtree,
    _outline_node,
    _search_node,
    _ui_node,
    require_editable_param,
)
from xknxeditor.prod.parser_v2.ui import (
    CheckBoxWidget,
    UiParameter,
    UiParameterBlock,
    UiTab,
)


def _parameter(ref_id: str, label: str) -> UiParameter:
    return UiParameter(ref_id=ref_id, label=label, value="0", widget=CheckBoxWidget())


_TAB = UiTab(
    id="T-1",
    text="Channel",
    children=(
        UiParameterBlock(
            id="PB-1", text="Shown", children=(_parameter("R-1", "Brightness"),)
        ),
        UiParameterBlock(
            id="PB-2",
            text="Internal",
            hidden=True,
            children=(_parameter("R-2", "Brightness offset"),),
        ),
        UiParameterBlock(
            id="PB-3", text="Also shown", children=(_parameter("R-3", "Delay"),)
        ),
    ),
)


class _Device:
    node_id = 7
    dynamic_ui = None

    def get_ui(self) -> list[Any]:
        return [_TAB]


def test_its_parameters_are_not_collected() -> None:
    assert [p.ref_id for p in collect_ui_parameters([_TAB])] == ["R-1", "R-3"]


def test_the_tree_leaves_it_out_and_keeps_the_sibling_paths() -> None:
    tab = _ui_node(_TAB)
    assert [(block["name"], block["id"]) for block in tab["children"]] == [
        ("Shown", "0.0"),
        ("Also shown", "0.2"),
    ]
    outline = _outline_node(_TAB)
    assert outline is not None
    assert [block["id"] for block in outline["children"]] == ["0.0", "0.2"]
    assert outline["parameter_count"] == 2
    assert _count_leaves(_TAB) == (2, 0)


def test_search_and_subtree_reads_do_not_reach_it() -> None:
    found = _search_node(_TAB, "brightness")
    assert found is not None
    assert [block["name"] for block in found["children"]] == ["Shown"]
    assert _search_node(_TAB, "internal") is None
    assert _find_subtree([_TAB], "0.1") is None
    assert _find_subtree([_TAB], "0.2") is _TAB.children[2]


def test_its_parameters_cannot_be_set() -> None:
    device: Any = _Device()
    assert require_editable_param(device, "R-1").ref_id == "R-1"
    with pytest.raises(ToolError, match="is not visible"):
        require_editable_param(device, "R-2")
