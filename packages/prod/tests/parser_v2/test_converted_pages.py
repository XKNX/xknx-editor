"""Pages of applications converted from the older product format: the visible parameters of a
hidden block go to the visible page beside it with the greatest display order not above their
own, in display order (the rocker pages of the Gira Tastsensor 3 Basis, issue #96; the internal
"GP_ConvertibleVersion" at the end of the "D: Function" page of an ABB valve drive)."""

from __future__ import annotations

from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.prod.parser_v2.converted_pages import place_hidden_parameters
from xknxeditor.prod.parser_v2.ui import (
    UiNode,
    UiParameter,
    UiParameterBlock,
    UiSeparator,
    UiTab,
)

# Display order per ParameterRef id; pages are keyed by their own (page) parameter.
ORDERS = {
    "page-1": 1000,
    "page-2": 3000,
    "page-full": 5000,
    "function-1": 1010,
    "led-1": 1221,
    "command-1": 1230,
    "function-2": 3010,
    "function-full": 5010,
    "internal": 1100,
    "own": 5020,
}


def _order(ref_id: str) -> int | None:
    return ORDERS.get(ref_id)


def _parameter(ref_id: str, access: Access = Access.READ_WRITE) -> UiParameter:
    return UiParameter(
        ref_id=ref_id, label=ref_id, value="", widget=None, access=access
    )


def _page(name: str, ref: str, *children: UiNode) -> UiParameterBlock:
    return UiParameterBlock(id=name, name=name, children=children, param_ref_id=ref)


def _labels(node: UiNode) -> list[str]:
    assert isinstance(node, UiParameterBlock)
    return [n.ref_id for n in node.children if isinstance(n, UiParameter)]


def _channel() -> UiTab:
    hidden = UiParameterBlock(
        id="hidden",
        name="Tastenauswahl",
        hidden=True,
        children=(
            _parameter("led-1"),
            _parameter("internal", Access.NONE),
            _parameter("command-1"),
            UiParameterBlock(id="inner", children=(_parameter("function-2"),)),
            _parameter("function-full"),
            _parameter("function-1"),
        ),
    )
    return UiTab(
        id="channel",
        text="Generic",
        children=(
            hidden,
            _page("Wippe 1", "page-1"),
            _page("Wippe 2", "page-2"),
            _page("Full", "page-full", _parameter("own")),
        ),
    )


def test_hidden_parameters_go_to_their_pages_in_display_order() -> None:
    (channel,) = place_hidden_parameters([_channel()], _order)
    assert isinstance(channel, UiTab)
    hidden, rocker_1, rocker_2, full = channel.children
    assert _labels(rocker_1) == ["function-1", "led-1", "command-1"]
    assert _labels(rocker_2) == ["function-2"]
    # A page with content of its own gets them too, sorted in by display order.
    assert _labels(full) == ["function-full", "own"]
    assert isinstance(hidden, UiParameterBlock)
    remaining = [
        node.ref_id for node in hidden.children if isinstance(node, UiParameter)
    ]
    assert remaining == ["internal"]


def test_a_hidden_parameter_is_not_shown() -> None:
    (channel,) = place_hidden_parameters([_channel()], _order)
    assert isinstance(channel, UiTab)
    shown = [ref for page in channel.children[1:] for ref in _labels(page)]
    assert "internal" not in shown


def test_without_display_orders_nothing_moves() -> None:
    channel = _channel()
    assert place_hidden_parameters([channel], lambda _ref: None) == [channel]


def test_a_separator_keeps_its_place() -> None:
    hidden = UiParameterBlock(
        id="hidden",
        hidden=True,
        children=(_parameter("led-1"), _parameter("function-1")),
    )
    separator = UiSeparator(id="separator", text="Status")
    (_, placed) = place_hidden_parameters(
        [hidden, _page("Wippe 1", "page-1", separator)], _order
    )
    assert isinstance(placed, UiParameterBlock)
    assert placed.children[0] == separator
    assert _labels(placed) == ["function-1", "led-1"]
