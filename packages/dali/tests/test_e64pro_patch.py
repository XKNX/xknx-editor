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

from xknxeditor.dali.model import EcgAssignment
from xknxeditor.dali.profiles import backup_blob_name, profile_for
from xknxeditor.dali.project_persistence import patch_assignments_in_blob

_FIXTURES = Path(__file__).parent / "fixtures"
_E64PRO_BLOBS = [
    "theben_e64pro_backup.dat",
    "theben_e64pro_backup2.dat",
    "ipas_e64pro_backup.dat",
]


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
    assert ecg9.get("flags") == "2"
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
    assert ecg9.get("flags") == "2"
    assert after.find("mds") is not None  # wide containers preserved
