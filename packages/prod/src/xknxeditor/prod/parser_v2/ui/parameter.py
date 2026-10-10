from __future__ import annotations

from dataclasses import dataclass

from xknxeditor.namespaces.intermediate import (
    HorizontalAlignment,
    ParameterType,
    ParameterTypeTypeColor,
    ParameterTypeTypeColorSpace,
    ParameterTypeTypeDate,
    ParameterTypeTypeDateEncoding,
    ParameterTypeTypeFloat,
    ParameterTypeTypeFloatEncoding,
    ParameterTypeTypeFloatUihint,
    ParameterTypeTypeIpaddress,
    ParameterTypeTypeIpaddressAddressType,
    ParameterTypeTypeIpaddressVersion,
    ParameterTypeTypeNumber,
    ParameterTypeTypeNumberType,
    ParameterTypeTypeNumberUihint,
    ParameterTypeTypePicture,
    ParameterTypeTypeRawData,
    ParameterTypeTypeRestriction,
    ParameterTypeTypeRestrictionBase,
    ParameterTypeTypeText,
    ParameterTypeTypeTime,
    ParameterTypeTypeTimeUihint,
    ParameterTypeTypeTimeUnit,
)
from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.namespaces.intermediate.text_alignment_t import TextAlignment


@dataclass(frozen=True, slots=True)
class EnumChoice:
    value: int
    label: str
    id: str
    icon: str | None = None
    picture_alignment: HorizontalAlignment = HorizontalAlignment.LEFT
    binary_value: bytes | None = None


@dataclass(frozen=True, slots=True)
class EnumWidget:
    """Drop-down of fixed labelled values (default widget)."""

    choices: tuple[EnumChoice, ...]
    base: ParameterTypeTypeRestrictionBase
    # Two choices without a drop-down hint are shown as radio buttons.
    radio: bool = False


@dataclass(frozen=True, slots=True)
class NumberWidget:
    """Spin-box for an integer in [min, max] by step."""

    min: int
    max: int
    increment: int = 1
    type_value: ParameterTypeTypeNumberType | None = None
    display_offset: float | None = None
    display_factor: float | None = None


@dataclass(frozen=True, slots=True)
class NumberSliderWidget:
    """Slider across an integer range."""

    min: int
    max: int
    increment: int = 1
    type_value: ParameterTypeTypeNumberType | None = None
    display_offset: float | None = None
    display_factor: float | None = None


@dataclass(frozen=True, slots=True)
class FloatWidget:
    """Spin-box for a float in [min, max]."""

    min: float
    max: float
    encoding: ParameterTypeTypeFloatEncoding
    increment: float | None = None
    display_format: str | None = None
    display_offset: float | None = None
    display_factor: float | None = None


@dataclass(frozen=True, slots=True)
class FloatSliderWidget:
    """Slider across a float range."""

    min: float
    max: float
    encoding: ParameterTypeTypeFloatEncoding
    increment: float | None = None
    display_format: str | None = None
    display_offset: float | None = None
    display_factor: float | None = None


@dataclass(frozen=True, slots=True)
class CheckBoxWidget:
    """Boolean toggle stored as 0/1."""


@dataclass(frozen=True, slots=True)
class ProgressBarWidget:
    """Read-only progress within [min, max]."""

    min: int
    max: int
    display_offset: float | None = None
    display_factor: float | None = None


@dataclass(frozen=True, slots=True)
class TextWidget:
    """Plain text entry."""

    max_length: int | None = None
    pattern: str | None = None


@dataclass(frozen=True, slots=True)
class TimeWidget:
    """Time or duration entry in the given unit."""

    unit: ParameterTypeTypeTimeUnit
    min: int
    max: int
    hint: ParameterTypeTypeTimeUihint | None = None


@dataclass(frozen=True, slots=True)
class DateWidget:
    """Date picker (DPT 11); year optionally hidden."""

    encoding: ParameterTypeTypeDateEncoding
    display_the_year: bool = True


@dataclass(frozen=True, slots=True)
class IpAddressWidget:
    """IP address entry."""

    address_type: ParameterTypeTypeIpaddressAddressType
    version: ParameterTypeTypeIpaddressVersion = ParameterTypeTypeIpaddressVersion.IPV4


@dataclass(frozen=True, slots=True)
class PictureWidget:
    """Static image; ref_id targets a BinaryData entry."""

    ref_id: str
    horizontal_alignment: HorizontalAlignment = HorizontalAlignment.LEFT


@dataclass(frozen=True, slots=True)
class ColorWidget:
    """Color picker for the given space."""

    space: ParameterTypeTypeColorSpace


@dataclass(frozen=True, slots=True)
class RawDataWidget:
    """Opaque bytes up to max_size."""

    max_size: int


