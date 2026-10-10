"""The DeviceInstance ``<GroupObjectTree>`` (channel/folder grouping) must survive import/export.

Regression for issue #14: the export emitted no ``GroupObjectTree``, so the channel/folder grouping,
nesting and order of a device's group objects were silently lost on export. The tree is now captured
verbatim on import (namespace-stripped) and re-emitted on export.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import Device

_FIXTURE = Path(__file__).parent / "fixtures" / "xknx_test_project_no_password.knxproj"


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _norm(elem: ET.Element) -> str:
    """Canonical string of an element: local tag + sorted attrs + children in order."""
    attrs = " ".join(f"{_localname(k)}={v}" for k, v in sorted(elem.attrib.items()))
    return f"({_localname(elem.tag)} {attrs}" + "".join(_norm(c) for c in elem) + ")"


def _group_object_trees(knxproj: Path) -> list[ET.Element]:
    """Every ``DeviceInstance/GroupObjectTree`` element across the archive's installation XMLs."""
    trees: list[ET.Element] = []
    with zipfile.ZipFile(knxproj) as zf:
        for name in zf.namelist():
            if not name.endswith("/0.xml"):
                continue
            root = ET.fromstring(zf.read(name))
            for device in root.iter():
                if _localname(device.tag) != "DeviceInstance":
                    continue
                trees += [c for c in device if _localname(c.tag) == "GroupObjectTree"]
    return trees


def test_group_object_tree_round_trips_from_fixture(tmp_path: Path) -> None:
    """A genuine fixture's GroupObjectTree is re-emitted with identical content (issue #14)."""
    original = _group_object_trees(_FIXTURE)
    assert original, "fixture is expected to carry a GroupObjectTree"

    xknx = tmp_path / "p.xknx"
    import_knxproj(_FIXTURE, xknx)
    out = tmp_path / "out.knxproj"
    export_knxproj(xknx, out)

    exported = _group_object_trees(out)
    # Same number of trees, and the same normalised content (GroupObjectInstances, nesting, order).
    assert sorted(map(_norm, exported)) == sorted(map(_norm, original))


def test_export_reemits_nested_channel_and_folder_nodes(tmp_path: Path) -> None:
    """A stored tree with a folder, a nested module-qualified channel and a sibling channel is
    rebuilt verbatim under the target namespace (covers folders + nesting + module-qualified RefId,
    which the minimal fixture does not exercise)."""
    stored = (
        '<GroupObjectTree GroupObjectInstances="O-0_R-1 O-1_R-2">'
        "<Nodes>"
        '<Node Type="Folder" RefId="CH-1" Text="Folder A">'
        '<Nodes><Node Type="Channel" RefId="MD-1_M-3_MI-1_CH-1" '
        'GroupObjectInstances="MD-1_M-3_MI-1_O-2-23_R-1" /></Nodes>'
        "</Node>"
        '<Node Type="Channel" RefId="CH-4" GroupObjectInstances="O-25_R-72" />'
        "</Nodes>"
        "</GroupObjectTree>"
    )

    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-GOT")
    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        if area.id == area_id
        for line in area.lines
        if line.id == line_id
    )
    svc.add_device(
        pid,
        segment_id,
        "M-1_H-1_P-1",
        address=5,
        name="Dev",
        hardware2program_ref_id="M-1_H-1_HP-1",
        # The device's com-objects back every instance the stored tree lists, as a real import
        # produces (export prunes tree instances with no backing com-object, so an unbacked tree
        # would be emptied).
        com_objects=[
            ("M-1_A-1_O-0_R-1", None),
            ("M-1_A-1_O-1_R-2", None),
            ("MD-1_M-3_MI-1_O-2-23_R-1", None),
            ("M-1_A-1_O-25_R-72", None),
        ],
    )
    svc.close(pid)

    # Stamp the captured tree onto the device directly (the editor does not edit the tree; import
    # is its only writer, mirrored here without needing a fixture that has this shape).
    engine = make_engine(url_for(src))
    try:
        with Session(engine) as session:
            device = session.query(Device).one()
            device.group_object_tree = stored
            session.commit()
    finally:
        engine.dispose()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)

    trees = _group_object_trees(out)
    assert len(trees) == 1
    # The rebuilt tree matches the stored one structurally (tags/attrs/nesting/order), and the
    # exporter has applied its namespace to every element.
    assert _norm(trees[0]) == _norm(ET.fromstring(stored))
    assert trees[0].tag.startswith("{")  # namespaced by the exporter
    nodes = [n for n in trees[0].iter() if _localname(n.tag) == "Node"]
    assert sum(n.get("Type") == "Folder" for n in nodes) == 1
    assert sum(n.get("Type") == "Channel" for n in nodes) == 2
    # Attribute ORDER must survive verbatim (ETS writes Type before RefId); _norm sorts attrs and
    # would not catch a re-ordering that breaks byte-fidelity, so assert the raw key order here.
    folder = next(n for n in nodes if n.get("Type") == "Folder")
    assert list(folder.attrib.keys()) == ["Type", "RefId", "Text"]
    channel = next(n for n in nodes if n.get("RefId") == "MD-1_M-3_MI-1_CH-1")
    assert list(channel.attrib.keys()) == ["Type", "RefId", "GroupObjectInstances"]


