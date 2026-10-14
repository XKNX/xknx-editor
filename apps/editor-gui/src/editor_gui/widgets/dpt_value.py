"""Value input for a group address, shaped by its data point type.

Bit and enumeration types offer their values (texts from the KNX master data where present):
radio buttons for two values, a drop-down for more. Numbers get a text field checked against the
type's range, text types a field limited to their length, and compound types (time, date, colour,
dimming step, ...) one input per field.
"""

from __future__ import annotations

import copy
import enum
import math
import re
import struct
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
from editor_gui.dpt_formats import (
    BitFormat,
    CompositeFormat,
    DptFormat,
    EnumFormat,
    FieldFormat,
    NumberFormat,
)
from editor_gui.regional import regional_format
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
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
    names: Callable[[], Mapping[str, str]] = dict


def dpt_label(token: str | None, names: Mapping[str, str] | None = None) -> str:
    """Number and name of a DPT, e.g. ``1.001 Switch``; empty without one. ``names`` are the
    names of the KNX master data by ``DPST-x-y`` / ``DPT-x``, used when present."""
    dpt = dpt_from_token(token)
    if not dpt.major:
        return ""
    code = dpt.code if dpt.has_subtype else f"{dpt.major}.xxx"
    key = f"DPST-{dpt.major}-{dpt.minor}" if dpt.has_subtype else f"DPT-{dpt.major}"
    name = (names or {}).get(key)
    return f"{code} {title_case(name) if name else dpt.name}".strip()


_MINOR_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "from",
        "in",
        "into",
        "nor",
        "of",
        "on",
        "or",
        "per",
        "the",
        "to",
        "via",
        "vs",
        "with",
    ]
)


