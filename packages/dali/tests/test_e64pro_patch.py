"""Generic blob-patch coverage for the Theben/IPAS e64pro schema.

The e64pro document is wider than MDT's gc16 (sensor/button/general inputs, effects, nested
Instances) and we deliberately do NOT model it fully. The persist path instead parses it
generically and patches only the ECG group assignments, preserving every other element. These
tests use real e64pro blobs: two from Theben gateways and one from a genuine IPAS DaliControl
e64 Pro (manufacturer M-00EF) - the same OEM chassis byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from xknxeditor.dali.model import EcgAssignment, Scene, SceneValue
from xknxeditor.dali.profiles import backup_blob_name, profile_for
from xknxeditor.dali.project_persistence import (
    patch_assignments_in_blob,
    patch_scene_levels_in_blob,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_E64PRO_BLOBS = [
    "theben_e64pro_backup.dat",
    "theben_e64pro_backup2.dat",
    "ipas_e64pro_backup.dat",
]


@pytest.mark.parametrize("name", [*_E64PRO_BLOBS, "mdt_dali_backup_ch0_colour.dat"])
def test_scene_level_patch_preserves_colour_targets_and_metadata(name: str) -> None:
    original = (_FIXTURES / name).read_bytes()
    before = ET.fromstring(original.decode("utf-16-le"))
    scene = before.find("scenes/Scene[@index='0']")
    assert scene is not None
    scene.set("fadeTime", "7")
    scene.set("knxScene", "45")
    prefix = "Dalie64" if before.get("type") == "e64pro" else "Dali"
    first = scene.find("SceneItem[@itemindex='0']")
    if first is None:
        first = ET.SubElement(
            scene, "SceneItem", itemtype=f"{prefix}.DcaApp.Group", itemindex="0"
        )
    first.set("setting", "6")
    first.set("color-type", "2")
    first.set("color", "3000")
    original = ET.tostring(before, encoding="unicode").encode("utf-16-le")
    patched = patch_scene_levels_in_blob(
        original, [Scene(0, values=[SceneValue(0, 0, 128), SceneValue(0, 79, 255)])]
    )
    after = ET.fromstring(patched.decode("utf-16-le"))
    changed = after.find("scenes/Scene[@index='0']")
    assert changed is not None
    updated = changed.find("SceneItem[@itemindex='0']")
    assert updated is not None
    assert updated.get("value") == "50"
    assert updated.get("setting") == "4"
    assert (updated.get("color-type"), updated.get("color")) == ("2", "3000")
    added = changed.find(f"SceneItem[@itemtype='{prefix}.DcaApp.ECG'][@itemindex='63']")
    assert (
        added is not None
        and added.get("value") == "100"
        and added.get("setting") == "1"
    )
    assert (changed.get("fadeTime"), changed.get("knxScene")) == ("7", "45")
    changed.remove(added)
    first.set("value", "50")
    first.set("setting", "4")
    scene.set("isDirty", "True")
    assert ET.tostring(after) == ET.tostring(before)


@pytest.mark.parametrize("name", _E64PRO_BLOBS)
def test_patch_with_no_assignments_is_byte_exact(name: str) -> None:
    """Patching nothing reproduces the wide e64pro blob byte-for-byte."""
    original = (_FIXTURES / name).read_bytes()
    assert patch_assignments_in_blob(original, []) == original


def test_patch_sets_ecg_group_and_preserves_wide_schema() -> None:
    original = (_FIXTURES / "theben_e64pro_backup.dat").read_bytes()
    before = ET.fromstring(original.decode("utf-16-le"))
    # The wide containers that must survive untouched.
    assert before.find("mds") is not None
    assert before.find("effects") is not None
    before_mds = ET.tostring(before.find("mds"))

    patched = patch_assignments_in_blob(
        original, [EcgAssignment(slot=5, ets_index=9, group_index=3)]
    )
    after = ET.fromstring(patched.decode("utf-16-le"))
    # Logical ECG row 9 bound to physical slot 5.
    ecg9 = after.find("ecgs/ECG[@index='9']")
    assert ecg9 is not None
    assert ecg9.get("groupindex") == "3"
    assert ecg9.get("short-address") == "5"
    assert ecg9.get("flags") == "4"
    # Wide schema preserved verbatim.
    assert ET.tostring(after.find("mds")) == before_mds
    assert after.find("effects") is not None
    assert after.get("type") == "e64pro"


def test_profile_blob_names() -> None:
    theben = profile_for("M-0048_A-DA11-21-9400-O00EF")
    ipas = profile_for("M-00EF_A-DA11-22-A545-O00EF")
    mdt = profile_for("M-0083_A-0154-40-0F69-O00EF")
    assert theben is not None and theben.backup_blob_base == "Dalie64-Backup-Store"
    assert ipas is not None and ipas.key == "ipas"
    assert ipas.backup_blob_base == "Dalie64-Backup-Store"
    assert mdt is not None and mdt.backup_blob_base == "DaliGC16-Backup-Store"
    assert backup_blob_name(theben.backup_blob_base, 0) == "Dalie64-Backup-Store"
    assert backup_blob_name(mdt.backup_blob_base, 1) == "DaliGC16-Backup-Store.1"


def test_real_ipas_blob_patch_sets_assignment() -> None:
    """A genuine IPAS e64 Pro blob patches group assignments and preserves its wide schema."""
    original = (_FIXTURES / "ipas_e64pro_backup.dat").read_bytes()
    root = ET.fromstring(original.decode("utf-16-le"))
    assert root.get("type") == "e64pro"
    patched = patch_assignments_in_blob(
        original, [EcgAssignment(slot=4, ets_index=9, group_index=2)]
    )
    after = ET.fromstring(patched.decode("utf-16-le"))
    ecg9 = after.find("ecgs/ECG[@index='9']")
    assert ecg9 is not None
    assert ecg9.get("groupindex") == "2"
    assert ecg9.get("short-address") == "4"
    assert ecg9.get("flags") == "4"
    assert after.find("mds") is not None  # wide containers preserved


@pytest.mark.parametrize("schema", ["e64pro", "gc16"])
@pytest.mark.parametrize("target", [9, 255])
def test_reassignment_clears_both_old_links(schema, target):
    from xknxeditor.dali.backup_store import (
        BackupDevEcg,
        BackupStore,
        dump_backup_store,
    )
    from xknxeditor.dali.model import DaliBusState
    from xknxeditor.dali.project_persistence import merge_commissioning_blob

    store = BackupStore.defaults(individual_address="1.1.1")
    store.devecgs[5] = BackupDevEcg(5, type=8, groupindex=3, short_address=7)
    store.devecgs[6] = BackupDevEcg(6, type=8, groupindex=4, short_address=9)
    store.ecgs[7].short_address = 5
    store.ecgs[7].description = "Keep this label"
    store.ecgs[9].short_address = 6
    original = dump_backup_store(store)
    assignments = [EcgAssignment(5, target, 3)]
    if schema == "gc16":
        result = merge_commissioning_blob(
            existing=original,
            state=DaliBusState(0),
            assignments=assignments,
            scenes=[],
            individual_address="1.1.1",
        )
    else:
        root = ET.fromstring(original)
        root.set("type", schema)
        result = patch_assignments_in_blob(
            ET.tostring(root, encoding="utf-16le"), assignments
        )
    root = ET.fromstring(result)
    assert root.find("ecgs/ECG[@index='7']").get("short-address") == "255"
    assert root.find("ecgs/ECG[@index='7']").get("description") == "Keep this label"
    assert root.find("devecgs/DevECG[@index='5']").get("short-address") == str(target)
    if target == 9:
        assert root.find("devecgs/DevECG[@index='6']").get("short-address") == "255"
        assert root.find("devecgs/DevECG[@index='6']").get("groupindex") == "32"
        assert root.find("devecgs/DevECG[@index='6']").get("type") == "8"
        assert root.find("ecgs/ECG[@index='9']").get("short-address") == "5"
    else:
        assert root.find("ecgs/ECG[@index='9']").get("short-address") == "6"


@pytest.mark.parametrize("name", [*_E64PRO_BLOBS, "mdt_dali_backup_ch0_colour.dat"])
@pytest.mark.parametrize("group,flag", [(3, "4"), (16, "4"), (32, "1")])
def test_completed_assignments_store_commissioned_status(name, group, flag):
    original = (_FIXTURES / name).read_bytes()
    before = ET.fromstring(original.decode("utf-16-le"))
    changed = patch_assignments_in_blob(
        original, [EcgAssignment(slot=5, ets_index=9, group_index=group)]
    )
    after = ET.fromstring(changed.decode("utf-16-le"))
    physical = before.find("devecgs/DevECG[@index='5']")
    if physical is not None:
        physical.set("groupindex", str(group))
        physical.set("short-address", "9")
    logical = before.find("ecgs/ECG[@index='9']")
    logical.set("groupindex", str(group))
    logical.set("short-address", "5")
    logical.set("flags", flag)
    assert ET.tostring(after) == ET.tostring(before)
