"""Channel identities and labels shared by device queries, parameter pages and diagrams."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from xknxeditor.prod.parser_v2.ui import UiComObject, UiNode, UiParameterBlock, UiTab


def raw_label(node: UiTab | UiParameterBlock) -> str:
    fallback = "Tab" if isinstance(node, UiTab) else ""
    return node.text or node.name or node.id or fallback


def repeated_labels(nodes: Iterable[UiNode]) -> frozenset[str]:
    """The labels that several of the tabs and blocks among ``nodes`` share."""
    counts = Counter(
        raw_label(node) for node in nodes if isinstance(node, UiTab | UiParameterBlock)
    )
    return frozenset(label for label, count in counts.items() if count > 1)


def instance_label(node: UiTab | UiParameterBlock, repeated: frozenset[str]) -> str:
    """Disambiguate a repeated module instance's tab/block label with its instance index.

    Some apps (e.g. the MDT DALI gateway) give every repeated instance the same text — 16 group tabs
    all "G,", 64 ECG blocks all "ECG ," — because the instance-number placeholder isn't substituted
    into the title; only the id differs (``…_MI-1`` … ``_MI-64``). Append that index so they read
    "G, 1"…"G, 16" / "ECG, 1"…"ECG, 64" (matching the "G1"/"ECG1"… labelling). Only a label that
    siblings share (``repeated``) gets it: instances with texts of their own ("Channel B: Kitchen")
    keep them, as do labels that already carry the number.
    """
    label = raw_label(node)
    match = re.search(r"_MI-(\d+)", node.id or "")
    if label in repeated and match and match.group(1) not in re.findall(r"\d+", label):
        return f"{label.rstrip(' ,;:')} {match.group(1)}".strip()
    return label


def com_object_channels(
    nodes: Iterable[UiNode],
) -> dict[str, tuple[str, str, int]]:
    """The channel of each group object in a device's parameter tree, by its ComObjectRef id:
    ``(channel id, label as the page tree shows it, position of the channel)``. A group object in
    no channel (or in a channel-independent block) has none; one the tree lists twice keeps the
    place it is listed first, as the device's group objects do."""
    first: dict[str, tuple[str, str, int] | None] = {}
    count = 0

    def walk(children: Iterable[UiNode], channel: tuple[str, str, int] | None) -> None:
        nonlocal count
        children = tuple(children)
        repeated = repeated_labels(children)
        for node in children:
            if isinstance(node, UiComObject):
                first.setdefault(node.ref_id, channel)
            elif isinstance(node, UiTab) and not node.independent:
                count += 1
                label = instance_label(node, repeated)
                walk(node.children, (node.id or label, label, count))
            elif isinstance(node, UiTab | UiParameterBlock):
                walk(node.children, channel)

    walk(nodes, None)
    return {ref: channel for ref, channel in first.items() if channel is not None}