def title_case(text: str) -> str:
    """``text`` as a title: major words capitalized, short minor words lower case except
    first; ``SceneNumber`` is split into words, abbreviations (``DHW``) are kept."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text).split()
    result: list[str] = []
    for index, word in enumerate(words):
        if any(c.isupper() for c in word) or (index and word.lower() in _MINOR_WORDS):
            result.append(word)
        else:
            result.append(word[:1].upper() + word[1:])
    return " ".join(result)


def _title(name: str) -> str:
    """A field name of xknx (``step_code``) as a label (``Step Code``)."""
    return title_case(name.replace("_", " ").strip())


def value_text(text: str) -> str:
    """A value or option text starting upper case (``not accepted`` -> ``Not accepted``);
    the rest is kept as it is."""
    text = text.strip()
    return text[:1].upper() + text[1:]


def _option_text(name: str) -> str:
    """An option value of xknx (``no_day``) as shown (``No day``)."""
    return value_text(name.replace("_", " "))


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
        if inline and len(self.fields) > 3:
            return _edit_in_dialog(
                widget_id, self.fields, lambda: self.render(widget_id, inline=False)
            )
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
                if imgui.begin_combo(item_id, _option_text(options[item.selected])):
                    for option_index, option in enumerate(options):
                        if imgui.selectable(
                            _option_text(option), item.selected == option_index
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


# Field values when an "Edit Value" dialog opened, by widget id, restored on Cancel.
_saved_fields: dict[str, list[Any]] = {}


def _edit_in_dialog(
    widget_id: str, fields: list[Any], render: Callable[[], bool]
) -> bool:
    """A button that opens the fields of a compound value in a modal dialog, for a one-line
    bar. OK (or Enter) keeps the entered values, Cancel (or Escape) restores the previous."""
    title = f"{S.DPT_VALUE_EDIT_TITLE}###{widget_id}_edit"
    if imgui.button(f"{S.DPT_VALUE_EDIT}##{widget_id}"):
        _saved_fields[widget_id] = copy.deepcopy(fields)
        imgui.open_popup(title)
    if not begin_dialog(title):
        return False
    submitted = render()
    clicked = dialog_buttons(S.OK, S.CANCEL)
    if clicked == 1:
        fields[:] = _saved_fields.get(widget_id, fields)
    if clicked is not None or submitted:
        _saved_fields.pop(widget_id, None)
        imgui.close_current_popup()
    end_dialog()
    return False


def _float_bits(value: float, width: int) -> int:
    if width == 16:
        return int.from_bytes(bytes(DPT2ByteFloat.to_knx(value).value), "big")
    return int.from_bytes(struct.pack(">f" if width == 32 else ">d", value), "big")


def _bits_float(value: int, width: int) -> float:
    data = value.to_bytes(width // 8, "big")
    if width == 16:
        return float(DPT2ByteFloat.from_knx(DPTArray(data)))
    return float(struct.unpack(">f" if width == 32 else ">d", data)[0])


@dataclass
class _MasterField:
    fmt: FieldFormat
    label: str = ""
    text: str = ""
    selected: int = 0

    @property
    def whole(self) -> bool:
        return self.fmt.kind in ("unsigned", "signed") and self.fmt.coefficient in (
            None,
            1.0,
        )

    def raw_range(self) -> tuple[int, int]:
        width = self.fmt.width
        if self.fmt.kind == "signed":
            return -(1 << (width - 1)), (1 << (width - 1)) - 1
        return 0, (1 << width) - 1

    def shown(self, raw: float) -> float:
        return raw * (self.fmt.coefficient or 1.0) + (self.fmt.offset or 0.0)

    def limits(self) -> tuple[float | None, float | None]:
        if self.fmt.kind == "float":
            return self.fmt.minimum, self.fmt.maximum
        low, high = self.raw_range()
        # Scaled limits are rounded: 255 * 0.3921566 is shown as 100, not 99.99993.
        minimum = self.fmt.minimum
        if minimum is None:
            minimum = round(self.shown(low), 2)
        maximum = self.fmt.maximum
        if maximum is None:
            maximum = round(self.shown(high), 2)
        return minimum, maximum

    def render(self, widget_id: str, *, inline: bool) -> bool:
        kind = self.fmt.kind
        if kind == "bit":
            for index, label in enumerate(
                (value_text(self.fmt.cleared), value_text(self.fmt.set))
            ):
                if index:
                    imgui.same_line()
                text = label or str(index)
                if imgui.radio_button(
                    f"{text}##{widget_id}_{index}", self.selected == index
                ):
                    self.selected = index
            return False
        if kind == "enum":
            texts = self.fmt.texts
            current = value_text(texts[self.selected][1]) if texts else ""
            imgui.set_next_item_width(px(170.0 if inline else 260.0))
            if imgui.begin_combo(f"##{widget_id}", current):
                for index, (_, text) in enumerate(texts):
                    if imgui.selectable(
                        f"{value_text(text)}##{index}", self.selected == index
                    )[0]:
                        self.selected = index
                imgui.end_combo()
            return False
        if kind == "string":
            imgui.set_next_item_width(px(180.0 if inline else 260.0))
            submitted, self.text = imgui.input_text(
                f"##{widget_id}", self.text, imgui.InputTextFlags_.enter_returns_true
            )
            return submitted
        imgui.set_next_item_width(px(140.0 if inline else 200.0))
        submitted, self.text = imgui.input_text_with_hint(
            f"##{widget_id}",
            _range_hint(*self.limits()),
            self.text,
            imgui.InputTextFlags_.enter_returns_true,
        )
        if self.fmt.unit:
            imgui.same_line()
            imgui.text_disabled(self.fmt.unit)
        return submitted

    def bits(self) -> int:
        """The field's value as ``width`` bits (strings are encoded by :meth:`octets`)."""
        kind = self.fmt.kind
        if kind == "reserved":
            return 0
        if kind == "bit":
            return self.selected
        if kind == "enum":
            return self.fmt.texts[self.selected][0] if self.fmt.texts else 0
        prefix = f"{self.label}: " if self.label else ""
        value = _parse_number(self.text)
        if value is None:
            raise ValueError(prefix + S.DPT_VALUE_NOT_A_NUMBER)
        if self.whole and isinstance(value, float) and not value.is_integer():
            raise ValueError(prefix + S.DPT_VALUE_WHOLE_NUMBER)
        minimum, maximum = self.limits()
        if (minimum is not None and value < minimum) or (
            maximum is not None and value > maximum
        ):
            raise ValueError(
                prefix
                + S.DPT_VALUE_RANGE.format(
                    minimum=_number_text(minimum if minimum is not None else value),
                    maximum=_number_text(maximum if maximum is not None else value),
                )
            )
        plain = (value - (self.fmt.offset or 0.0)) / (self.fmt.coefficient or 1.0)
        if kind == "float":
            return _float_bits(plain, self.fmt.width)
        low, high = self.raw_range()
        raw = max(low, min(high, round(plain)))
        return raw & ((1 << self.fmt.width) - 1)

    def octets(self) -> bytes:
        size = self.fmt.width // 8
        try:
            data = self.text.encode(self.fmt.encoding or "us-ascii")
        except (UnicodeEncodeError, LookupError) as exc:
            raise ValueError(S.DPT_VALUE_INVALID) from exc
        if self.fmt.null_terminated:
            data += b"\x00"
        if len(data) > size:
            raise ValueError(S.DPT_VALUE_TOO_LONG_BYTES.format(count=size))
        return data if self.fmt.variable_length else data.ljust(size, b"\x00")

    def show_bits(self, value: int) -> None:
        kind = self.fmt.kind
        if kind == "bit":
            self.selected = value & 1
        elif kind == "enum":
            for index, (raw, _) in enumerate(self.fmt.texts):
                if raw == value:
                    self.selected = index
        elif kind == "float":
            self.text = _number_text(self.shown(_bits_float(value, self.fmt.width)))
        elif kind in ("unsigned", "signed"):
            if kind == "signed" and value >= 1 << (self.fmt.width - 1):
                value -= 1 << self.fmt.width
            shown = self.shown(value)
            self.text = str(round(shown)) if self.whole else _number_text(shown)

    def show_octets(self, data: bytes) -> None:
        if self.fmt.null_terminated and b"\x00" in data:
            data = data[: data.index(b"\x00")]
        self.text = data.rstrip(b"\x00").decode(
            self.fmt.encoding or "us-ascii", errors="replace"
        )


