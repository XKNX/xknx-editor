"""Typed configuration model for a Busch-Jaeger LCD display push-button.

Reproduces the data the "LCD-Verwaltungssoftware" (LVS 2) lets a user author for the triton 6323 /
6325 and the 6136UP LCD interface: per display, a set of text lines, each showing a fixed text, a
switch state, a scaled 1-byte value, a 2-byte float, or time/date, plus number formatting and
display behaviour. The field set and ranges were recovered from the decompiled LVS forms + manual;
the provenance lives in the repository's reference notes, not here.

This module is the editor-side authoring model only. Turning a :class:`DisplayDevice` into the
bytes a device expects lives in :mod:`xknxeditor.vendor.triton.codec`, whose layout is a
reconstruction and not yet verified against hardware.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# A display line is 16 characters wide; text fields are capped to that.
LINE_WIDTH = 16
MAX_NAME_LEN = 40
MAX_ADDRESS_LEN = 9
# Observed text-memory budget of a TRITON display (recomputed from the device after the first
# write); used only to drive the "x / N byte" gauge, never as a hard limit here.
TRITON_MEMORY_BUDGET = 134

_ADDRESS_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{1,3}$")


class DeviceType(Enum):
    """The kind of display being configured."""

    INTERFACE_6136UP = "6136UP"
    TRITON_6323 = "triton-6323"
    TRITON_6325 = "triton-6325"

    @property
    def is_triton(self) -> bool:
        return self in (DeviceType.TRITON_6323, DeviceType.TRITON_6325)

    @property
    def max_lines(self) -> int:
        """6136UP drives up to 8 lines; a TRITON shows up to 5 (one visible at a time)."""
        return 8 if self is DeviceType.INTERFACE_6136UP else 5

    @property
    def has_send_function(self) -> bool:
        """Only the 6136UP can send telegrams of its own."""
        return self is DeviceType.INTERFACE_6136UP


class LineDataType(Enum):
    """What a single display line shows. The ``EIS`` variants map to a KNX object."""

    FREE = "free"  # unused line, not displayed
    STATIC_TEXT = "static-text"  # fixed text, no KNX object
    SWITCH_EIS1 = "eis1-switch"  # 1 bit
    VALUE_1BYTE_EIS6 = "eis6-value"  # 1 byte, scalable
    VALUE_2BYTE_EIS5 = "eis5-float"  # 2 byte float
    TIME_EIS3 = "eis3-time"  # 3 byte time (TRITON only)
    DATE_EIS4 = "eis4-date"  # 3 byte date (TRITON only)

    @property
    def has_object(self) -> bool:
        return self not in (LineDataType.FREE, LineDataType.STATIC_TEXT)

    @property
    def is_number(self) -> bool:
        return self in (LineDataType.VALUE_1BYTE_EIS6, LineDataType.VALUE_2BYTE_EIS5)

    @property
    def is_time_or_date(self) -> bool:
        return self in (LineDataType.TIME_EIS3, LineDataType.DATE_EIS4)

    @property
    def object_size_bits(self) -> int | None:
        """Bit width of the KNX object this line reads, or ``None`` when it has no object."""
        return {
            LineDataType.SWITCH_EIS1: 1,
            LineDataType.VALUE_1BYTE_EIS6: 8,
            LineDataType.VALUE_2BYTE_EIS5: 16,
            LineDataType.TIME_EIS3: 24,
            LineDataType.DATE_EIS4: 24,
        }.get(self)

    @property
    def dpt(self) -> str | None:
        """The modern KNX DPT this EIS type corresponds to (main type), or ``None``.

        The LVS model only knows the EIS class, so the subtype is the conventional default.
        """
        return {
            LineDataType.SWITCH_EIS1: "1.001",
            LineDataType.VALUE_1BYTE_EIS6: "5.001",
            LineDataType.VALUE_2BYTE_EIS5: "9.001",
            LineDataType.TIME_EIS3: "10.001",
            LineDataType.DATE_EIS4: "11.001",
        }.get(self)


class BacklightMode(Enum):
    """Backlight behaviour. The available set is device-specific (see :func:`backlight_modes`)."""

    ALWAYS_OFF = "always-off"  # 6136UP: "immer aus"
    ALWAYS_ON = "always-on"  # both: "dauernd ein"
    ON_RECEIVE = "on-receive"  # both: "bei Empfang"
    OBJECT_CONTROLLED = (
        "object-controlled"  # device-payload only (no LVS authoring control)
    )
    LIKE_TEXT_FIELDS = "like-text-fields"  # TRITON: "wie Schriftfelder"
    ON_ALARM = "on-alarm"  # TRITON: "bei Störmeldung"


class SendModeEis1(Enum):
    """How a 6136UP send line drives a 1-bit object."""

    TOGGLE = "toggle"
    ON = "on"
    OFF = "off"


class DateOrder(Enum):
    DAY_MONTH_YEAR = "dd.mm.yy"
    MONTH_DAY_YEAR = "mm.dd.yy"


# Signal-tone durations of the alarm (Störmeldung) tab, in seconds, 0 meaning unlimited. The two
# device families offer different lists; the order mirrors the LVS drop-down (and, for a TRITON,
# the stored 3-bit index).
ALARM_TONE_DURATIONS_6136UP = (10, 30, 60, 120, 300, 600, 1800, 3600, 7200, 18000, 0)
ALARM_TONE_DURATIONS_TRITON = (1, 20, 60, 300, 600, 1800, 3600, 0)


def alarm_tone_durations(device_type: DeviceType) -> tuple[int, ...]:
    """The signal-tone durations (seconds, 0 = unlimited) a device offers on the alarm tab."""
    if device_type is DeviceType.INTERFACE_6136UP:
        return ALARM_TONE_DURATIONS_6136UP
    return ALARM_TONE_DURATIONS_TRITON


def alarm_tone_label(seconds: int) -> str:
    """A short English label for a signal-tone duration (e.g. 0 -> 'Unlimited', 60 -> '1 min')."""
    if seconds == 0:
        return "Unlimited"
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"


def backlight_modes(device_type: DeviceType) -> tuple[BacklightMode, ...]:
    """The backlight modes a device offers, in LVS drop-down order (they differ by family)."""
    if device_type.is_triton:
        return (
            BacklightMode.ALWAYS_ON,
            BacklightMode.ON_RECEIVE,
            BacklightMode.LIKE_TEXT_FIELDS,
            BacklightMode.ON_ALARM,
        )
    return (
        BacklightMode.ALWAYS_OFF,
        BacklightMode.ALWAYS_ON,
        BacklightMode.ON_RECEIVE,
    )


def device_type_from_text(text: str) -> DeviceType | None:
    """Guess the display device type from a product / hardware / order name.

    Matches the order numbers that appear in the Busch-Jaeger product names (e.g. "6931+6325
    5f-triton-Taster mit LCD"). Returns ``None`` when nothing matches.
    """
    lowered = text.lower()
    if re.search(r"(?<![0-9])6325(?![0-9])", lowered):
        return DeviceType.TRITON_6325
    if re.search(r"(?<![0-9])6323(?![0-9])", lowered):
        return DeviceType.TRITON_6323
    if re.search(r"(?<![0-9])6136\s*up(?![a-z0-9])", lowered):
        return DeviceType.INTERFACE_6136UP
    return None


def device_type_from_app_id(app_id: str) -> DeviceType | None:
    """Guess the display device type from a KNX application id's app number.

    The app id is ``M-<mfr>_A-<appnum>-<version>-<hash>``; the Busch-Jaeger / ABB LCD apps use
    the app number as the product number: ``6325`` = 5-fold, ``6323``/``6322`` = 3-fold (ABB
    numbers a 3-fold LCD as 6322). Manufacturer and application number must both match.
    Returns ``None`` for other applications.
    """
    match = re.match(r"M-(000[27])_A-(632[235])(?:-|$)", app_id, re.IGNORECASE)
    if match is None:
        return None
    manufacturer, number = match.groups()
    if manufacturer == "0007" and number == "6322":
        return None
    if number == "6325":
        return DeviceType.TRITON_6325
    return DeviceType.TRITON_6323


def data_types_for(
    device_type: DeviceType, line_index: int
) -> tuple[LineDataType, ...]:
    """The line data types valid for ``device_type`` at 1-based ``line_index``.

    Time/date are TRITON-only; on the 6136UP the last line (8) carries only 1-bit / 1-byte.
    """
    base = [
        LineDataType.FREE,
        LineDataType.STATIC_TEXT,
        LineDataType.SWITCH_EIS1,
        LineDataType.VALUE_1BYTE_EIS6,
    ]
    if not (device_type is DeviceType.INTERFACE_6136UP and line_index == 8):
        base.append(LineDataType.VALUE_2BYTE_EIS5)
    if device_type.is_triton:
        base.extend([LineDataType.TIME_EIS3, LineDataType.DATE_EIS4])
    return tuple(base)


@dataclass(slots=True)
class NumberFormat:
    """Digit layout of a numeric line (EIS 5 / EIS 6)."""

    first_digit_position: int = 1  # 1..16, first character column of the number
    integer_digits: int = 1  # 1..6
    decimal_digits: int = 0  # 0..2
    show_leading_zeros: bool = False
    extra_sign_position: bool = False  # reserve a column for the sign
    show_positive_sign: bool = False  # needs extra_sign_position
    round_last_digit_to_0_or_5: bool = False  # TRITON only


@dataclass(slots=True)
class Eis6Scaling:
    """Linear scaling of a 1-byte (EIS 6) value: display_at_0 at object 0, display_at_255 at 255."""

    display_at_0: int = 0  # -32767..32766
    display_at_255: int = 100  # -32767..32766, must be > display_at_0

    @property
    def step(self) -> float:
        """The display change per object step (read-only, like the LVS ``Abstufung`` label)."""
        return (self.display_at_255 - self.display_at_0) / 255.0


@dataclass(slots=True)
class TimeDateFormat:
    """Formatting of a time (EIS 3) or date (EIS 4) line."""

    show_weekday: bool = False
    weekday_in_english: bool = False
    date_order: DateOrder = DateOrder.DAY_MONTH_YEAR


@dataclass(slots=True)
class SendParameters:
    """User-settable send values for a 6136UP send line, by EIS type."""

    eis1_mode: SendModeEis1 | None = None
    eis5_min: float | None = None
    eis5_max: float | None = None
    eis6_min: int | None = None
    eis6_max: int | None = None


@dataclass(slots=True)
class DisplayLine:
    """One text line of a display."""

    index: int  # 1-based line number
    data_type: LineDataType = LineDataType.FREE
    text_off: str = ""  # text at state 0 / static text / the frame a number sits in
    text_on: str = ""  # text at state 1 (EIS 1 only)
    object_number: int | None = None  # linked KNX communication object (TRITON)
    send: bool = False  # 6136UP only
    receive: bool = True
    alarm: bool = False  # EIS 1 only, needs receive
    show_on_autoscroll: bool = True
    show_on_receive: bool = False
    beep_on_receive: bool = False  # EIS 1 only
    number_format: NumberFormat | None = None
    scaling: Eis6Scaling | None = None
    time_date_format: TimeDateFormat | None = None
    send_parameters: SendParameters | None = None


@dataclass(slots=True)
class DisplayDevice:
    """A full display configuration: the device and its lines."""

    device_type: DeviceType
    name: str = ""
    physical_address: str = ""
    backlight: BacklightMode = BacklightMode.ALWAYS_ON
    beep_on_keypress: bool = False
    scroll_with_key: bool = True
    key_backlight_blinks_on_alarm: bool = False  # TRITON
    # Alarm (Störmeldung) tab: signal-tone duration in seconds (0 = unlimited); whether alarm lines
    # scroll after 3 s instead of 1 s (TRITON). The duration's valid set differs per device family.
    alarm_tone_duration_s: int = 0
    alarm_scroll_after_3s: bool = False  # TRITON
    lines: list[DisplayLine] = field(default_factory=list["DisplayLine"])


def validate_line(device_type: DeviceType, line: DisplayLine) -> list[str]:
    """Human-readable problems with a line's configuration (empty list = valid)."""
    issues: list[str] = []
    if not 1 <= line.index <= device_type.max_lines:
        issues.append(
            f"Line {line.index} is out of range for this device (1..{device_type.max_lines})."
        )
    if line.data_type.is_time_or_date and not device_type.is_triton:
        issues.append("Time and date lines are only available on a triton 6323/6325.")
    if (
        device_type is DeviceType.INTERFACE_6136UP
        and line.index == 8
        and line.data_type
        not in (
            LineDataType.FREE,
            LineDataType.STATIC_TEXT,
            LineDataType.SWITCH_EIS1,
            LineDataType.VALUE_1BYTE_EIS6,
        )
    ):
        issues.append("Line 8 of a 6136UP only supports a switch or a 1-byte value.")
    if len(line.text_off) > LINE_WIDTH:
        issues.append(f"Text at off exceeds {LINE_WIDTH} characters.")
    if len(line.text_on) > LINE_WIDTH:
        issues.append(f"Text at on exceeds {LINE_WIDTH} characters.")
    if line.text_on and line.data_type is not LineDataType.SWITCH_EIS1:
        issues.append("Text at on is only used by a switch (EIS 1) line.")
    if line.object_number is not None and not 0 <= line.object_number <= 31:
        issues.append("Object number must be between 0 and 31.")
    if line.send and not device_type.has_send_function:
        issues.append("Only a 6136UP can send; this device cannot.")
    if line.alarm and line.data_type is not LineDataType.SWITCH_EIS1:
        issues.append("An alarm line must be a switch (EIS 1) line.")
    if line.alarm and not line.receive:
        issues.append("An alarm line must also receive.")
    if line.number_format is not None:
        issues.extend(_validate_number_format(device_type, line.number_format))
    if line.scaling is not None:
        if line.scaling.display_at_0 >= line.scaling.display_at_255:
            issues.append("Scaling: display at 0 must be below display at 255.")
        for name, value in (
            ("display at 0", line.scaling.display_at_0),
            ("display at 255", line.scaling.display_at_255),
        ):
            if not -32767 <= value <= 32766:
                issues.append(f"Scaling {name} must be between -32767 and 32766.")
    return issues


def _validate_number_format(device_type: DeviceType, fmt: NumberFormat) -> list[str]:
    issues: list[str] = []
    if not 1 <= fmt.first_digit_position <= LINE_WIDTH:
        issues.append(f"First digit position must be between 1 and {LINE_WIDTH}.")
    if not 1 <= fmt.integer_digits <= 6:
        issues.append("Integer digits must be between 1 and 6.")
    if not 0 <= fmt.decimal_digits <= 2:
        issues.append("Decimal digits must be between 0 and 2.")
    if fmt.show_positive_sign and not fmt.extra_sign_position:
        issues.append("Showing a positive sign needs the extra sign position enabled.")
    if fmt.round_last_digit_to_0_or_5 and not device_type.is_triton:
        issues.append("Restricting the last digit to 0 or 5 is a triton-only option.")
    return issues


def validate_device(device: DisplayDevice) -> list[str]:
    """Human-readable problems with the whole display configuration (empty list = valid)."""
    issues: list[str] = []
    if len(device.name) > MAX_NAME_LEN:
        issues.append(f"Name exceeds {MAX_NAME_LEN} characters.")
    if device.physical_address:
        if _ADDRESS_RE.fullmatch(device.physical_address) is None:
            issues.append("Physical address must look like '1.2.5'.")
        else:
            area, line, address = (int(p) for p in device.physical_address.split("."))
            if not (0 <= area <= 15 and 0 <= line <= 15 and 0 <= address <= 255):
                issues.append("Physical address must look like '1.2.5'.")
    if device.backlight not in backlight_modes(device.device_type):
        issues.append("Backlight mode is not available on this device.")
    if device.key_backlight_blinks_on_alarm and not device.device_type.is_triton:
        issues.append("Key-backlight blinking on alarm is a triton-only option.")
    if device.alarm_scroll_after_3s and not device.device_type.is_triton:
        issues.append("The alarm scroll interval is a triton-only option.")
    if device.alarm_tone_duration_s not in alarm_tone_durations(device.device_type):
        issues.append("The signal-tone duration is not available on this device.")
    seen: set[int] = set()
    for line in device.lines:
        if line.index in seen:
            issues.append(f"Line {line.index} is configured more than once.")
        seen.add(line.index)
        issues.extend(validate_line(device.device_type, line))
    return issues
