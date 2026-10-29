"""The group object trees the exports get for devices whose stored tree is out of date."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from editor_gui.plugins.project.object_tree import group_object_tree_xml
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.prod.parser_v2.context import ObjectTreeNode

APP = "M-00FA_A-0005-01-0000"


def _tree() -> ObjectTreeNode:
    return ObjectTreeNode(
        "",
        "",
        [f"{APP}_O-1_R-1"],
        [
            ObjectTreeNode(
                "Channel",
                f"{APP}_CH-1",
                [f"{APP}_O-2_R-2"],
                [ObjectTreeNode("Folder", f"{APP}_PB-3", [f"{APP}_O-3_R-3"], [])],
            ),
            ObjectTreeNode("Channel", f"{APP}_MD-1_M-1_MI-1_CH-2", [], []),
        ],
    )


def test_the_tree_as_stored_trees_are_kept() -> None:
    stored = (
        '<GroupObjectTree><Nodes><Node Type="Channel" RefId="CH-1" Text="Kitchen" />'
        "</Nodes></GroupObjectTree>"
    )
    assert group_object_tree_xml(_tree(), stored) == (
        '<GroupObjectTree GroupObjectInstances="O-1_R-1"><Nodes>'
        # A renamed channel keeps its name.
        '<Node Type="Channel" RefId="CH-1" Text="Kitchen" GroupObjectInstances="O-2_R-2">'
        '<Nodes><Node Type="Folder" RefId="PB-3" GroupObjectInstances="O-3_R-3" /></Nodes>'
        "</Node>"
        '<Node Type="Channel" RefId="MD-1_M-1_MI-1_CH-2" />'
        "</Nodes></GroupObjectTree>"
    )
    assert (
        group_object_tree_xml(ObjectTreeNode("", "", [], [])) == "<GroupObjectTree />"
    )
    # A stored tree that cannot be read has no names to keep.
    assert "Text" not in group_object_tree_xml(_tree(), "<GroupObjectTree")


def _object(ref: str) -> Any:
    return SimpleNamespace(ref_id=ref, instance_ref_id=ref)


def test_only_devices_with_an_out_of_date_tree_get_one() -> None:
    covered = '<GroupObjectTree GroupObjectInstances="O-1_R-1" />'
    rows = [
        # Its tree lists its only object.
        SimpleNamespace(
            id=1, group_object_tree=covered, com_objects=[_object(f"{APP}_O-1_R-1")]
        ),
        # The editor activated O-2.
        SimpleNamespace(
            id=2,
            group_object_tree=covered,
            com_objects=[_object(f"{APP}_O-1_R-1"), _object(f"{APP}_O-2_R-2")],
        ),
        # No objects, nothing to list.
        SimpleNamespace(id=3, group_object_tree="", com_objects=[]),
    ]
    service: Any = MagicMock()
    service._pid = "P-1"
    service._svc.devices.return_value = rows
    ui = SimpleNamespace(group_object_tree=_tree)
    service.find_device_by_node_id.return_value = SimpleNamespace(
        app=object(), dynamic_ui=ui
    )
    trees = ProjectService.group_object_trees(service)
    assert list(trees) == [2]
    assert trees[2].startswith('<GroupObjectTree GroupObjectInstances="O-1_R-1">')
    service._pid = None
    assert ProjectService.group_object_trees(service) == {}