def _master_fields(fmt: CompositeFormat) -> list[_MasterField]:
    """The fields with their labels; several unnamed values are numbered ``Value 1`` ..."""
    unnamed = [
        f
        for f in fmt.fields
        if not f.name and f.kind not in ("reserved", "bit", "enum")
    ]
    fields: list[_MasterField] = []
    number = 0
    for item in fmt.fields:
        label = title_case(item.name)
        if not label and len(unnamed) > 1 and item in unnamed:
            number += 1
            label = S.DPT_VALUE_NUMBERED.format(number=number)
        fields.append(_MasterField(item, label))
    return fields


@dataclass
class _Master(_Editor):
    """A subtype laid out by the KNX master data, one input per field."""

    fields: list[_MasterField]

    @property
    def shown_fields(self) -> list[_MasterField]:
        return [f for f in self.fields if f.fmt.kind != "reserved"]

    def _render_fields(self, widget_id: str) -> bool:
        shown = self.shown_fields
        labels = [f.label for f in shown if f.label]
        label_w = max((imgui.calc_text_size(text).x for text in labels), default=0.0)
        submitted = False
        for index, item in enumerate(shown):
            if labels:
                imgui.align_text_to_frame_padding()
                imgui.text_disabled(item.label)
                imgui.same_line(label_w + px(16.0))
            submitted = item.render(f"{widget_id}_{index}", inline=False) or submitted
        return submitted

    def render(self, widget_id: str, *, inline: bool) -> bool:
        shown = self.shown_fields
        if len(shown) == 1:
            return shown[0].render(widget_id, inline=inline)
        if inline:
            return _edit_in_dialog(
                widget_id, self.fields, lambda: self._render_fields(widget_id)
            )
        return self._render_fields(widget_id)

    def payload(self) -> Payload:
        value = 0
        width = 0
        tail = b""
        for item in self.fields:
            if item.fmt.kind == "string":
                tail += item.octets()
                continue
            value = (value << item.fmt.width) | item.bits()
            width += item.fmt.width
        if not tail and width <= 6:
            return DPTBinary(value)
        if width % 8:
            raise ValueError(S.DPT_VALUE_INVALID)
        head = value.to_bytes(width // 8, "big") if width else b""
        return DPTArray(head + tail)

    def set_payload(self, payload: Payload) -> None:
        if isinstance(payload, DPTBinary):
            raw_bits, total = int(payload.value), sum(f.fmt.width for f in self.fields)
        else:
            data = bytes(payload.value)
            raw_bits, total = int.from_bytes(data, "big"), len(data) * 8
        position = 0
        for item in self.fields:
            if item.fmt.kind == "string":
                item.show_octets(raw_bits.to_bytes(total // 8, "big")[position // 8 :])
                return
            end = position + item.fmt.width
            item.show_bits((raw_bits >> (total - end)) & ((1 << item.fmt.width) - 1))
            position = end


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
                (value_text(fmt.cleared), _raw_payload(transcoder, dpt.major, 0)),
                (value_text(fmt.set), _raw_payload(transcoder, dpt.major, 1)),
            ]
        )
    if isinstance(fmt, EnumFormat) and fmt.texts:
        return _Choice(
            [
                (value_text(text), _raw_payload(transcoder, dpt.major, raw))
                for raw, text in sorted(fmt.texts.items())
            ]
        )
    if transcoder is None:
        if isinstance(fmt, CompositeFormat):
            return _Master(_master_fields(fmt))
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
    if isinstance(fmt, CompositeFormat):
        return _Master(_master_fields(fmt))
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
