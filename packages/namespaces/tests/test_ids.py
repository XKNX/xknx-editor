"""The unpadded spelling of element ids."""

from __future__ import annotations

import pytest

from xknxeditor.namespaces import unpadded_id


@pytest.mark.parametrize(
    ("element_id", "expected"),
    [
        (
            "M-00FA_A-A012-56-5470_UP-0099811_R-009981101",
            "M-00FA_A-A012-56-5470_UP-99811_R-9981101",
        ),
        ("M-00FA_A-A012-56-5470_P-0099900", "M-00FA_A-A012-56-5470_P-99900"),
        # Already unpadded, and the manufacturer and application keep their zeros.
        (
            "M-0083_A-00B2-32-B4D6_P-23_R-1",
            "M-0083_A-00B2-32-B4D6_P-23_R-1",
        ),
        (
            "M-0083_A-00B2-32-B4D6_MD-01_M-02_MI-1_P-007_R-0012",
            "M-0083_A-00B2-32-B4D6_MD-1_M-2_MI-1_P-7_R-12",
        ),
        # A number of only zeros stays a zero.
        ("O-0_R-00", "O-0_R-0"),
        # Ids relative to the application lose the zeros of their first part too.
        ("O-01_R-0101", "O-1_R-101"),
        ("MD-01_M-3_MI-1_P-02_R-02", "MD-1_M-3_MI-1_P-2_R-2"),
        ("M-00FA", "M-00FA"),
        # Parts that are no number are left as they are.
        ("M-00FA_A-0007-01-0000_PT-0Bit", "M-00FA_A-0007-01-0000_PT-0Bit"),
    ],
)
def test_unpadded_id(element_id: str, expected: str) -> None:
    assert unpadded_id(element_id) == expected


def test_spellings_of_one_id_have_the_same_unpadded_id() -> None:
    assert unpadded_id("M-00FA_A-0001-01-0000_UP-0012_R-001201") == unpadded_id(
        "M-00FA_A-0001-01-0000_UP-12_R-1201"
    )
