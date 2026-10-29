"""A device's ``<GroupObjectTree>`` for the export, from its parameter evaluation.

The KNX tool takes the group objects a device's tree lists as the device's active ones, so a
tree the editor's changes made out of date (objects it activated) is written again: as the
stored trees are kept (namespace-free XML, ids relative to the application), with the
``Text`` of a renamed channel or block from the stored tree.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from xknxeditor.proj.core.knxproj_export import relidref

if TYPE_CHECKING:
    from xknxeditor.prod.parser_v2.context import ObjectTreeNode


def group_object_tree_xml(root: ObjectTreeNode, stored: str = "") -> str:
    """The ``<GroupObjectTree>`` of ``root`` (see ``DynamicUI.group_object_tree``)."""
    texts = _stored_texts(stored)
    element = ET.Element("GroupObjectTree")
    _fill(element, root, texts)
    return ET.tostring(element, encoding="unicode")


def _fill(
    element: ET.Element, node: ObjectTreeNode, texts: dict[tuple[str, str], str]
) -> None:
    if node.objects:
        element.set("GroupObjectInstances", " ".join(relidref(o) for o in node.objects))
    if not node.nodes:
        return
    nodes = ET.SubElement(element, "Nodes")
    for child in node.nodes:
        ref = relidref(child.ref_id)
        sub = ET.SubElement(nodes, "Node", Type=child.kind, RefId=ref)
        text = texts.get((child.kind, ref))
        if text is not None:
            sub.set("Text", text)
        _fill(sub, child, texts)


def _stored_texts(stored: str) -> dict[tuple[str, str], str]:
    """The ``Text`` of each node of the stored tree, by type and id."""
    if not stored:
        return {}
    try:
        root = ET.fromstring(stored)
    except ET.ParseError:
        return {}
    return {
        (node.get("Type", ""), node.get("RefId", "")): text
        for node in root.iter("Node")
        if (text := node.get("Text")) is not None
    }
