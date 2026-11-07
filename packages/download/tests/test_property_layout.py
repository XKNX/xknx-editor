"""Property element widths: KNX v01.04.01 - Standardized Identifier Tables 03.07.03 -
§4.1 Table 6, with PDT_CONTROL's write width."""

from __future__ import annotations

import pytest

from xknxeditor.download.errors import UnsupportedProcedureError
from xknxeditor.download.property_layout import property_width
from xknxeditor.namespaces.intermediate.prop_type_t import PropType


@pytest.mark.parametrize(
    ("data_type", "width"),
    [
        (PropType.PDT_CONTROL, 10),
        (PropType.PDT_UNSIGNED_INT, 2),
        (PropType.PDT_CHAR_BLOCK, 10),
        (PropType.PDT_GENERIC_01, 1),
        (PropType.PDT_GENERIC_20, 20),
        (PropType.PDT_VERSION, 2),
        (PropType.PDT_ALARM_INFO, 6),
        (PropType.PDT_SCALING, 1),
    ],
)
def test_declared_types(data_type: PropType, width: int) -> None:
    assert property_width(data_type) == width


@pytest.mark.parametrize(
    ("code", "width"),
    [
        (0x00, 10),  # PDT_CONTROL
        (0x04, 2),  # PDT_UNSIGNED_INT
        (0x11, 1),  # PDT_GENERIC_01
        (0x24, 20),  # PDT_GENERIC_20
        (0x30, 2),  # PDT_VERSION
        (0x36, 1),  # PDT_SCALING
        # the write-enable bit of a description response's type octet is ignored
        (0x84, 2),
    ],
)
def test_description_type_octets(code: int, width: int) -> None:
    assert property_width(code) == width


@pytest.mark.parametrize(
    "data_type", [PropType.PDT_VARIABLE_LENGTH, PropType.PDT_UTF_8, 0x10, 0x26, 0x3E]
)
def test_types_without_a_fixed_width(data_type: PropType | int) -> None:
    with pytest.raises(UnsupportedProcedureError, match="unsupported property element"):
        property_width(data_type)
