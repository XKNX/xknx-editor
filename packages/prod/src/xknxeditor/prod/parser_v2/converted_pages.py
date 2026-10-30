"""Parameter pages of applications converted from the older product format (``PreEts4Style``).

In that format a page is a heading in one list of parameters ordered by display order, and the
converted dynamic section's nesting does not decide the pages (:func:`pages_by_display_order`).
Per channel, the shown blocks, parameters and separators go into one list, sorted by display order
(a stable sort, so the document order decides between equal ones). A block starts a page, and every
parameter or separator after it is on that page, until the next block. Pages are not nested, and a
page without anything on it is not shown. A block hidden by its access is no page, but its shown
parameters are in the list. A block's display order is that of its heading ``ParameterRef`` (its
``ParamRefId``), 0 without one; a parameter's is that of its own ``ParameterRef``. A node without one
(a separator) keeps its place behind the node in front of it.

A converted application with a parameter dialog of its own (a UI plug-in) has no generic pages to
follow. It keeps the editor's own layout (:func:`place_hidden_parameters`), which runs after the
selector-chain re-home (``dynamic._rehome_to_pages``): a hidden block's parameters move onto the page
in front of them by display order, and a parameter whose display order belongs to a page the re-home
left EMPTY is drawn onto that page. A page the re-home already populated keeps its parameters.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Sequence
from dataclasses import replace

from xknxeditor.namespaces.intermediate.access_t import Access

from .ui import UiNode, UiParameter, UiParameterBlock, UiTab

DisplayOrder = Callable[[str], int | None]


def place_hidden_parameters(
    nodes: Sequence[UiNode],
    display_order: DisplayOrder,
    pinned: frozenset[str] = frozenset(),
) -> list[UiNode]:
    """Place by display order what the selector-chain re-home did not: the visible parameters of
    hidden blocks move onto the page in front of them, and a parameter whose display order belongs
    to an empty page beside it is drawn onto that page.

    ``display_order`` returns a ParameterRef's display order, or ``None`` when it has none (the
    parameter then stays put). ``pinned`` holds ParameterRef ids already placed on their page by the
    re-home; they are left where they are. Nodes with no parameter to relocate are returned
    unchanged.
    """
    return _place(list(nodes), display_order, pinned)


def _place(
    nodes: list[UiNode], display_order: DisplayOrder, pinned: frozenset[str]
) -> list[UiNode]:
    nodes = [_with_placed_children(node, display_order, pinned) for node in nodes]
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
    pages.sort()
    page_orders = [order for order, _ in pages]
    # Pages the selector-chain re-home left without any parameter. Only these draw a parameter off
    # a visible page by display order, so a page the re-home already populated keeps its parameters.
    empty_pages = {
        index
        for _, index in pages
        if isinstance(block := nodes[index], UiParameterBlock)
        and not _visible_parameters(block.children)
    }

    def target_index(order: int) -> int | None:
        position = bisect.bisect_right(page_orders, order) - 1
        return pages[position][1] if position >= 0 else None

    moved: dict[int, list[tuple[int, UiParameter]]] = {}
    taken: set[int] = set()

    def consider(
        parameter: UiParameter, current_index: int | None, *, into_empty_only: bool
    ) -> None:
        if parameter.ref_id in pinned:
            return  # already placed by the selector-chain re-home
        order = display_order(parameter.ref_id)
        if order is None:
            return
        index = target_index(order)
        if index is None or index == current_index:
            return  # no page in front of it, or already on the right page
        if into_empty_only and index not in empty_pages:
            return  # do not pull it off a visible page onto an already-populated one
        moved.setdefault(index, []).append((order, parameter))
        taken.add(id(parameter))

    for index, node in enumerate(nodes):
        if not isinstance(node, UiParameterBlock):
            continue
        if node.hidden:
            # the hidden block's parameters belong to the pages beside it
            for parameter in _visible_parameters(node.children):
                consider(parameter, None, into_empty_only=False)
        elif not node.inline and node.param_ref_id is not None:
            # a parameter on a visible page whose display order belongs to an empty page beside it
            for child in node.children:
                if isinstance(child, UiParameter) and child.access != Access.NONE:
                    consider(child, index, into_empty_only=True)
    if not moved:
        return nodes
    result: list[UiNode] = []
    for index, node in enumerate(nodes):
        if isinstance(node, UiParameterBlock):
            children = _without(node.children, taken)
            if index in moved:
                children = _in_display_order(children, moved[index], display_order)
            if children is not node.children:
                node = replace(node, children=children)
        result.append(node)
    return result


def _with_placed_children(
    node: UiNode, display_order: DisplayOrder, pinned: frozenset[str]
) -> UiNode:
    if isinstance(node, UiTab | UiParameterBlock) and node.children:
        return replace(
            node, children=tuple(_place(list(node.children), display_order, pinned))
        )
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


def pages_by_display_order(
    nodes: Sequence[UiNode], display_order: DisplayOrder
) -> list[UiNode]:
    """``nodes`` with each channel's (tab's) pages built by display order (see the module); without
    any tab, ``nodes`` are the one channel."""
    if not any(isinstance(node, UiTab) for node in nodes):
        return list(_flat_pages(nodes, display_order))
    return [
        replace(node, children=_flat_pages(node.children, display_order))
        if isinstance(node, UiTab)
        else node
        for node in nodes
    ]


def _flat_pages(
    nodes: Sequence[UiNode], display_order: DisplayOrder
) -> tuple[UiNode, ...]:
    items: list[tuple[int, int, UiNode]] = []
    previous = 0

    def collect(ns: Sequence[UiNode]) -> None:
        nonlocal previous
        for node in ns:
            if isinstance(node, UiParameterBlock) and not node.inline:
                if not node.hidden:
                    order = (
                        display_order(node.param_ref_id) if node.param_ref_id else None
                    )
                    previous = order if order is not None else 0
                    items.append((previous, len(items), replace(node, children=())))
                collect(node.children)
                continue
            if isinstance(node, UiParameter):
                if node.access == Access.NONE:
                    continue
                order = display_order(node.ref_id)
                if order is not None:
                    previous = order
            items.append((previous, len(items), node))

    collect(nodes)
    items.sort(key=lambda item: (item[0], item[1]))
    # Before the first page, a node stays on the channel itself.
    channel: list[UiNode] = []
    pages: list[tuple[UiParameterBlock, list[UiNode]]] = []
    for _order, _position, node in items:
        if isinstance(node, UiParameterBlock) and not node.inline:
            pages.append((node, []))
        elif pages:
            pages[-1][1].append(node)
        else:
            channel.append(node)
    return tuple(channel) + tuple(
        replace(page, children=tuple(children)) for page, children in pages if children
    )
