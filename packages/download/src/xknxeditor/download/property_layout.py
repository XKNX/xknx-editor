"""Property element widths used by load controls and property writes (KNX Resources)."""

from __future__ import annotations

from xknx.profile.const import PropertyDataType

from xknxeditor.namespaces.intermediate.prop_type_t import PropType

from .errors import UnsupportedProcedureError

# PID5 has asymmetric read/write widths; never infer its write size from a read.
STANDARD_WRITE_WIDTHS = {1: 2, 5: 10, 13: 5, 14: 1, 27: 8, 56: 2}
# PDT_CONTROL is 1 octet read and 10 octets written (KNX v01.04.01 - Standardized
# Identifier Tables 03.07.03 - §4.1 Table 6); the widths here are those of writes.
_CONTROL_WRITE_WIDTH = 10


def property_width(data_type: PropType | int) -> int:
    """The element size of a declared PDT, or of the type octet in an
    A_PropertyDescription_Response - KNX v01.04.01 - Standardized Identifier Tables
    03.07.03 - §4.1 Table 6."""
    try:
        pdt = (
            PropertyDataType[data_type.name]
            if isinstance(data_type, PropType)
            else PropertyDataType(data_type & 0x3F)
        )
    except (KeyError, ValueError):
        pdt = None
    if pdt is PropertyDataType.PDT_CONTROL:
        return _CONTROL_WRITE_WIDTH
    size = pdt.size if pdt is not None else None
    if size is None:
        raise UnsupportedProcedureError(
            f"unsupported property element type {data_type}"
        )
    return size