Widget = (
    EnumWidget
    | NumberWidget
    | NumberSliderWidget
    | FloatWidget
    | FloatSliderWidget
    | CheckBoxWidget
    | ProgressBarWidget
    | TextWidget
    | TimeWidget
    | DateWidget
    | IpAddressWidget
    | PictureWidget
    | ColorWidget
    | RawDataWidget
    | None
)


@dataclass(frozen=True, slots=True)
class UiParameter:
    ref_id: str  # ParameterRef ID, instance-qualified (state binding)
    label: str  # display text (static fallback)
    value: str  # from state, else ParameterRef.value, else ParameterBase.value
    widget: Widget  # render hint; None for TypeNone/unknown
    default_value: str = (
        ""  # application default (ParameterRef.value or ParameterBase.value)
    )
    indent_level: int = 0
    suffix: str | None = None
    access: Access = (
        Access.READ_WRITE
    )  # ParameterRef overrides merged over ParameterBase
    icon: str | None = None  # icon override per placement
    cell: str | None = None  # "row,col" for TABLE/GRID layouts
    text_alignment: TextAlignment | None = None  # value alignment, from ParameterType
    help_context: str | None = None  # page in the application's ContextHelpFile


def resolve_widget(param_type: ParameterType) -> Widget:
    """Pick the UI widget descriptor for a ParameterType."""
    match param_type.choice:
        case ParameterTypeTypeRestriction() as r if r.enumeration:
            ordered = sorted(
                r.enumeration,
                key=lambda e: e.display_order if e.display_order is not None else 0,
            )
            return EnumWidget(
                choices=tuple(
                    EnumChoice(
                        value=e.value,
                        label=e.text or str(e.value),
                        id=e.id,
                        icon=e.icon,
                        picture_alignment=e.picture_alignment,
                        binary_value=e.binary_value,
                    )
                    for e in ordered
                ),
                base=r.base,
                radio=len(ordered) == 2 and r.uihint is None,
            )
        case ParameterTypeTypeRestriction() as r:
            return NumberWidget(min=0, max=2**r.size_in_bit - 1)
        case ParameterTypeTypeNumber() as n if (
            n.uihint is ParameterTypeTypeNumberUihint.SLIDER
        ):
            return NumberSliderWidget(
                min=n.min_inclusive,
                max=n.max_inclusive,
                increment=n.increment,
                type_value=n.type_value,
                display_offset=n.display_offset,
                display_factor=n.display_factor,
            )
        case ParameterTypeTypeNumber() as n if (
            n.uihint is ParameterTypeTypeNumberUihint.CHECK_BOX
        ):
            return CheckBoxWidget()
        case ParameterTypeTypeNumber() as n if (
            n.uihint is ParameterTypeTypeNumberUihint.PROGRESS_BAR
        ):
            return ProgressBarWidget(
                min=n.min_inclusive,
                max=n.max_inclusive,
                display_offset=n.display_offset,
                display_factor=n.display_factor,
            )
        case ParameterTypeTypeNumber() as n:
            return NumberWidget(
                min=n.min_inclusive,
                max=n.max_inclusive,
                increment=n.increment,
                type_value=n.type_value,
                display_offset=n.display_offset,
                display_factor=n.display_factor,
            )
        case ParameterTypeTypeFloat() as f if (
            f.uihint is ParameterTypeTypeFloatUihint.SLIDER
        ):
            return FloatSliderWidget(
                min=f.min_inclusive,
                max=f.max_inclusive,
                encoding=f.encoding,
                increment=f.increment,
                display_format=f.display_format,
                display_offset=f.display_offset,
                display_factor=f.display_factor,
            )
        case ParameterTypeTypeFloat() as f:
            return FloatWidget(
                min=f.min_inclusive,
                max=f.max_inclusive,
                encoding=f.encoding,
                increment=f.increment,
                display_format=f.display_format,
                display_offset=f.display_offset,
                display_factor=f.display_factor,
            )
        case ParameterTypeTypeText() as t:
            return TextWidget(max_length=t.size_in_bit // 8, pattern=t.pattern)
        case ParameterTypeTypeTime() as t:
            return TimeWidget(
                unit=t.unit, min=t.min_inclusive, max=t.max_inclusive, hint=t.uihint
            )
        case ParameterTypeTypeDate() as d:
            return DateWidget(encoding=d.encoding, display_the_year=d.display_the_year)
        case ParameterTypeTypeIpaddress() as ip:
            return IpAddressWidget(address_type=ip.address_type, version=ip.version)
        case ParameterTypeTypePicture() as p:
            return PictureWidget(
                ref_id=p.ref_id, horizontal_alignment=p.horizontal_alignment
            )
        case ParameterTypeTypeColor() as c:
            return ColorWidget(space=c.space)
        case ParameterTypeTypeRawData() as r:
            return RawDataWidget(max_size=r.max_size)
        case _:
            return None
