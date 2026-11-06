"""Busch-Jaeger triton 6323/6325 (and 6136UP) LCD display text configuration.

Reproduces the "LCD-Verwaltungssoftware" (LVS 2): author the per-line display configuration
(:mod:`~xknxeditor.vendor.triton.model`) and encode it to the device blob
(:mod:`~xknxeditor.vendor.triton.codec`). The device byte layout is a reconstruction and is NOT
yet verified against hardware (see the codec's ``EXPERIMENTAL`` note).
"""

from xknxeditor.vendor.triton.codec import (
    EXPERIMENTAL,
    TritonCodecError,
    decode_display,
    encode_display,
)
from xknxeditor.vendor.triton.model import (
    LINE_WIDTH,
    MAX_ADDRESS_LEN,
    MAX_NAME_LEN,
    TRITON_MEMORY_BUDGET,
    BacklightMode,
    DateOrder,
    DeviceType,
    DisplayDevice,
    DisplayLine,
    Eis6Scaling,
    LineDataType,
    NumberFormat,
    SendModeEis1,
    SendParameters,
    TimeDateFormat,
    alarm_tone_durations,
    alarm_tone_label,
    backlight_modes,
    data_types_for,
    device_type_from_app_id,
    device_type_from_text,
    validate_device,
    validate_line,
)
from xknxeditor.vendor.triton.prj import (
    PrjError,
    blank_prj,
    read_prj,
    write_prj,
)
from xknxeditor.vendor.triton.serialize import (
    SCHEMA_VERSION,
    default_device,
    device_from_dict,
    device_to_dict,
    from_json_bytes,
    to_json_bytes,
)

__all__ = [
    "EXPERIMENTAL",
    "LINE_WIDTH",
    "MAX_ADDRESS_LEN",
    "MAX_NAME_LEN",
    "SCHEMA_VERSION",
    "TRITON_MEMORY_BUDGET",
    "BacklightMode",
    "DateOrder",
    "DeviceType",
    "DisplayDevice",
    "DisplayLine",
    "Eis6Scaling",
    "LineDataType",
    "NumberFormat",
    "PrjError",
    "SendModeEis1",
    "SendParameters",
    "TimeDateFormat",
    "TritonCodecError",
    "alarm_tone_durations",
    "alarm_tone_label",
    "backlight_modes",
    "blank_prj",
    "data_types_for",
    "decode_display",
    "default_device",
    "device_from_dict",
    "device_to_dict",
    "device_type_from_app_id",
    "device_type_from_text",
    "encode_display",
    "from_json_bytes",
    "read_prj",
    "to_json_bytes",
    "validate_device",
    "validate_line",
    "write_prj",
]
