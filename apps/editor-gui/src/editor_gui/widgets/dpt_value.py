"""Value input for a group address, shaped by its data point type.

Bit and enumeration types offer their values (texts from the KNX master data where present):
radio buttons for two values, a drop-down for more. Numbers get a text field checked against the
type's range, text types a field limited to their length, and compound types (time, date, colour,
dimming step, ...) one input per field.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from imgui_bundle import imgui
from xknx.dpt.dpt import DPTBase, DPTComplex, DPTEnum, DPTNumeric
from xknx.dpt.dpt_9 import DPT2ByteFloat
from xknx.dpt.dpt_14 import DPT4ByteFloat
from xknx.dpt.dpt_16 import DPTString
from xknx.dpt.payload import DPTArray, DPTBinary

from editor_gui.dpt import dpt_from_token, transcoder_for
from editor_gui.dpt_formats import BitFormat, DptFormat, EnumFormat, NumberFormat
from editor_gui.regional import regional_format
from editor_gui.widgets.dpi import px
from editor_gui.widgets.float_format import general_float
from editor_gui.widgets.strings import S

Payload = DPTBinary | DPTArray


@dataclass(frozen=True)
class GroupValueBus:
    """What a panel needs to read and write group values on the bus."""

    connected: Callable[[], bool]
    read: Callable[[str], object]
    write: Callable[[str, Payload], object]
    formats: Callable[[], Mapping[str, DptFormat]]


def dpt_label(token: str | None) -> str:
    """Number and name of a DPT, e.g. ``1.001 Switch``; empty without one."""
    dpt = dpt_from_token(token)
    if not dpt.major:
        return ""
    code = dpt.code if dpt.has_subtype else f"{dpt.major}.xxx"
    return f"{code} {dpt.name}".strip()


def _title(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _number_text(value: float) -> str:
    return general_float(value).replace(".", regional_format().decimal)


def _range_hint(minimum: float | None, maximum: float | None) -> str:
    """``min … max`` as an input hint; empty for a type without finite limits."""
    if minimum is None or maximum is None:
        return ""
    if not (math.isfinite(minimum) and math.isfinite(maximum)):
        return ""
    return f"{_number_text(minimum)} … {_number_text(maximum)}"


def _parse_number(text: str) -> int | float | None:
    regional = regional_format()
    cleaned = text.strip().replace(" ", "").replace("\xa0", "")
    if regional.decimal in cleaned:
        if regional.group != regional.decimal:
            cleaned = cleaned.replace(regional.group, "")
        cleaned = cleaned.replace(regional.decimal, ".")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return int(cleaned)
    except ValueError:
        pass
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _raw_payload(transcoder: type[DPTBase] | None, main: int, raw: int) -> Payload:
    """``raw`` as the payload of the type: a few bits, or ``payload_length`` octets."""
    if transcoder is not None:
        if getattr(transcoder, "payload_type", None) is DPTBinary:
            return DPTBinary(raw)
        return DPTArray(raw.to_bytes(transcoder.payload_length, "big"))
    if main in (1, 2, 3, 23) and raw <= DPTBinary.APCI_BITMASK:
        return DPTBinary(raw)
    return DPTArray(raw.to_bytes(max(1, (raw.bit_length() + 7) // 8), "big"))


class _Editor:
    def render(self, widget_id: str, *, inline: bool) -> bool:
        raise NotImplementedError

    def payload(self) -> Payload:
        raise NotImplementedError

    def set_payload(self, payload: Payload) -> None:
        return None


@dataclass
class _Choice(_Editor):
    options: list[tuple[str, Payload]]
    selected: int = 0

    def render(self, widget_id: str, *, inline: bool) -> bool:
        if len(self.options) == 2:
            for index, (label, _) in enumerate(self.options):
                if index:
                    imgui.same_line()
                if imgui.radio_button(
                    f"{label}##{widget_id}_{index}", self.selected == index
                ):
                    self.selected = index
            return False
        imgui.set_next_item_width(px(170.0 if inline else 260.0))
        current = self.options[self.selected][0] if self.options else ""
        if imgui.begin_combo(f"##{widget_id}", current):
            for index, (label, _) in enumerate(self.options):
                if imgui.selectable(f"{label}##{index}", self.selected == index)[0]:
                    self.selected = index
            imgui.end_combo()
        return False

    def payload(self) -> Payload:
        return self.options[self.selected][1]

    def set_payload(self, payload: Payload) -> None:
        for index, (_, option) in enumerate(self.options):
            if option.value == payload.value:
                self.selected = index


@dataclass
class _Number(_Editor):
    transcoder: type[DPTNumeric]
    unit: str
    integer: bool
    text: str = ""

    def render(self, widget_id: str, *, inline: bool) -> bool:
        hint = _range_hint(self.transcoder.value_min, self.transcoder.value_max)
        imgui.set_next_item_width(px(140.0 if inline else 200.0))
        submitted, self.text = imgui.input_text_with_hint(
            f"##{widget_id}", hint, self.text, imgui.InputTextFlags_.enter_returns_true
        )
        if self.unit:
            imgui.same_line()
            imgui.text_disabled(self.unit)
        return submitted

    def payload(self) -> Payload:
        value = _parse_number(self.text)
        if value is None:
            raise ValueError(S.DPT_VALUE_NOT_A_NUMBER)
        if self.integer and isinstance(value, float):
            if not value.is_integer():
                raise ValueError(S.DPT_VALUE_WHOLE_NUMBER)
            value = int(value)
        minimum, maximum = self.transcoder.value_min, self.transcoder.value_max
        if not minimum <= value <= maximum:
            raise ValueError(
                S.DPT_VALUE_RANGE.format(
                    minimum=_number_text(minimum), maximum=_number_text(maximum)
                )
            )
        return self.transcoder.to_knx(value)

    def set_payload(self, payload: Payload) -> None:
        value = self.transcoder.from_knx(payload)
        self.text = (
            _number_text(float(value)) if isinstance(value, float) else str(value)
        )


@dataclass
class _Text(_Editor):
    transcoder: type[DPTBase]
    length: int
    text: str = ""

    def render(self, widget_id: str, *, inline: bool) -> bool:
        imgui.set_next_item_width(px(180.0 if inline else 260.0))
        submitted, self.text = imgui.input_text(
            f"##{widget_id}", self.text, imgui.InputTextFlags_.enter_returns_true
        )
        return submitted

    def payload(self) -> Payload:
        if len(self.text) > self.length:
            raise ValueError(S.DPT_VALUE_TOO_LONG.format(count=self.length))
        return self.transcoder.to_knx(self.text)

    def set_payload(self, payload: Payload) -> None:
        self.text = str(self.transcoder.from_knx(payload))


@dataclass
class _Free(_Editor):
    """Any other type xknx encodes: the text is read as on/off, a number or text."""

    transcoder: type[DPTBase]
    text: str = ""

    def render(self, widget_id: str, *, inline: bool) -> bool:
        imgui.set_next_item_width(px(160.0 if inline else 260.0))
        submitted, self.text = imgui.input_text(
            f"##{widget_id}", self.text, imgui.InputTextFlags_.enter_returns_true
        )
        return submitted

    def payload(self) -> Payload:
        text = self.text.strip()
        value: Any = text
        if text.lower() in ("on", "true", "yes"):
            value = True
        elif text.lower() in ("off", "false", "no"):
            value = False
        else:
            number = _parse_number(text)
            if number is not None:
                value = number
        try:
            return self.transcoder.to_knx(value)
        except Exception as exc:  # xknx raises ConversionError/ValueError on bad input
            raise ValueError(S.DPT_VALUE_INVALID) from exc

    def set_payload(self, payload: Payload) -> None:
        value = self.transcoder.from_knx(payload)
        if isinstance(value, bool):
            self.text = "on" if value else "off"
        elif isinstance(value, float):
            self.text = _number_text(value)
        else:
            self.text = str(value)


@dataclass
class _Field:
    name: str
    kind: str
    required: bool
    minimum: float | None = None
    maximum: float | None = None
    options: list[str] = field(default_factory=list[str])
    text: str = ""
    selected: int = 0
    checked: bool = False


@dataclass
class _Fields(_Editor):
    transcoder: type[DPTComplex[Any]]
    fields: list[_Field]

    def render(self, widget_id: str, *, inline: bool) -> bool:
        submitted = False
        label_w = (
            0.0
            if inline
            else max(imgui.calc_text_size(_title(f.name)).x for f in self.fields)
        )
        for index, item in enumerate(self.fields):
            if inline and index:
                imgui.same_line()
            imgui.align_text_to_frame_padding()
            imgui.text_disabled(_title(item.name))
            imgui.same_line(0.0 if inline else label_w + px(16.0))
            item_id = f"##{widget_id}_{item.name}"
            if item.kind == "enum":
                options = item.options if item.required else ["-", *item.options]
                imgui.set_next_item_width(px(110.0 if inline else 180.0))
                if imgui.begin_combo(item_id, _title(options[item.selected])):
                    for option_index, option in enumerate(options):
                        if imgui.selectable(
                            _title(option), item.selected == option_index
                        )[0]:
                            item.selected = option_index
                    imgui.end_combo()
            elif item.kind == "boolean":
                item.checked = imgui.checkbox(item_id, item.checked)[1]
            else:
                hint = _range_hint(item.minimum, item.maximum)
                imgui.set_next_item_width(px(60.0 if inline else 180.0))
                done, item.text = imgui.input_text_with_hint(
                    item_id, hint, item.text, imgui.InputTextFlags_.enter_returns_true
                )
                submitted = submitted or done
        return submitted

    def payload(self) -> Payload:
        values: dict[str, Any] = {}
        for item in self.fields:
            label = _title(item.name)
            if item.kind == "enum":
                options = item.options if item.required else [None, *item.options]
                if options[item.selected] is not None:
                    values[item.name] = options[item.selected]
            elif item.kind == "boolean":
                values[item.name] = item.checked
            elif item.text.strip():
                value = _parse_number(item.text)
                if value is None:
                    raise ValueError(f"{label}: {S.DPT_VALUE_NOT_A_NUMBER}")
                if item.kind == "integer":
                    if isinstance(value, float) and not value.is_integer():
                        raise ValueError(f"{label}: {S.DPT_VALUE_WHOLE_NUMBER}")
                    value = int(value)
                if (item.minimum is not None and value < item.minimum) or (
                    item.maximum is not None and value > item.maximum
                ):
                    raise ValueError(
                        f"{label}: "
                        + S.DPT_VALUE_RANGE.format(
                            minimum=_number_text(item.minimum or 0),
                            maximum=_number_text(item.maximum or 0),
                        )
                    )
                values[item.name] = value
            elif item.required:
                raise ValueError(f"{label}: {S.DPT_VALUE_REQUIRED}")
        try:
            return self.transcoder.to_knx(values)
        except Exception as exc:  # xknx raises ConversionError for inconsistent fields
            raise ValueError(S.DPT_VALUE_INVALID) from exc

    def set_payload(self, payload: Payload) -> None:
        data = cast("dict[str, Any]", self.transcoder.from_knx(payload).as_dict())
        for item in self.fields:
            value = data.get(item.name)
            if item.kind == "enum":
                options: list[str | None] = (
                    [*item.options] if item.required else [None, *item.options]
                )
                if value in options:
                    item.selected = options.index(cast("str | None", value))
            elif item.kind == "boolean":
                item.checked = bool(value)
            elif value is not None:
                item.text = (
                    _number_text(value) if isinstance(value, float) else str(value)
                )


def _complex_fields(transcoder: type[DPTComplex[Any]]) -> list[_Field] | None:
    fields: list[_Field] = []
    for spec in cast("list[dict[str, Any]]", transcoder.get_dict_schema()):
        kind = spec.get("type")
        if kind not in ("integer", "float", "enum", "boolean"):
            return None
        item = _Field(
            name=str(spec["name"]),
            kind=str(kind),
            required=bool(spec.get("required")),
            minimum=spec.get("value_min"),
            maximum=spec.get("value_max"),
            options=[str(option) for option in spec.get("options", [])],
        )
        default = spec.get("default")
        if item.kind == "enum" and default is not None:
            options = item.options if item.required else [None, *item.options]
            if default in options:
                item.selected = options.index(default)
        elif item.kind == "boolean":
            item.checked = bool(default)
        fields.append(item)
    return fields


def _enum_label(member: enum.Enum) -> str:
    return member.name.replace("_", " ").title()


def _editor_for(token: str | None, formats: Mapping[str, DptFormat]) -> _Editor | None:
    dpt = dpt_from_token(token)
    if not dpt.major:
        return None
    transcoder = transcoder_for(token)
    fmt = formats.get(f"DPST-{dpt.major}-{dpt.minor}") if dpt.has_subtype else None
    if isinstance(fmt, BitFormat):
        return _Choice(
            [
                (fmt.cleared, _raw_payload(transcoder, dpt.major, 0)),
                (fmt.set, _raw_payload(transcoder, dpt.major, 1)),
            ]
        )
    if isinstance(fmt, EnumFormat) and fmt.texts:
        return _Choice(
            [
                (text, _raw_payload(transcoder, dpt.major, raw))
                for raw, text in sorted(fmt.texts.items())
            ]
        )
    if transcoder is None:
        return None
    if issubclass(transcoder, DPTEnum):
        enum_transcoder = cast("type[DPTEnum[Any]]", transcoder)
        members = cast("list[enum.Enum]", enum_transcoder.get_valid_values())
        return _Choice([(_enum_label(m), enum_transcoder.to_knx(m)) for m in members])
    if issubclass(transcoder, DPTNumeric):
        unit = (
            (fmt.unit if isinstance(fmt, NumberFormat) else "") or transcoder.unit or ""
        )
        integer = not issubclass(transcoder, (DPT2ByteFloat, DPT4ByteFloat))
        return _Number(transcoder, unit, integer)
    if issubclass(transcoder, DPTString) or transcoder.__name__.startswith(
        "DPTCharacter"
    ):
        return _Text(transcoder, transcoder.payload_length)
    if issubclass(transcoder, DPTComplex):
        complex_transcoder = cast("type[DPTComplex[Any]]", transcoder)
        fields = _complex_fields(complex_transcoder)
        if fields:
            return _Fields(complex_transcoder, fields)
    return _Free(cast("type[DPTBase]", transcoder))


@dataclass
class DptValueInput:
    """Keeps the entered value of one input; the editor follows the DPT shown."""

    widget_id: str
    _key: tuple[str | None, int] | None = None
    _editor: _Editor | None = None
    error: str = ""

    def _sync(self, token: str | None, formats: Mapping[str, DptFormat]) -> None:
        key = (token, id(formats))
        if key != self._key:
            self._key = key
            self._editor = _editor_for(token, formats)
            self.error = ""

    def reset(self) -> None:
        """Start empty the next time it is shown."""
        self._key = None
        self._editor = None
        self.error = ""

    def can_write(self, token: str | None, formats: Mapping[str, DptFormat]) -> bool:
        self._sync(token, formats)
        return self._editor is not None

    def render(
        self,
        token: str | None,
        formats: Mapping[str, DptFormat],
        *,
        inline: bool = False,
    ) -> bool:
        """Draw the input; ``True`` when Enter was pressed in one of its text fields."""
        self._sync(token, formats)
        if self._editor is None:
            imgui.text_disabled(S.DPT_VALUE_UNSUPPORTED)
            return False
        return self._editor.render(self.widget_id, inline=inline)

    def payload(self) -> Payload | None:
        """The entered value encoded, or ``None`` with :attr:`error` set."""
        if self._editor is None:
            self.error = S.DPT_VALUE_UNSUPPORTED
            return None
        try:
            payload = self._editor.payload()
        except ValueError as exc:
            self.error = str(exc)
            return None
        self.error = ""
        return payload

    def set_payload(
        self, token: str | None, formats: Mapping[str, DptFormat], payload: Payload
    ) -> None:
        """Show ``payload`` (e.g. a value seen on the bus) as the entered value."""
        self._sync(token, formats)
        if self._editor is None:
            return
        try:
            self._editor.set_payload(payload)
        except Exception:
            return
