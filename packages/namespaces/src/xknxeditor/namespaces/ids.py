"""Spellings of KNX element ids."""

from __future__ import annotations

import re

# The leading zeros of the number of an id part (``UP-0099811``, ``R-009981101``), except those
# of the manufacturer an id starts with (``M-00FA``).
_PADDED_NUMBER = re.compile(
    r"(?:(?<=_)|^(?!M-[0-9A-F]{4}(?:_|$)))([A-Z]+-)0+(?=[0-9A-F]+(?:_|$))"
)


def unpadded_id(element_id: str) -> str:
    """``element_id`` with the numbers of its parts written without leading zeros.

    An application may write the numbers of its ids with leading zeros
    (``M-00FA_A-A012-56-5470_UP-0099811_R-009981101``); a project stores the values of such a
    parameter under the same id with the numbers written without them
    (``M-00FA_A-A012-56-5470_UP-99811_R-9981101``). Two spellings of an id name the same element
    when their unpadded ids are equal. The manufacturer (``M-00FA``) and the application
    (``A-A012-56-5470``) are left as they are, and so is a number that is only zeros
    (``O-0``); an id relative to the application (``O-01_R-0101``) loses the zeros of its first
    part too."""
    if "-0" not in element_id:
        return element_id
    return _PADDED_NUMBER.sub(r"\1", element_id)
