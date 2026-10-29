"""The group object tree of a device, as its parameters select it: channels, the parameter blocks
shown in the group object tree and the objects in them."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.context import ObjectTreeNode
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

APP = "M-00FA_A-0005-01-0000"
XML = (
    Path(__file__).parents[1] / "fixtures" / "object_tree_application.xml"
).read_bytes()


def _ui() -> DynamicUI:
    return DynamicUI(parse_application_xml(XML, "M-00FA")[0].program)


def _short(node: ObjectTreeNode) -> tuple[object, ...]:
    """The node without the application prefix of its ids."""

    def short(ref: str) -> str:
        return ref.removeprefix(f"{APP}_")

    return (
        node.kind,
        short(node.ref_id),
        [short(o) for o in node.objects],
        [_short(n) for n in node.nodes],
    )


def test_the_tree_of_the_defaults() -> None:
    assert _short(_ui().group_object_tree()) == (
        "",
        "",
        # The channel-independent block is no node: its objects are the tree's.
        ["O-1_R-1"],
        [
            (
                "Channel",
                "CH-1",
                # In document order; a block not shown in the tree is no node.
                ["O-6_R-6", "O-2_R-2"],
                # The empty folder of the inactive extra object is left out.
                [("Folder", "PB-3", ["O-3_R-3"], [])],
            ),
            # An active channel without objects stays.
            ("Channel", "CH-2", [], []),
        ],
    )


def test_the_tree_follows_the_parameters() -> None:
    ui = _ui()
    ui.set_parameter_ref(f"{APP}_P-1_R-1", "1")
    tree = _short(ui.group_object_tree())
    channel_a, channel_b, channel_c = tree[3]  # type: ignore[misc]
    assert channel_a[3] == [
        ("Folder", "PB-3", ["O-3_R-3"], []),
        ("Folder", "PB-4", ["O-4_R-4"], []),
    ]
    assert channel_b == ("Channel", "CH-2", [], [])
    assert channel_c == ("Channel", "CH-3", ["O-5_R-5"], [])
