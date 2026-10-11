"""Typed model for the MDT DALI gateway's bus state and the commissioning plan.

Index conventions (from the DCA, critical): a **slot** is the physical DALI short address (0-63) —
the position in the EVG_*/ComissionSync tables. The **ETS ECG number** is the logical ECG configured
in the project. ``EVG_PROP.ets_ecg_index`` / ``COMMISSION_SYNC.ets_index`` bind a physical slot to an
ETS ECG number. Group encoding: 0-15 = real group, 16 = single/ungrouped, 32 = unassigned, 255 =
missing (no ballast present).
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_ECG = 64
MAX_GROUP = 16
MAX_SCENES = 16
MAX_TEMPLATES = 16
MAX_TPL_COMMANDS = 300

GROUP_SINGLE = 16
GROUP_UNASSIGNED = 32
GROUP_MISSING = 255
SHORT_ADDR_UNASSIGNED = 255


def group_present(group_index: int) -> bool:
    """Whether a ballast is present in this slot (group != missing)."""
    return group_index != GROUP_MISSING


def group_assigned(group_index: int) -> bool:
    """Whether the slot is assigned to a real group (0-15)."""
    return 0 <= group_index < MAX_GROUP


@dataclass(slots=True)
class EcgState:
    """One physical DALI slot as read back from the gateway (EVG_PROP + STATIC + STATUS)."""

    slot: int  # physical DALI short address (0-63)
    present: bool
    ecg_type: int = -1
    sub_type: int = 0
    group_index: int = GROUP_MISSING
    ets_ecg_index: int = SHORT_ADDR_UNASSIGNED  # which ETS ECG number this slot maps to
    long_address: int = 0  # 24-bit DALI random/long address
    min_value: int = 0
    min_color_temp: int = 0
    max_color_temp: int = 0
    alarm: int = 0  # EVG_STATUS byte 1 (EcgAlarmFlags)


@dataclass(slots=True)
class GroupState:
    """One DALI group as read back (GROUP_DYN + STATUS + FAILURE)."""

    index: int  # 0-15
    run_hours: int = 0
    run_seconds: int = 0
    knx_value: int = 0
    ecg_count: int = 0
    lamp_failures: int = 0
    ecg_failures: int = 0


@dataclass(slots=True)
class DaliBusState:
    """A read-only snapshot of one DALI channel (result of a scan)."""

    channel: int
    firmware: tuple[int, int, int] | None = None
    ecgs: list[EcgState] = field(default_factory=list["EcgState"])
    groups: list[GroupState] = field(default_factory=list["GroupState"])

    @property
    def present_ecgs(self) -> list[EcgState]:
        return [e for e in self.ecgs if e.present]


@dataclass(slots=True)
class EcgAssignment:
    """A planned physical-slot -> (ETS ECG number, group) mapping, written via ComissionSync."""

    slot: int  # physical DALI short address (0-63)
    ets_index: int  # ETS ECG number bound to this slot
    group_index: int  # 0-15 group, 16 single, 32 unassigned


@dataclass(slots=True)
class SceneValue:
    """One entry in a scene: a target (group or ECG) and its level (+ optional colour bytes)."""

    scene: int  # 0-15
    target_index: int  # group 0-15, or 16 + ecg_short_address for an ECG target
    level: int  # 0-255 DALI arc value
    color: bytes = b"\x00\x00\x00\x00"  # 4-byte ColorProp
    flags: int = 0  # bit0 keep-colour, bit1 keep-value


@dataclass(slots=True)
class Scene:
    """A DALI scene: a fade time, a KNX scene number, and its per-target values."""

    index: int  # 0-15
    fade_time: int = 0
    knx_scene: int = 0  # DALI/KNX scene number 1-64 (0 = unmapped)
    values: list[SceneValue] = field(default_factory=list["SceneValue"])


@dataclass(slots=True)
class TemplateCommand:
    """One timed command of a colour-control template (an EVG effect step).

    ``value`` is the 4-byte ColorProp payload whose meaning follows ``func`` (a scalar level, a
    little-endian colour temperature, XY, RGBW or HSVW); the caller encodes it. ``weekdays`` is a
    7-bit mask (bit 0 = Monday).
    """

    func: int  # TplFunc (0-3 level, 4+ colour, CallScene, SetMaxOnValue, ...)
    hour: int
    minute: int
    value: bytes = b"\x00\x00\x00\x00"  # 4-byte ColorProp, func-dependent
    fade_time: int = 0
    weekdays: int = 0


@dataclass(slots=True)
class Template:
    """A colour-control template: its members (groups/ECGs) and its timed commands."""

    index: int  # 0-15
    mode: int = 0  # TplMode
    overwritable: bool = False
    groups: tuple[int, ...] = ()  # member group indices 0-15
    ecgs: tuple[int, ...] = ()  # member ECG indices 0-63
    commands: list[TemplateCommand] = field(default_factory=list["TemplateCommand"])
