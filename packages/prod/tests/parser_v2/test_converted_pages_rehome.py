"""Page assignment of a converted-from-older-format (``PreEts4Style``) application.

Such a device uses both page models at once, and regressing either one leaves pages empty:
- the selector chain re-home (like the Gira Tastsensor 3 Plus): a parameter placed under one block
  but whose chain names another page moves onto that page;
- display order filling empty pages (like the ABB AE/S4 analogue inputs): a parameter left on its
  channel whose display order belongs to an empty sub-page is drawn onto that sub-page.
A page the re-home already populated is never reshuffled by display order.
"""

from __future__ import annotations

from pathlib import Path

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.ui import UiNode, UiParameter, UiParameterBlock, UiTab

XML = (
    Path(__file__).parents[1] / "fixtures" / "converted_pages_application.xml"
).read_bytes()


def _pages(nodes: list[UiNode] | tuple[UiNode, ...]) -> dict[str, set[str]]:
    """Map each page block's heading text to the set of parameter labels shown directly on it."""
    out: dict[str, set[str]] = {}
    for node in nodes:
        if isinstance(node, UiParameterBlock):
            out[node.text or node.name or node.id] = {
                child.label
                for child in node.children
                if isinstance(child, UiParameter) and child.access.name != "NONE"
            }
            out.update(_pages(node.children))
        elif isinstance(node, UiTab):
            out.update(_pages(node.children))
    return out


def test_converted_device_uses_both_page_models() -> None:
    app = parse_application_xml(XML, "M-00FA")[0]
    assert app.program.pre_ets4_style
    pages = _pages(DynamicUI(app.program).ui())

    # selector chain: "Detection" is placed under "Controller" but its chain names "Measurement"
    assert "Detection" in pages["Measurement"]
    assert "Detection" not in pages["Controller"]
    # the block it was nested under keeps its own parameter
    assert "Ctrl setting" in pages["Controller"]

    # display order: "Use threshold" sits on the populated "Channel" but its display order belongs
    # to the empty "Threshold" page, so it is drawn onto it
    assert "Use threshold" in pages["Threshold"]
    assert "Use threshold" not in pages["Channel"]
    # the channel keeps its own parameters
    assert {"Scan rate", "Filter"} <= pages["Channel"]


def test_display_order_does_not_pull_from_a_populated_page() -> None:
    # "Filter" sits on "Channel"; its display order belongs to "Measurement", but the re-home has
    # already populated that page, so display order leaves "Filter" on its channel.
    pages = _pages(DynamicUI(parse_application_xml(XML, "M-00FA")[0].program).ui())
    assert "Filter" in pages["Channel"]
    assert "Filter" not in pages["Measurement"]
