"""Tests for the Type-A DALI vendor profiles (app-id gating + object-index base)."""

from __future__ import annotations

import pytest

from xknxeditor.dali import object_index, profile_for

LOAD_PROCEDURES = """<LoadProcedures>
<LdCtrlLoadImageProp ObjIdx="1" />
<LdCtrlLoadImageProp ObjIdx="2" />
<LdCtrlLoadImageProp ObjIdx="3" />
<LdCtrlLoadImageProp ObjIdx="4" />
<LdCtrlWriteProp ObjIdx="7" />
</LoadProcedures>"""


def test_profile_matches_mdt_and_theben() -> None:
    mdt = profile_for("M-0083_A-0154-40-0F69-O00EF")
    assert mdt is not None and mdt.key == "mdt" and mdt.object_index_base == 6

    theben = profile_for("M-0048_A-DA11-21-9400-O00EF")
    assert (
        theben is not None and theben.key == "theben" and theben.object_index_base == 7
    )


def test_profile_none_for_other_apps() -> None:
    assert profile_for("M-0083_A-9999-40") is None  # other MDT app
    assert profile_for("M-0001_A-0248") is None  # Siemens DALI2 (not Type-A)
    assert profile_for(None) is None


def test_object_index_uses_base_and_channel() -> None:
    assert object_index(0, base=6) == 6  # MDT channel 0
    assert object_index(1, base=6) == 7  # MDT channel 1
    assert object_index(0, base=7) == 7  # Theben channel 0
    assert object_index(1, base=7) == 8  # Theben channel 1


@pytest.mark.parametrize(
    "app_id,key,base,stride,fade,extended",
    [
        ("M-0083_A-0153-21-8829-O00EF", "mdt", 6, 8, 212, False),
        ("M-0048_A-DA11-21-9400-O00EF", "theben", 7, 10, 213, True),
        ("M-00EF_A-DA11-22-A545", "ipas", 7, 10, 213, True),
    ],
)
@pytest.mark.parametrize("xml", [LOAD_PROCEDURES, LOAD_PROCEDURES.encode()])
def test_shared_load_procedures_preserve_vendor_protocol(
    app_id, key, base, stride, fade, extended, xml
):
    profile = profile_for(app_id, application_xml=xml)
    assert profile is not None
    assert (profile.key, profile.object_index_base) == (key, base)
    assert dict(profile.element_size_overrides).get(208, 8) == stride
    assert dict(profile.property_id_overrides).get(212, 212) == fade
    assert profile.supports_extended_templates is extended


@pytest.mark.parametrize(
    "app_id", [None, "M-0083_A-9999-21-1234", "M-0008_A-C030-21-AA50-O000A"]
)
def test_load_procedures_alone_do_not_enable_an_unverified_protocol(app_id):
    assert profile_for(app_id, application_xml=LOAD_PROCEDURES) is None
