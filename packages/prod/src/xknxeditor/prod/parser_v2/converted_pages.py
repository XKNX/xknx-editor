"""Parameter pages of applications converted from the older product format (``PreEts4Style``).

In that format a page is a heading in one list of parameters ordered by display order: a parameter
belongs to the page in front of it. The converted dynamic section keeps the pages as blocks, but
may leave a page empty and put its parameters into a hidden block (``Access="None"``) beside it,
e.g. the rocker pages of the Gira Tastsensor 3 Basis. Such a parameter is shown on the visible page
beside the hidden block with the greatest display order not above its own, and the page lists its
parameters in display order (an internal parameter like "GP_ConvertibleVersion" of the ABB
valve drives too: it is shown at the end of the "D: Function" page).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from xknxeditor.namespaces.intermediate.access_t import Access

from .ui import UiNode, UiParameter, UiParameterBlock, UiTab

DisplayOrder = Callable[[str], int | None]


def place_hidden_parameters(
    nodes: Sequence[UiNode], display_order: DisplayOrder
) -> list[UiNode]:
    """Move the visible parameters of hidden blocks onto the visible pages beside them.

    ``display_order`` returns a ParameterRef's display order, or ``None`` when it has none (the
    parameter then stays where it is). Nodes without hidden blocks beside visible pages are
    returned unchanged.
    """
    return _place(list(nodes), display_order)


def _place(nodes: list[UiNode], display_order: DisplayOrder) -> list[UiNode]:
    nodes = [_with_placed_children(node, display_order) for node in nodes]
    pages: list[tuple[int, int]] = []
    for index, node in enumerate(nodes):
        if (
            isinstance(node, UiParameterBlock)
            and not node.hidden
            and not node.inline
            and node.param_ref_id is not None
        ):
            order = display_order(node.param_ref_id)
            if order is not None:
                pages.append((order, index))
    if not pages:
        return nodes
    moved: dict[int, list[tuple[int, UiParameter]]] = {}
    for node in nodes:
        if not (isinstance(node, UiParameterBlock) and node.hidden):
            continue
        for parameter in _visible_parameters(node.children):
            order = display_order(parameter.ref_id)
            if order is None:
                continue
            target = max(
                (
                    (page_order, index)
                    for page_order, index in pages
                    if page_order <= order
                ),
                default=None,
            )
            if target is not None:
                moved.setdefault(target[1], []).append((order, parameter))
    if not moved:
        return nodes
    taken = {id(parameter) for items in moved.values() for _, parameter in items}
    result: list[UiNode] = []
    for index, node in enumerate(nodes):
        if isinstance(node, UiParameterBlock) and node.hidden:
            node = replace(node, children=_without(node.children, taken))
        elif index in moved:
            assert isinstance(node, UiParameterBlock)
            node = replace(
                node,
                children=_in_display_order(node.children, moved[index], display_order),
            )
        result.append(node)
    return result


def _with_placed_children(node: UiNode, display_order: DisplayOrder) -> UiNode:
    if isinstance(node, UiTab | UiParameterBlock) and node.children:
        return replace(node, children=tuple(_place(list(node.children), display_order)))
    return node


def _visible_parameters(nodes: Sequence[UiNode]) -> list[UiParameter]:
    """The shown parameters among ``nodes`` and the blocks inside them, in document order."""
    found: list[UiParameter] = []
    for node in nodes:
        if isinstance(node, UiParameter):
            if node.access != Access.NONE:
                found.append(node)
        elif isinstance(node, UiTab | UiParameterBlock):
            found.extend(_visible_parameters(node.children))
    return found


def _without(nodes: Sequence[UiNode], taken: set[int]) -> tuple[UiNode, ...]:
    kept: list[UiNode] = []
    for node in nodes:
        if id(node) in taken:
            continue
        if isinstance(node, UiTab | UiParameterBlock):
            node = replace(node, children=_without(node.children, taken))
        kept.append(node)
    return tuple(kept)


def _in_display_order(
    own: Sequence[UiNode],
    added: list[tuple[int, UiParameter]],
    display_order: DisplayOrder,
) -> tuple[UiNode, ...]:
    """The page's own nodes and the added parameters, sorted by display order. A node without one
    (a separator, a parameter without display order) keeps its place after the node before it."""
    keyed: list[tuple[int, int, UiNode]] = []
    previous = -1
    for sequence, node in enumerate(own):
        order = display_order(node.ref_id) if isinstance(node, UiParameter) else None
        previous = order if order is not None else previous
        keyed.append((previous, sequence, node))
    start = len(keyed)
    for offset, (order, parameter) in enumerate(added):
        keyed.append((order, start + offset, parameter))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return tuple(node for _, _, node in keyed)
