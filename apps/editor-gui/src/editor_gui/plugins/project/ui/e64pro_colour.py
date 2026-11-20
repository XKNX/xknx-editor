"""DALI colour values and the offline colour dialog."""

from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass, replace

from imgui_bundle import imgui

from editor_gui.plugin_blob_readers.dali_backup.e64pro import (
    BYTE_COLOUR_MODES,
    VERIFIED_COLOUR_MODES,
)
from editor_gui.plugins.project.strings import _
from editor_gui.widgets.dpi import px


@dataclass(frozen=True)
class Colour:
    mode: str
    components: tuple[str, ...]

    @classmethod
    def parse(cls, mode: str, raw: str) -> Colour | None:
        parts = tuple(raw.split(";"))
        if mode == "2":
            if re.fullmatch(r"[0-9]{1,5}", raw) and 1 <= int(raw) <= 65535:
                return cls(mode, parts)
            return None
        count = 4 if mode in BYTE_COLOUR_MODES or mode == "32" else 3
        if mode not in VERIFIED_COLOUR_MODES or len(parts) != count:
            return None
        if mode in BYTE_COLOUR_MODES:
            if all(re.fullmatch(r"[0-9]{1,3}", p) and int(p) <= 255 for p in parts):
                return cls(mode, parts)
            return None
        if not all(
            re.fullmatch(r"[01](?:\.[0-9]+)?", p) and float(p) <= 1 for p in parts[:3]
        ):
            return None
        if mode == "32" and not (
            re.fullmatch(r"[0-9]{1,3}", parts[3]) and int(parts[3]) <= 255
        ):
            return None
        return cls(mode, parts)

    @property
    def raw(self) -> str:
        return ";".join(self.components)

    @property
    def rgb(self) -> tuple[float, float, float]:
        if self.mode == "2":
            return _kelvin_rgb(int(self.components[0]))
        x, y, level = (float(p) for p in self.components[:3])
        if self.mode in BYTE_COLOUR_MODES:
            return x / 255, y / 255, level / 255
        return (
            _xy_rgb(x, y, level)
            if self.mode == "1"
            else colorsys.hsv_to_rgb(x, y, level)
        )

    @property
    def summary(self) -> str:
        if self.mode == "2":
            return f"{self.raw} K"
        if self.mode == "1":
            return (
                f"x {float(self.components[0]):.4f}  y {float(self.components[1]):.4f}"
            )
        if self.mode in BYTE_COLOUR_MODES:
            red, green, blue = self.components[:3]
            white = f"  W {self.components[3]}" if self.mode == "8" else ""
            return f"R {red}  G {green}  B {blue}{white}"
        h, s, v = (float(part) for part in self.components[:3])
        white = f"  W {self.components[3]}" if self.mode == "32" else ""
        return f"{h * 360:.0f}°  {s:.0%}  {v:.0%}{white}"

    def with_component(self, index: int, value: str) -> Colour:
        return replace(
            self,
            components=(*self.components[:index], value, *self.components[index + 1 :]),
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _kelvin_rgb(kelvin: int) -> tuple[float, float, float]:
    """Approximate display swatch, independent of the value written to the device."""
    temperature = max(1000, min(40000, kelvin)) / 100
    if temperature <= 66:
        red = 255
        green = 99.4708025861 * math.log(temperature) - 161.1195681661
        blue = (
            0
            if temperature <= 19
            else 138.5177312231 * math.log(temperature - 10) - 305.0447927307
        )
    else:
        red = 329.698727446 * (temperature - 60) ** -0.1332047592
        green = 288.1221695283 * (temperature - 60) ** -0.0755148492
        blue = 255
    return _clamp(red / 255), _clamp(green / 255), _clamp(blue / 255)


def _xy_rgb(x: float, y: float, level: float) -> tuple[float, float, float]:
    if y <= 0:
        return 0.0, 0.0, 0.0
    X, Z = x * level / y, (1 - x - y) * level / y
    r = 3.2406 * X - 1.5372 * level - 0.4986 * Z
    g = -0.9689 * X + 1.8758 * level + 0.0415 * Z
    b = 0.0557 * X - 0.2040 * level + 1.0570 * Z
    scale = max(r, g, b, 1.0)

    def srgb(linear: float) -> float:
        linear = _clamp(linear / scale)
        return (
            12.92 * linear
            if linear <= 0.0031308
            else 1.055 * linear ** (1 / 2.4) - 0.055
        )

    return srgb(r), srgb(g), srgb(b)


def swatch(colour: Colour) -> imgui.ImVec4:
    return imgui.ImVec4(*colour.rgb, 1.0)


def _edit_white(colour: Colour) -> Colour:
    changed, white = imgui.slider_int(
        _("White") + " (0-255)",
        int(colour.components[3]),
        0,
        255,
        flags=imgui.SliderFlags_.always_clamp,
    )
    return colour.with_component(3, str(white)) if changed else colour


def edit_colour(colour: Colour) -> Colour:
    """Edit a draft. Retain each original component until that control changes."""
    if colour.mode == "2":
        kelvin = int(colour.raw)
        label = _("Colour Temperature") + " (K)"
        changed, new = imgui.slider_int(
            "##kelvin", kelvin, min(1000, kelvin), max(10000, kelvin)
        )
        typed, value = imgui.input_int(label, new if changed else kelvin)
        if typed and 1 <= value <= 65535:
            return colour.with_component(0, str(value))
        return colour.with_component(0, str(new)) if changed else colour
    if colour.mode == "1":
        for index, label in enumerate(("x", "y")):
            changed, value = imgui.drag_float(
                label,
                float(colour.components[index]),
                0.001,
                0.0,
                1.0,
                "%.4f",
                imgui.SliderFlags_.always_clamp,
            )
            if changed and math.isfinite(value):
                colour = colour.with_component(index, f"{_clamp(value):.4f}")
        return colour

    imgui.set_next_item_width(min(imgui.get_content_region_avail().x, px(300)))
    if colour.mode in BYTE_COLOUR_MODES:
        changed, picked = imgui.color_picker3(
            "##rgb",
            list(colour.rgb),
            imgui.ColorEditFlags_.input_rgb
            | imgui.ColorEditFlags_.no_inputs
            | imgui.ColorEditFlags_.no_options
            | imgui.ColorEditFlags_.no_side_preview,
        )
        if changed:
            for index, value in enumerate(picked):
                channel = round(_clamp(value) * 255)
                if channel != int(colour.components[index]):
                    colour = colour.with_component(index, str(channel))
        for index, label in enumerate(("R", "G", "B")):
            changed, channel = imgui.slider_int(
                label,
                int(colour.components[index]),
                0,
                255,
                flags=imgui.SliderFlags_.always_clamp,
            )
            if changed:
                colour = colour.with_component(index, str(channel))
        return _edit_white(colour) if colour.mode == "8" else colour

    hsv = [float(value) for value in colour.components[:3]]
    changed, picked = imgui.color_picker3(
        "##hsv",
        hsv,
        imgui.ColorEditFlags_.input_hsv
        | imgui.ColorEditFlags_.no_inputs
        | imgui.ColorEditFlags_.no_options
        | imgui.ColorEditFlags_.no_side_preview,
    )
    if changed:
        for index, value in enumerate(picked):
            colour = colour.with_component(index, f"{_clamp(value):.4f}")
    for index, label, scale in (
        (0, "H (°)", 360.0),
        (1, "S (%)", 100.0),
        (2, "V (%)", 100.0),
    ):
        changed, value = imgui.drag_float(
            label,
            float(colour.components[index]) * scale,
            0.5,
            0.0,
            scale,
            "%.1f",
            imgui.SliderFlags_.always_clamp,
        )
        if changed and math.isfinite(value):
            colour = colour.with_component(index, f"{_clamp(value / scale):.4f}")
    return _edit_white(colour) if colour.mode == "32" else colour
