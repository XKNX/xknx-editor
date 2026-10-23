"""MDT DALI gateway (GC16/Hawk) KNX property + command identifiers.

The gateway is commissioned over KNX Interface-Object Property access
(A_PropertyValue_Read/Write): the property id equals the configuration app's ``PropertyType``
enum value, on interface **object index ``6 + dali_channel``** (firmware is on object 0,
property 25). See :mod:`xknxeditor.dali.structs` for the per-element byte layouts.

Per-vendor byte-size overrides live on :class:`xknxeditor.dali.profiles.DaliProfile`
(``element_size_overrides``). Call :func:`element_size_for` with the active profile to
pick the right size; the module-level ``ELEMENT_SIZE`` map remains the MDT default used
when no profile is known.
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xknxeditor.dali.profiles import DaliProfile

# Interface object holding the DALI commissioning properties for channel 0; +1 per further channel.
# The base is vendor-specific (MDT = 6, Theben = 7); see :mod:`xknxeditor.dali.profiles`.
DEFAULT_OBJECT_INDEX_BASE = 6
FIRMWARE_OBJECT_INDEX = 0


def object_index(dali_channel: int = 0, base: int = DEFAULT_OBJECT_INDEX_BASE) -> int:
    """KNX interface-object index for a DALI channel's commissioning properties."""
    return base + dali_channel


class PropertyType(IntEnum):
    """KNX property ids on the gateway's DALI object."""

    HAWK_FIRMWARE = 25  # on FIRMWARE_OBJECT_INDEX (0), not the DALI object
    FIRMWARE = 65
    EVG_PROP = 203
    EVG_STATIC = 204
    EVG_STATUS = 205
    GROUP_DYN = 206
    GROUP_STATUS = 208
    GROUP_FAILURE = 209
    SCENE_ASSIGN = 212
    SCENE_GROUP_VALUE = 214
    COMMAND = 218
    COMMISSION_SYNC = 224
    COLOUR_C_EVENT = 225
    COLOUR_C_ASSIGN = 226
    EXT_COMMAND = 227
    SCENE_KNX_MAP = 228


# Bytes per table element.
ELEMENT_SIZE: dict[PropertyType, int] = {
    PropertyType.EVG_PROP: 4,
    PropertyType.EVG_STATIC: 8,
    PropertyType.EVG_STATUS: 5,
    PropertyType.GROUP_DYN: 6,
    PropertyType.GROUP_STATUS: 8,
    PropertyType.GROUP_FAILURE: 6,
    PropertyType.SCENE_ASSIGN: 1,
    PropertyType.SCENE_GROUP_VALUE: 8,
    PropertyType.SCENE_KNX_MAP: 1,
    PropertyType.COMMAND: 3,
    PropertyType.COMMISSION_SYNC: 2,
    PropertyType.COLOUR_C_EVENT: 10,
    PropertyType.COLOUR_C_ASSIGN: 10,
    PropertyType.EXT_COMMAND: 8,
}


def element_size_for(prop: PropertyType, profile: DaliProfile | None = None) -> int:
    """Element size for ``prop`` under ``profile``, with the vendor's overrides applied.

    Theben P64 V2 and IPAS DaliControl e64 Pro encode ``GROUP_STATUS`` as 10 bytes per
    element instead of the MDT default of 8; without this lookup the connection layer
    would read truncated records and the commissioning table would be interpreted wrong.
    """
    if profile is not None:
        for pid_int, size in profile.element_size_overrides:
            if pid_int == int(prop):
                return size
    return ELEMENT_SIZE[prop]


class DeviceCommand(IntEnum):
    """Device command (byte 0 of the 3-byte COMMAND property)."""

    NONE = 0
    NEW_INSTALLATION = 1
    POST_INSTALLATION = 2
    ABORT = 3
    BLINK = 4
    ASSIGN_ECG_GROUP = 5
    APPLY_SCENE_VALUE = 6
    TEST_SCENE = 7
    SET_GROUP_VALUE = 8
    SET_ALL_ECG = 10
    SET_ECG_VALUE = 11
    GET_GROUP_VALUE = 12
    SWAP_ECGS = 14
    START_STOP_EFFECT = 15
    RESET_RUN_HOURS = 16
    START_STOP_BURN_IN = 17
    UPDATE_EFFECT_TABLE = 18
    EASY_REPLACEMENT = 19
    CONVERTER_TEST = 20
    SHORT_DURATION_TEST = 21
    LONG_DURATION_TEST = 22
    BATTERY_TEST = 23
    PLUGIN_SYNC = 24
    SCENE_SYNC = 25
    RESET_DEVICE = 26
    COLOR_SYNC = 27
    REMOVE_SHORT_ADR = 28
    CONVERTER_INHIBIT = 29


class DeviceExtCommand(IntEnum):
    """DCA ``DeviceExtCommand`` (byte 0 of the 8-byte EXT_COMMAND property)."""

    NONE = 0
    SET_VALUE = 1
    SET_MIN_VALUE = 2
    SET_MAX_VALUE = 3
    COLOR_TEMP = 4
    COLOR_XY = 5
    COLOR_RGBW = 6
    COLOR_RGB = 7
    COLOR_HSV = 8
    COLOR_HSVW = 9


# Blink command Param1 bit flags (DCA CommissioningView).
BLINK_AUTO = 0x40  # start auto-blink
BLINK_STOP = 0x80  # stop blink

# Command poll readback byte0 sentinels.
POLL_DONE = 0x00  # finished OK
POLL_ERROR = 0xFF  # aborted / error
