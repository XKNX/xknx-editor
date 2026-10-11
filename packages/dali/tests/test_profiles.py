"""Tests for the Type-A DALI vendor profiles (app-id gating + object-index base)."""

from __future__ import annotations

from xknxeditor.dali import object_index, profile_for


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