def test_group_object_tree_skipped_for_schema_14(tmp_path: Path) -> None:
    """project/14 has no GroupObjectTree element, so an export at that schema must omit it rather
    than emit XML the importer rejects (Codex review, issue #14)."""
    xknx = tmp_path / "p.xknx"
    import_knxproj(_FIXTURE, xknx)
    out = tmp_path / "out.knxproj"
    export_knxproj(xknx, out, schema="14")
    assert not _group_object_trees(out)


def test_import_materialises_unlinked_tree_instances(tmp_path: Path) -> None:
    """ETS writes a ComObjectInstanceRef only for linked/overridden objects; the GroupObjectTree is
    the full active set. Instances only in the tree get a com_objects row (flags = app default),
    so an active-but-unlinked channel keeps its objects and parameters."""
    xknx = tmp_path / "p.xknx"
    import_knxproj(_FIXTURE, xknx)
    with Session(make_engine(url_for(xknx))) as s:
        dev = next(d for d in s.query(Device).all() if d.address == 1)
        by_inst = {co.instance_ref_id: co for co in dev.com_objects}
    assert "O-10_R-485" in by_inst
    co = by_inst["O-10_R-485"]
    assert co.ref_id.endswith("_O-10_R-485") and co.ref_id != co.instance_ref_id
    assert co.read_flag is None and co.text_override is None
    assert co.channel_id is None  # root-level instance, no channel node


def test_export_omits_unlinked_tree_backed_com_objects(tmp_path: Path) -> None:
    """Mirror ETS: a ComObjectInstanceRef is emitted only for linked/overridden objects. The
    fixture device has 7 linked + 2 tree-only instances, so the export writes 7 COIRs (not 9) while
    the GroupObjectTree still lists all 9 (so re-import re-materialises the unlinked ones)."""
    xknx = tmp_path / "p.xknx"
    import_knxproj(_FIXTURE, xknx)
    out = tmp_path / "out.knxproj"
    export_knxproj(xknx, out)
    with zipfile.ZipFile(out) as zf:
        name = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        root = ET.fromstring(zf.read(name))
    device = next(d for d in root.iter() if _localname(d.tag) == "DeviceInstance")
    coir_refs = {
        c.get("RefId")
        for c in device.iter()
        if _localname(c.tag) == "ComObjectInstanceRef"
    }
    tree_tokens: set[str] = set()
    for e in device.iter():
        if _localname(e.tag) in ("GroupObjectTree", "Node"):
            tree_tokens.update((e.get("GroupObjectInstances") or "").split())
    assert "O-10_R-485" not in coir_refs and "O-11_R-486" not in coir_refs
    assert {"O-10_R-485", "O-11_R-486"} <= tree_tokens
    assert len(coir_refs) == 7


def test_unlinked_com_objects_from_tree_channel_inheritance_and_guards() -> None:
    """A folder nested under a channel inherits that channel; malformed XML is swallowed; and the
    already-prefixed check is boundary-safe (a different app sharing the prefix is re-qualified)."""
    from xknxeditor.proj.core.knxproj_import import _unlinked_com_objects_from_tree

    tree = (
        '<GroupObjectTree GroupObjectInstances="O-0_R-1">'
        "<Nodes>"
        '<Node Type="Channel" RefId="CH-1" GroupObjectInstances="O-1_R-1">'
        '<Nodes><Node Type="Folder" RefId="F-1" GroupObjectInstances="O-2_R-1" /></Nodes>'
        "</Node>"
        "</Nodes></GroupObjectTree>"
    )
    by_inst = {
        r.instance_ref_id: r
        for r in _unlinked_com_objects_from_tree(tree, set(), "M-1_A-1")
    }
    assert by_inst["O-0_R-1"].channel_id is None  # root -> channel-less
    assert by_inst["O-1_R-1"].channel_id == "CH-1"  # channel node
    assert by_inst["O-2_R-1"].channel_id == "CH-1"  # folder under channel inherits it
    assert by_inst["O-0_R-1"].ref_id == "M-1_A-1_O-0_R-1"

    # Malformed tree is swallowed, not raised.
    assert _unlinked_com_objects_from_tree("<GroupObjectTree", set(), "M-1_A-1") == []

    # Same app, already prefixed -> unchanged.
    same = _unlinked_com_objects_from_tree(
        '<GroupObjectTree GroupObjectInstances="M-1_A-1_O-5_R-1" />', set(), "M-1_A-1"
    )
    assert same[0].ref_id == "M-1_A-1_O-5_R-1"
    # Different app that merely shares the prefix -> re-qualified (boundary-safe).
    other = _unlinked_com_objects_from_tree(
        '<GroupObjectTree GroupObjectInstances="M-1_A-10_O-9_R-1" />', set(), "M-1_A-1"
    )
    assert other[0].ref_id.startswith("M-1_A-1_")
    assert other[0].ref_id != "M-1_A-10_O-9_R-1"
