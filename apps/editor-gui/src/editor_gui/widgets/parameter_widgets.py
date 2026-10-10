from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.regional import regional_format
from editor_gui.widgets.dpi import px
from editor_gui.widgets.float_format import format_float, general_float, parse_float
from editor_gui.widgets.icons import Icon, Picture, draw_icon
from editor_gui.widgets.strings import S
from editor_gui.widgets.time_format import (
    format_time,
    parse_time,
    time_pattern,
    unit_ms,
)
from editor_gui.widgets.value_pickers import (
    format_date,
    render_color_param,
    render_date_param,
)
from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.namespaces.intermediate.horizontal_alignment_t import (
    HorizontalAlignment,
)
from xknxeditor.namespaces.intermediate.parameter_block_layout_t import (
    ParameterBlockLayout,
)
from xknxeditor.prod.parser_v2.ui import (
    UiButton,
    UiComObject,
    UiNode,
    UiParameter,
    UiParameterBlock,
    UiSeparator,
    UiTab,
)
from xknxeditor.prod.parser_v2.ui.parameter import (
    CheckBoxWidget,
    ColorWidget,
    DateWidget,
    EnumWidget,
    FloatSliderWidget,
    FloatWidget,
    NumberSliderWidget,
    NumberWidget,
    PictureWidget,
    ProgressBarWidget,
    RawDataWidget,
    TimeWidget,
)

# Parameters changed from their default are tinted to stand out.
_CHANGED_COLOR = imgui.ImVec4(0.36, 0.71, 1.0, 1.0)
_ERROR_COLOR = imgui.ImVec4(1.0, 0.42, 0.42, 1.0)


@dataclass(frozen=True)
class ButtonActions:
    """Callbacks for Button elements: click, enabled state with tooltip, and last error."""

    on_click: Callable[[Device, UiButton], None]
    state: Callable[[Device, UiButton], tuple[bool, str | None]]
    error: Callable[[Device, UiButton], str | None]


def _render_button(
    device: Device, button: UiButton, prefix: str, buttons: ButtonActions | None
) -> None:
    enabled, tooltip = (
        buttons.state(device, button) if buttons is not None else (False, None)
    )
    enabled = enabled and not button.read_only
    imgui.begin_disabled(not enabled)
    clicked = imgui.button(f"{button.text}###btn_{prefix}_{button.id}")
    imgui.end_disabled()
    if tooltip and imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
        imgui.set_tooltip(tooltip)
    if clicked and enabled and buttons is not None:
        buttons.on_click(device, button)
    error = buttons.error(device, button) if buttons is not None else None
    if error:
        imgui.push_style_color(imgui.Col_.text, _ERROR_COLOR)
        imgui.text_wrapped(error)
        imgui.pop_style_color()


def _render_device_param(
    device: Device,
    param: UiParameter,
    widget_id: str,
    on_change: Callable[[str], None],
    *,
    deferred_enum: bool,
    differs: bool,
) -> EnumPopupRequest | None:
    """A parameter widget; a rejected input stays in a red field with the message as tooltip."""
    error = device.param_errors.get(param.ref_id)
    if error is None:
        return render_param_widget(
            param, widget_id, on_change, deferred_enum=deferred_enum, differs=differs
        )
    rejected = device.param_inputs.get(param.ref_id)
    shown = replace(param, value=rejected) if rejected is not None else param
    imgui.push_style_color(imgui.Col_.border, _ERROR_COLOR)
    imgui.push_style_var(imgui.StyleVar_.frame_border_size, px(2.0))
    req = render_param_widget(
        shown, widget_id, on_change, deferred_enum=deferred_enum, differs=differs
    )
    imgui.pop_style_var()
    imgui.pop_style_color()
    if imgui.is_item_hovered():
        imgui.set_tooltip(error)
    return req


_selected_help: list[str] = []


def take_selected_help() -> str | None:
    """The HelpContext of a parameter or block the user selected since the last call."""
    return _selected_help.pop() if _selected_help else None


def _track_help(help_context: str | None) -> None:
    if help_context and (imgui.is_item_activated() or imgui.is_item_clicked()):
        _selected_help[:] = [help_context]


def _default_display(param: UiParameter) -> str:
    """Human-readable default value (enum default resolved to its label)."""
    return _value_display(param, param.default_value) or "-"


def _value_display(param: UiParameter, value: str) -> str:
    """``value`` as shown for ``param``: an enum value as its label, a float, date or time in
    its format."""
    widget = param.widget
    if isinstance(widget, EnumWidget):
        for choice in widget.choices:
            if str(choice.value) == value:
                return choice.label
    if isinstance(widget, FloatWidget | FloatSliderWidget) and value:
        return format_float(value, decimal=regional_format().decimal)
    if isinstance(widget, DateWidget):
        return format_date(value, widget.display_the_year, regional_format())
    if isinstance(widget, TimeWidget):
        return format_time(value, widget.unit.value, _hint(widget))
    return value


def _shows_as_text(param: UiParameter) -> bool:
    """A read-only parameter is shown as its value; a checkbox keeps its (disabled) box."""
    return param.access == Access.READ and not isinstance(
        param.widget, CheckBoxWidget | PictureWidget
    )


# Loads the picture of a TypePicture baggage while a parameter tree is drawn.
_get_picture: Callable[[str], Picture | None] | None = None


def _render_picture(widget: PictureWidget, width: float) -> None:
    """The picture at its own size, placed in ``width`` by its alignment and clipped to it."""
    picture = _get_picture(widget.ref_id) if _get_picture is not None else None
    if picture is None:
        imgui.text_disabled(S.NODE_IMAGE_PLACEHOLDER)
        return
    w, h = px(picture.width), px(picture.height)
    alignment = widget.horizontal_alignment
    if alignment == HorizontalAlignment.STRETCH:
        w, h = width, h * width / w
    start = imgui.get_cursor_screen_pos()
    x = start.x
    if alignment == HorizontalAlignment.RIGHT:
        x += width - w
    elif alignment == HorizontalAlignment.MIDDLE:
        x += (width - w) / 2
    draw_list = imgui.get_window_draw_list()
    draw_list.push_clip_rect(start, imgui.ImVec2(start.x + width, start.y + h), True)
    while True:
        draw_list.add_image(
            picture.texture, imgui.ImVec2(x, start.y), imgui.ImVec2(x + w, start.y + h)
        )
        x += w
        if alignment != HorizontalAlignment.REPEAT or x >= start.x + width:
            break
    draw_list.pop_clip_rect()
    imgui.dummy(imgui.ImVec2(width, h))


@dataclass
class EnumPopupRequest:
    device: Device
    param: UiParameter


def render_param_widget(
    param: UiParameter,
    widget_id: str,
    on_change: Callable[[str], None],
    deferred_enum: bool = False,
    differs: bool = False,
) -> EnumPopupRequest | None:
    """``differs`` (multi-device edit): the parameter's value diverges across the selected devices,
    so show a ``<differs>`` placeholder instead of one device's value. Any edit still writes the
    chosen value to all of them via ``on_change``."""
    match param.widget:
        case EnumWidget() as w:
            current_idx = 0
            for i, choice in enumerate(w.choices):
                if str(choice.value) == param.value:
                    current_idx = i
                    break
            preview = w.choices[current_idx].label if w.choices else param.value
            if differs:
                preview = S.PARAM_DIFFERS
            if deferred_enum:
                if imgui.button(f"{preview}##{widget_id}", imgui.ImVec2(-1, 0)):
                    return EnumPopupRequest(device=None, param=param)  # type: ignore[arg-type]
            elif w.radio:
                for i, choice in enumerate(w.choices):
                    if i:
                        imgui.same_line()
                    selected = not differs and str(choice.value) == param.value
                    if _small_radio(
                        f"{choice.label}##{widget_id}_{i}", selected, first=i == 0
                    ):
                        on_change(str(choice.value))
            else:
                if imgui.begin_combo(f"##{widget_id}", preview):
                    for choice in w.choices:
                        selected = not differs and str(choice.value) == param.value
                        if imgui.selectable(choice.label, selected)[0]:
                            on_change(str(choice.value))
                    imgui.end_combo()
        case NumberWidget() as w:
            _render_int_param(
                widget_id,
                "" if differs else param.value,
                w.min,
                w.max,
                on_change,
                differs,
                w.increment,
            )
        case ProgressBarWidget() as w:
            _render_int_param(
                widget_id,
                "" if differs else param.value,
                w.min,
                w.max,
                on_change,
                differs,
            )
        case NumberSliderWidget() as w:
            _render_slider(
                widget_id, param.value, w.min, w.max, w.increment, on_change, differs
            )
        case FloatSliderWidget() as w:
            _render_slider(
                widget_id,
                param.value,
                w.min,
                w.max,
                w.increment,
                on_change,
                differs,
                is_float=True,
            )
        case FloatWidget() as w:
            _render_float_param(widget_id, w, param.value, on_change, differs)
        case TimeWidget() as w:
            _render_time_param(widget_id, w, param.value, on_change, differs)
        case ColorWidget():
            render_color_param(widget_id, param.value, on_change, differs)
        case DateWidget() as w:
            render_date_param(
                widget_id, param.value, w.display_the_year, on_change, differs
            )
        case CheckBoxWidget():
            if differs:
                _render_differs_text(widget_id, on_change)
            else:
                checked = param.value == "1"
                changed, new_checked = _small_checkbox(f"##{widget_id}", checked)
                if changed:
                    on_change("1" if new_checked else "0")
        case RawDataWidget():
            pass
        case PictureWidget() as w:
            _render_picture(w, imgui.get_content_region_avail().x)
        case _:
            _render_text_param(widget_id, param.value, on_change, differs)
    return None


def _small_checkbox(label: str, checked: bool) -> tuple[bool, bool]:
    """A checkbox about the size of the text, centred in a row of full-height fields."""
    padding = imgui.get_style().frame_padding
    small = padding.y / 4
    imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + padding.y - small)
    imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(padding.x, small))
    result = imgui.checkbox(label, checked)
    imgui.pop_style_var()
    return result


def _small_radio(label: str, active: bool, *, first: bool) -> bool:
    """A radio button about the size of the text, centred in a row of full-height fields;
    the ones after the ``first`` follow on its line."""
    padding = imgui.get_style().frame_padding
    small = padding.y / 4
    if first:
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + padding.y - small)
    imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(padding.x, small))
    pressed = imgui.radio_button(label, active)
    imgui.pop_style_var()
    return pressed


def _render_text_param(
    widget_id: str,
    value: str,
    on_change: Callable[[str], None],
    differs: bool,
) -> None:
    if differs:
        _, new_value = imgui.input_text_with_hint(f"##{widget_id}", S.PARAM_DIFFERS, "")
    else:
        _, new_value = imgui.input_text(f"##{widget_id}", value)
    if imgui.is_item_deactivated_after_edit() and (not differs or new_value):
        on_change(new_value)


def _render_float_param(
    widget_id: str,
    widget: FloatWidget,
    value: str,
    on_change: Callable[[str], None],
    differs: bool,
) -> None:
    """A float with the regional decimal separator; an entry is stored when editing ends."""
    regional = regional_format()
    shown = "" if differs else format_float(value, decimal=regional.decimal)
    if differs:
        _, text = imgui.input_text_with_hint(f"##{widget_id}", S.PARAM_DIFFERS, "")
    else:
        _, text = imgui.input_text(f"##{widget_id}", shown)
    if not imgui.is_item_deactivated_after_edit() or text == shown:
        return
    stored = parse_float(
        text, widget.min, widget.max, decimal=regional.decimal, group=regional.group
    )
    if stored is not None:
        on_change(stored)


# Value of a slider while it is dragged, stored when it is released.
_slider_values: dict[str, float] = {}


def _render_slider(
    widget_id: str,
    value: str,
    minimum: float,
    maximum: float,
    increment: float | None,
    on_change: Callable[[str], None],
    differs: bool,
    *,
    is_float: bool = False,
) -> None:
    """A slider in steps of ``increment`` that shows its value; stored when released."""
    try:
        current = float(value)
    except ValueError:
        current = minimum
    current = _slider_values.get(widget_id, current)
    if differs and widget_id not in _slider_values:
        shown = S.PARAM_DIFFERS
    elif is_float:
        shown = general_float(current).replace(".", regional_format().decimal)
    else:
        shown = str(round(current))
    changed, moved = imgui.slider_float(
        f"##{widget_id}", current, minimum, maximum, shown.replace("%", "%%")
    )
    if changed:
        step = increment or (0 if is_float else 1)
        if step:
            moved = minimum + round((moved - minimum) / step) * step
        _slider_values[widget_id] = min(maximum, max(minimum, moved))
    if imgui.is_item_deactivated():
        released = _slider_values.pop(widget_id, None)
        if released is not None:
            stored = general_float(released) if is_float else str(round(released))
            if differs or stored != value:
                on_change(stored)


def _render_time_param(
    widget_id: str,
    widget: TimeWidget,
    value: str,
    on_change: Callable[[str], None],
    differs: bool,
) -> None:
    """A time in the format of its hint with the format next to it, else a number of its unit
    with the unit next to it."""
    hint = _hint(widget)
    pattern = time_pattern(hint) if unit_ms(widget.unit.value) is not None else None
    if pattern is None:
        _render_int_param(
            widget_id,
            "" if differs else value,
            widget.min,
            widget.max,
            on_change,
            differs,
        )
        _render_suffix(S.time_unit(widget.unit.value))
        return
    shown = "" if differs else format_time(value, widget.unit.value, hint)
    _set_number_field_width(max(len(pattern), len(shown)), spin=False)
    if differs:
        _, text = imgui.input_text_with_hint(f"##{widget_id}", S.PARAM_DIFFERS, "")
    else:
        _, text = imgui.input_text(f"##{widget_id}", shown)
    edited = imgui.is_item_deactivated_after_edit() and text != shown
    _render_suffix(pattern)
    if edited:
        stored = parse_time(text, widget.unit.value, hint, widget.min, widget.max)
        if stored is not None:
            on_change(stored)


def _suffix_width(suffix: str | None) -> float:
    """Room a suffix shown after a field takes."""
    if not suffix:
        return 0.0
    return imgui.calc_text_size(suffix).x + imgui.get_style().item_spacing.x


def _render_suffix(suffix: str | None) -> None:
    """``suffix`` (e.g. a unit) after the previous field, on the field's text line."""
    if suffix:
        imgui.same_line()
        imgui.align_text_to_frame_padding()
        imgui.text(suffix)


def _hint(widget: TimeWidget) -> str | None:
    return widget.hint.value if widget.hint is not None else None


def _spin_width() -> float:
    return imgui.get_frame_height() * 0.6


def _set_number_field_width(chars: int, *, spin: bool = True) -> None:
    """Size the next field to ``chars`` characters (plus its spin arrows, drawn inside its right
    end), at most the requested width."""
    style = imgui.get_style()
    content = (
        imgui.calc_text_size("0" * max(chars, _MIN_NUMBER_CHARS)).x
        + 2 * style.frame_padding.x
    )
    if spin:
        content += _spin_width()
    imgui.set_next_item_width(min(imgui.calc_item_width(), content))
    if spin:
        imgui.set_next_item_allow_overlap()


def _spin_buttons(widget_id: str, *, enabled: bool) -> int:
    """Up/down arrows inside the right end of the previous field: +1 or -1 while pressed."""
    rect_min = imgui.get_item_rect_min()
    rect_max = imgui.get_item_rect_max()
    size = imgui.ImVec2(_spin_width(), (rect_max.y - rect_min.y) / 2)
    left = rect_max.x - size.x
    draw = imgui.get_window_draw_list()
    rounding = imgui.get_style().frame_rounding
    steps = 0
    imgui.set_cursor_screen_pos(imgui.ImVec2(left, rect_min.y))
    imgui.begin_group()
    imgui.begin_disabled(not enabled)
    imgui.push_item_flag(imgui.ItemFlags_.button_repeat, True)
    for row, step in enumerate((1, -1)):
        top = imgui.ImVec2(left, rect_min.y + row * size.y)
        imgui.set_cursor_screen_pos(top)
        if imgui.invisible_button(f"##spin{row}_{widget_id}", size):
            steps = step
        if imgui.is_item_active() or imgui.is_item_hovered():
            color = (
                imgui.Col_.button_active
                if imgui.is_item_active()
                else imgui.Col_.button_hovered
            )
            draw.add_rect_filled(
                top,
                imgui.ImVec2(top.x + size.x, top.y + size.y),
                imgui.get_color_u32(color),
                rounding,
            )
        mid_x = top.x + size.x / 2
        mid_y = top.y + size.y / 2
        half = min(size.x, size.y) * 0.35
        tip = mid_y - half * 0.6 * step
        base = mid_y + half * 0.6 * step
        draw.add_triangle_filled(
            imgui.ImVec2(mid_x, tip),
            imgui.ImVec2(mid_x - half, base),
            imgui.ImVec2(mid_x + half, base),
            imgui.get_color_u32(imgui.Col_.text),
        )
    imgui.pop_item_flag()
    imgui.end_disabled()
    imgui.end_group()
    return steps


def _render_differs_text(widget_id: str, on_change: Callable[[str], None]) -> None:
    """A checkbox cannot show a third "differs" state, so offer an explicit Off/On choice that,
    once picked, writes the same value to every selected device."""
    if imgui.begin_combo(f"##{widget_id}", S.PARAM_DIFFERS):
        if imgui.selectable(S.PARAM_OFF, False)[0]:
            on_change("0")
        if imgui.selectable(S.PARAM_ON, False)[0]:
            on_change("1")
        imgui.end_combo()


def _render_int_param(
    widget_id: str,
    value: str,
    min_value: int | None,
    max_value: int | None,
    on_change: Callable[[str], None],
    differs: bool = False,
    increment: int = 1,
) -> None:
    chars = max(len(str(min_value or 0)), len(str(max_value or 0)), len(value))
    _set_number_field_width(chars)
    if differs:
        _, new_text = imgui.input_text_with_hint(
            f"##{widget_id}", S.PARAM_DIFFERS, "", imgui.InputTextFlags_.chars_decimal
        )
        edited = imgui.is_item_deactivated_after_edit() and bool(new_text)
    else:
        _, new_text = imgui.input_text(
            f"##{widget_id}", value, imgui.InputTextFlags_.chars_decimal
        )
        edited = imgui.is_item_deactivated_after_edit()
    steps = _spin_buttons(widget_id, enabled=not differs)
    if steps:
        try:
            new_text = str(int(value) + steps * (increment or 1))
        except ValueError:
            return
    elif not edited:
        return
    try:
        clamped = int(new_text)
    except ValueError:
        clamped = min_value if min_value is not None else 0
    if min_value is not None:
        clamped = max(min_value, clamped)
    if max_value is not None:
        clamped = min(max_value, clamped)
    if differs or str(clamped) != value:
        on_change(str(clamped))


def _flatten_params(nodes: list[UiNode] | tuple[UiNode, ...]) -> list[UiParameter]:
    params: list[UiParameter] = []
    for node in nodes:
        if isinstance(node, UiParameter):
            params.append(node)
        elif isinstance(node, (UiTab, UiParameterBlock)):
            params.extend(_flatten_params(node.children))
    return params


def differing_param_refs(devices: list[Device]) -> frozenset[str]:
    """ref_ids whose value diverges across ``devices`` (multi-device edit). Devices are expected to
    share an application, so ref_ids line up. Builds each device's UI tree — the caller must cache
    the result (do not call per frame)."""
    if len(devices) < 2:
        return frozenset()
    seen: dict[str, str] = {}
    diff: set[str] = set()
    for device in devices:
        for p in _flatten_params(device.get_ui()):
            if p.ref_id in seen:
                if seen[p.ref_id] != p.value:
                    diff.add(p.ref_id)
            else:
                seen[p.ref_id] = p.value
    return frozenset(diff)


def _all_blocks(nodes: list[UiNode] | tuple[UiNode, ...]) -> list[UiParameterBlock]:
    out: list[UiParameterBlock] = []
    for node in nodes:
        if isinstance(node, UiParameterBlock):
            out.append(node)
            out.extend(_all_blocks(node.children))
        elif isinstance(node, UiTab):
            out.extend(_all_blocks(node.children))
    return out


def _ancestor_blocks(
    nodes: list[UiNode] | tuple[UiNode, ...],
    ref_id: str,
    stack: tuple[UiParameterBlock, ...] = (),
) -> tuple[UiParameterBlock, ...] | None:
    """The chain of ``UiParameterBlock`` ancestors (outermost first) of the parameter ``ref_id``."""
    for node in nodes:
        if isinstance(node, UiParameter):
            if node.ref_id == ref_id:
                return stack
        elif isinstance(node, UiTab):
            found = _ancestor_blocks(node.children, ref_id, stack)
            if found is not None:
                return found
        elif isinstance(node, UiParameterBlock):
            found = _ancestor_blocks(node.children, ref_id, (*stack, node))
            if found is not None:
                return found
    return None


def _block_signature(block: UiParameterBlock) -> tuple[tuple[str, str], ...]:
    """Structural fingerprint of a block: (label, widget-type) of each parameter it contains. Two
    repeated channels of the same type share this signature regardless of the channel's own name."""
    return tuple(
        (p.label, type(p.widget).__name__) for p in _flatten_params(block.children)
    )


def channel_apply_targets(nodes: list[UiNode], ref_id: str) -> list[str]:
    """ref_ids of the *same* parameter in every other repeated channel — for "apply to all channels".

    A device like an OpenKNX PresenceModule expands its channels (PM 1, PM 2, …) into structurally
    identical parameter blocks (no module link). Given a parameter the user just edited, this
    finds its channel block (the outermost ancestor block whose structure repeats elsewhere in the
    tree) and returns the ref_id of the positionally-corresponding parameter in each twin block. The
    label must also match, so a channel currently showing a different (conditionally visible)
    structure is skipped rather than mis-mapped. Empty when the parameter is not inside a repeated
    channel."""
    chain = _ancestor_blocks(nodes, ref_id)
    if not chain:
        return []
    blocks = _all_blocks(nodes)
    counts: dict[tuple[tuple[str, str], ...], int] = {}
    sig_by_block: dict[int, tuple[tuple[str, str], ...]] = {}
    for block in blocks:
        sig = _block_signature(block)
        sig_by_block[id(block)] = sig
        counts[sig] = counts.get(sig, 0) + 1
    # Channel unit = the outermost ancestor block whose structure occurs more than once.
    channel_block = next(
        (b for b in chain if counts.get(sig_by_block[id(b)], 0) >= 2), None
    )
    if channel_block is None:
        return []
    sig = sig_by_block[id(channel_block)]
    params = _flatten_params(channel_block.children)
    idx = next((i for i, p in enumerate(params) if p.ref_id == ref_id), None)
    if idx is None:
        return []
    label = params[idx].label
    targets: list[str] = []
    for block in blocks:
        if block is channel_block or sig_by_block[id(block)] != sig:
            continue
        twin = _flatten_params(block.children)
        if idx < len(twin) and twin[idx].label == label:
            targets.append(twin[idx].ref_id)
    return targets


def count_parameters(nodes: list[UiNode] | tuple[UiNode, ...]) -> int:
    count = 0
    for node in nodes:
        if isinstance(node, UiParameter):
            count += 1
        elif isinstance(node, (UiTab, UiParameterBlock)):
            count += count_parameters(node.children)
    return count


def _node_matches(node: UiNode, needle: str) -> bool:
    """Whether ``node`` or any descendant has a label containing ``needle`` (lowercased)."""
    if isinstance(node, UiParameter):
        return needle in node.label.lower()
    if isinstance(node, UiButton):
        return needle in node.text.lower()
    if isinstance(node, (UiTab, UiParameterBlock)):
        label = (
            getattr(node, "text", None) or getattr(node, "name", None) or ""
        ).lower()
        if needle in label:
            return True
        return any(_node_matches(c, needle) for c in node.children)
    return False


def _instance_label(label: str, node_id: str | None) -> str:
    """Disambiguate a repeated module instance's tab/block label with its instance index.

    Some apps (e.g. the MDT DALI gateway) give every repeated instance the same text — 16 group tabs
    all "G,", 64 ECG blocks all "ECG ," — because the instance-number placeholder isn't substituted
    into the title; only the id differs (``…_MI-1`` … ``_MI-64``). Append that index so they read
    "G, 1"…"G, 16" / "ECG, 1"…"ECG, 64" (matching how ETS shows "G1"/"ECG1"…). Skips labels that
    already carry the number.
    """
    match = re.search(r"_MI-(\d+)", node_id or "")
    if match and match.group(1) not in re.findall(r"\d+", label):
        return f"{label.rstrip(' ,;:')} {match.group(1)}".strip()
    return label


def _tab_label(tab: UiTab) -> str:
    return _instance_label(tab.text or tab.name or tab.id or "Tab", tab.id)


@dataclass(frozen=True, slots=True)
class ParameterPage:
    """A channel or parameter page in the page tree."""

    key: str
    label: str
    icon: str | None
    content: tuple[UiNode, ...]  # shown when the page is selected
    children: tuple[ParameterPage, ...] = ()
    block: UiParameterBlock | None = None
    selectable: bool = True


def _is_page(node: UiNode) -> bool:
    return isinstance(node, UiParameterBlock) and not node.inline and not node.hidden


def _unique_key(key: str, seen: dict[str, int]) -> str:
    count = seen.get(key, 0)
    seen[key] = count + 1
    return key if count == 0 else f"{key}#{count}"


def _has_content(nodes: tuple[UiNode, ...]) -> bool:
    return any(
        isinstance(n, (UiParameter, UiButton, UiSeparator))
        or (isinstance(n, UiParameterBlock) and not n.hidden)
        for n in nodes
    )


def _split_pages(
    children: tuple[UiNode, ...], prefix: str, seen: dict[str, int]
) -> tuple[tuple[UiNode, ...], list[ParameterPage]]:
    """Separate the content shown on a page from the sub-pages listed below it in the tree."""
    content: list[UiNode] = []
    pages: list[ParameterPage] = []
    for node in children:
        if isinstance(node, UiTab):
            key = f"{prefix}_{node.id}"
            if node.independent:
                inner, sub = _split_pages(node.children, key, seen)
                content.extend(inner)
                pages.extend(sub)
                continue
            inner, sub = _split_pages(node.children, key, seen)
            pages.append(
                ParameterPage(
                    key=_unique_key(key, seen),
                    label=_tab_label(node),
                    icon=node.icon,
                    content=inner,
                    children=tuple(sub),
                    selectable=_has_content(inner),
                )
            )
        elif _is_page(node):
            assert isinstance(node, UiParameterBlock)
            key = f"{prefix}_{node.id}"
            if node.layout == ParameterBlockLayout.LIST:
                inner, sub = _split_pages(node.children, key, seen)
            else:
                inner, sub = node.children, []
            pages.append(
                ParameterPage(
                    key=_unique_key(key, seen),
                    label=_instance_label(node.text or node.name or node.id, node.id),
                    icon=node.icon,
                    content=inner,
                    children=tuple(sub),
                    block=node,
                )
            )
        elif (
            isinstance(node, UiParameterBlock)
            and node.inline
            and not node.hidden
            and node.layout == ParameterBlockLayout.LIST
        ):
            inner, sub = _split_pages(node.children, f"{prefix}_{node.id}", seen)
            pages.extend(sub)
            content.append(replace(node, children=inner) if sub else node)
        else:
            content.append(node)
    return tuple(content), pages


def build_pages(nodes: list[UiNode], prefix: str) -> tuple[ParameterPage, ...]:
    """The page tree of a device: channels and parameter pages, as the dialog lists them."""
    content, pages = _split_pages(tuple(nodes), prefix, {})
    if _has_content(content):
        pages.insert(
            0,
            ParameterPage(
                key=f"{prefix}_root", label=S.PAGE_GENERAL, icon=None, content=content
            ),
        )
    return tuple(pages)


def filter_pages(
    pages: tuple[ParameterPage, ...], needle: str
) -> tuple[ParameterPage, ...]:
    """Pages whose label or parameters match ``needle``, with the pages leading to them."""
    result: list[ParameterPage] = []
    for page in pages:
        children = filter_pages(page.children, needle)
        own = needle in page.label.lower() or any(
            _node_matches(n, needle) for n in page.content
        )
        if own or children:
            result.append(
                replace(page, children=children, selectable=page.selectable and own)
            )
    return tuple(result)


def _first_selectable(pages: tuple[ParameterPage, ...]) -> ParameterPage | None:
    for page in pages:
        if page.selectable:
            return page
        found = _first_selectable(page.children)
        if found is not None:
            return found
    return None


def _page_path(
    pages: tuple[ParameterPage, ...], key: str
) -> list[ParameterPage] | None:
    """``key``'s page and the pages above it, top first."""
    for page in pages:
        if page.key == key:
            return [page]
        below = _page_path(page.children, key)
        if below is not None:
            return [page, *below]
    return None


# Device node id -> selected page key.
_selected_pages: dict[int, str] = {}
# Device node id -> (UI nodes, filter, pages) of the last page tree built.
_page_cache: dict[int, tuple[list[UiNode], str, tuple[ParameterPage, ...]]] = {}

_TREE_WIDTH = 240.0


def _device_pages(
    device: Device, nodes: list[UiNode], needle: str
) -> tuple[ParameterPage, ...]:
    cached = _page_cache.get(device.node_id)
    if cached is not None and cached[0] is nodes and cached[1] == needle:
        return cached[2]
    pages = build_pages(nodes, str(device.node_id))
    if needle:
        pages = filter_pages(pages, needle)
    _page_cache[device.node_id] = (nodes, needle, pages)
    return pages


def render_ui_tree(
    device: Device,
    nodes: list[UiNode],
    on_change: Callable[[Device, str, str], None],
    deferred_enum: bool = False,
    filter_text: str = "",
    differing_refs: frozenset[str] = frozenset(),
    buttons: ButtonActions | None = None,
    *,
    height: float = 0.0,
    get_icon: Callable[[str], Icon | None] | None = None,
    get_picture: Callable[[str], Picture | None] | None = None,
    footer: Callable[[float], None] | None = None,
    footer_height: float = 0.0,
) -> EnumPopupRequest | None:
    """The page tree beside the selected page, with filter and multi-device diff markers.

    ``get_icon`` loads a page icon by name and ``get_picture`` a picture by baggage id.

    ``footer`` is drawn below the page, about ``footer_height`` high, and is passed the
    exact height left for it.
    """
    if not nodes:
        return None
    needle = filter_text.lower().strip()
    pages = _device_pages(device, nodes, needle)
    path = _page_path(pages, _selected_pages.get(device.node_id, ""))
    if path is None or not path[-1].selectable:
        first = _first_selectable(pages)
        path = _page_path(pages, first.key) if first is not None else None
        if first is not None and not needle:
            _selected_pages[device.node_id] = first.key
    selected = path[-1] if path else None
    opened = frozenset(p.key for p in (path or [])[:-1])

    if height <= 0:
        height = imgui.get_content_region_avail().y
    height -= imgui.get_style().cell_padding.y * 2
    popup_request: EnumPopupRequest | None = None
    flags = imgui.TableFlags_.resizable | imgui.TableFlags_.borders_inner_v
    if imgui.begin_table(f"##pages_{device.node_id}", 2, flags):
        imgui.table_setup_column(
            "##tree", imgui.TableColumnFlags_.width_fixed, px(_TREE_WIDTH)
        )
        imgui.table_setup_column("##page", imgui.TableColumnFlags_.width_stretch)
        imgui.table_next_row()
        imgui.table_next_column()
        scroll = imgui.WindowFlags_.horizontal_scrollbar
        if imgui.begin_child("##page_tree", imgui.ImVec2(0, height), 0, scroll):
            clicked = _render_page_tree(
                pages,
                selected.key if selected else None,
                opened,
                bool(needle),
                get_icon,
            )
            if clicked is not None:
                _selected_pages[device.node_id] = clicked
        imgui.end_child()
        imgui.table_next_column()
        bottom = imgui.get_cursor_screen_pos().y + height
        page_height = height
        if footer is not None:
            page_height = max(height - footer_height, imgui.get_frame_height() * 3)
        if (
            imgui.begin_child("##page_content", imgui.ImVec2(0, page_height), 0, scroll)
            and selected is not None
        ):
            global _get_picture
            _get_picture = get_picture
            try:
                popup_request = _render_page(
                    device,
                    selected,
                    on_change,
                    deferred_enum,
                    needle,
                    differing_refs,
                    buttons,
                )
            finally:
                _get_picture = None
        imgui.end_child()
        if footer is not None:
            footer(bottom - imgui.get_cursor_screen_pos().y)
        imgui.end_table()
    return popup_request


def _render_page_tree(
    pages: tuple[ParameterPage, ...],
    selected: str | None,
    opened: frozenset[str],
    open_all: bool,
    get_icon: Callable[[str], Icon | None] | None,
) -> str | None:
    """Draw ``pages`` as tree nodes; returns the key of a page clicked this frame."""
    clicked: str | None = None
    icon_size = imgui.get_font_size()
    space = imgui.calc_text_size(" ").x
    for page in pages:
        flags = imgui.TreeNodeFlags_.span_avail_width
        if not page.children:
            flags |= (
                imgui.TreeNodeFlags_.leaf | imgui.TreeNodeFlags_.no_tree_push_on_open
            )
        if page.selectable:
            flags |= (
                imgui.TreeNodeFlags_.open_on_arrow
                | imgui.TreeNodeFlags_.open_on_double_click
            )
        if page.key == selected:
            flags |= imgui.TreeNodeFlags_.selected
        if open_all:
            imgui.set_next_item_open(True, imgui.Cond_.always)
        elif page.key in opened:
            imgui.set_next_item_open(True, imgui.Cond_.once)
        icon = get_icon(page.icon) if get_icon is not None and page.icon else None
        pad = " " * math.ceil((icon_size + space) / space) if icon is not None else ""
        is_open = imgui.tree_node_ex(f"{pad}{page.label}###{page.key}", flags)
        if icon is not None:
            top = imgui.get_item_rect_min()
            x = top.x + imgui.get_tree_node_to_label_spacing()
            y = top.y + (imgui.get_item_rect_size().y - icon_size) / 2
            draw_icon(icon, imgui.ImVec2(x, y), icon_size)
        if (
            page.selectable
            and imgui.is_item_clicked()
            and not imgui.is_item_toggled_open()
        ):
            clicked = page.key
        if page.block is not None:
            _track_help(page.block.help_context)
        if is_open and page.children:
            below = _render_page_tree(
                page.children, selected, opened, open_all, get_icon
            )
            clicked = clicked or below
            imgui.tree_pop()
    return clicked


def _render_page(
    device: Device,
    page: ParameterPage,
    on_change: Callable[[Device, str, str], None],
    deferred_enum: bool,
    needle: str,
    differing_refs: frozenset[str],
    buttons: ButtonActions | None,
) -> EnumPopupRequest | None:
    block = page.block
    if needle in page.label.lower():
        needle = ""
    imgui.begin_disabled(block is not None and block.read_only)
    if block is not None and block.layout != ParameterBlockLayout.LIST:
        req = _render_grid_block(
            device, block, on_change, deferred_enum, page.key, differing_refs, buttons
        )
    else:
        req = _render_children(
            device,
            page.content,
            on_change,
            deferred_enum,
            page.key,
            needle,
            differing_refs,
            buttons,
        )
    imgui.end_disabled()
    return req


def _render_children(
    device: Device,
    children: tuple[UiNode, ...],
    on_change: Callable[[Device, str, str], None],
    deferred_enum: bool,
    prefix: str,
    needle: str = "",
    differing_refs: frozenset[str] = frozenset(),
    buttons: ButtonActions | None = None,
) -> EnumPopupRequest | None:
    popup_request: EnumPopupRequest | None = None
    pending_params: list[UiParameter | UiButton] = []
    table_idx = 0

    def flush() -> None:
        nonlocal popup_request, table_idx
        if not pending_params:
            return
        req = _render_param_table(
            device,
            pending_params,
            on_change,
            deferred_enum,
            f"{prefix}_{table_idx}",
            differing_refs,
            buttons,
        )
        table_idx += 1
        if req is not None:
            popup_request = req
        pending_params.clear()

    for node in children:
        if isinstance(node, UiParameter) and isinstance(node.widget, PictureWidget):
            if needle:
                continue
            flush()
            width = min(imgui.get_content_region_avail().x, px(_GRID_REFERENCE_WIDTH))
            _render_picture(node.widget, width)
        elif isinstance(node, UiParameter):
            if not needle or needle in node.label.lower():
                pending_params.append(node)
        elif isinstance(node, UiParameterBlock):
            if needle and not _node_matches(node, needle):
                continue
            flush()
            req = _render_block(
                device,
                node,
                on_change,
                deferred_enum,
                prefix,
                needle,
                differing_refs,
                buttons,
            )
            if req is not None:
                popup_request = req
        elif isinstance(node, UiSeparator):
            if needle:
                continue  # separators are noise while filtering
            flush()
            _render_separator(node)
        elif isinstance(node, UiButton):
            if not needle or needle in node.text.lower():
                pending_params.append(node)
        elif isinstance(node, UiComObject):
            pass

    flush()
    return popup_request


def _render_block(
    device: Device,
    block: UiParameterBlock,
    on_change: Callable[[Device, str, str], None],
    deferred_enum: bool,
    prefix: str,
    needle: str = "",
    differing_refs: frozenset[str] = frozenset(),
    buttons: ButtonActions | None = None,
) -> EnumPopupRequest | None:
    if block.hidden:
        return None
    block_prefix = f"{prefix}_{block.id}"

    if block.layout in (ParameterBlockLayout.GRID, ParameterBlockLayout.TABLE):
        imgui.begin_disabled(block.read_only)
        req = _render_grid_block(
            device,
            block,
            on_change,
            deferred_enum,
            block_prefix,
            differing_refs,
            buttons,
        )
        imgui.end_disabled()
        return req

    if block.inline:
        imgui.begin_disabled(block.read_only)
        req = _render_children(
            device,
            block.children,
            on_change,
            deferred_enum,
            block_prefix,
            needle,
            differing_refs,
            buttons,
        )
        imgui.end_disabled()
        return req

    label = _instance_label(block.text or block.name or block.id, block.id)
    param_count = count_parameters(block.children)
    popup_request: EnumPopupRequest | None = None
    if needle:  # while filtering, expand matching blocks so hits are visible
        imgui.set_next_item_open(True, imgui.Cond_.always)
    # "###" keeps the node's identity keyed on its unique path (block_prefix), so a parameter edit
    # that changes the block's label does not collapse the tree. block_prefix is already unique.
    is_open = imgui.tree_node(f"{label}###{block_prefix}")
    _track_help(block.help_context)
    imgui.same_line()
    imgui.text_disabled(f"({param_count})")
    if is_open:
        imgui.begin_disabled(block.read_only)
        req = _render_children(
            device,
            block.children,
            on_change,
            deferred_enum,
            block_prefix,
            needle,
            differing_refs,
            buttons,
        )
        imgui.end_disabled()
        if req is not None:
            popup_request = req
        imgui.tree_pop()
    return popup_request


def _grid_pos(cell: str | None) -> tuple[int, int] | None:
    if not cell:
        return None
    try:
        row, col = cell.split(",")
        return int(row), int(col)
    except ValueError:
        return None


def _column_width(spec: str, avail: float) -> float | None:
    """Pixel width of a Column ``Width`` ("45%" of the block, or plain pixels)."""
    spec = spec.strip()
    try:
        if spec.endswith("%"):
            return avail * float(spec[:-1]) / 100.0
        return px(float(spec))
    except ValueError:
        return None


def _render_grid_block(
    device: Device,
    block: UiParameterBlock,
    on_change: Callable[[Device, str, str], None],
    deferred_enum: bool,
    prefix: str,
    differing_refs: frozenset[str] = frozenset(),
    buttons: ButtonActions | None = None,
) -> EnumPopupRequest | None:
    """Lay out parameters, buttons and nested blocks positioned by their cell attribute into a grid."""
    popup_request: EnumPopupRequest | None = None
    cells_by_pos: dict[tuple[int, int], UiParameter | UiButton | UiParameterBlock] = {}
    labels_by_pos: dict[tuple[int, int], UiSeparator] = {}
    uncelled: list[UiParameter | UiButton] = []
    uncelled_blocks: list[UiParameterBlock] = []

    for node in block.children:
        if isinstance(node, UiParameter | UiButton | UiParameterBlock):
            pos = _grid_pos(node.cell)
            if pos is not None:
                cells_by_pos[pos] = node
            elif isinstance(node, UiParameterBlock):
                uncelled_blocks.append(node)
            else:
                uncelled.append(node)
        elif isinstance(node, UiSeparator):
            pos = _grid_pos(node.cell)
            if pos is not None:
                labels_by_pos[pos] = node

    if cells_by_pos or labels_by_pos:
        req = _render_grid_cells(
            device,
            block,
            cells_by_pos,
            labels_by_pos,
            on_change,
            deferred_enum,
            prefix,
            differing_refs,
            buttons,
        )
        if req is not None:
            popup_request = req

    if uncelled:
        req = _render_param_table(
            device, uncelled, on_change, deferred_enum, prefix, differing_refs, buttons
        )
        if req is not None:
            popup_request = req
    for nested in uncelled_blocks:
        req = _render_block(
            device,
            nested,
            on_change,
            deferred_enum,
            prefix,
            "",
            differing_refs,
            buttons,
        )
        if req is not None:
            popup_request = req

    return popup_request


def _render_grid_cells(
    device: Device,
    block: UiParameterBlock,
    cells_by_pos: dict[tuple[int, int], UiParameter | UiButton | UiParameterBlock],
    labels_by_pos: dict[tuple[int, int], UiSeparator],
    on_change: Callable[[Device, str, str], None],
    deferred_enum: bool,
    prefix: str,
    differing_refs: frozenset[str],
    buttons: ButtonActions | None,
) -> EnumPopupRequest | None:
    popup_request: EnumPopupRequest | None = None
    occupied = set(cells_by_pos) | set(labels_by_pos)
    max_row = max((r for r, _ in occupied), default=1)
    max_col = max((c for _, c in occupied), default=1)

    is_table = block.layout == ParameterBlockLayout.TABLE
    table_flags = (
        imgui.TableFlags_.no_saved_settings | imgui.TableFlags_.sizing_stretch_prop
    )
    if is_table:
        table_flags |= imgui.TableFlags_.borders | imgui.TableFlags_.row_bg

    has_row_labels = any(block.row_labels)
    has_col_headers = any(block.column_headers)
    col_offset = 1 if has_row_labels else 0
    declared_cols = max(max_col, len(block.column_headers), len(block.column_widths))
    total_cols = declared_cols + col_offset
    avail = imgui.get_content_region_avail().x
    # Percentage widths refer to the same page width on every grid, so grids line up.
    reference = min(avail, px(_GRID_REFERENCE_WIDTH))
    widths = [
        _column_width(block.column_widths[col], reference)
        if col < len(block.column_widths)
        else None
        for col in range(declared_cols)
    ]
    # Columns keep their declared widths, so a grid whose widths add up to more than the
    # page extends past its right edge instead of squeezing the last columns.
    stretch_cols = sum(w is None for w in widths) + col_offset
    outer_width = max(
        avail,
        sum(w for w in widths if w is not None)
        + stretch_cols * px(_MIN_STRETCH_COLUMN),
    )

    # Grid columns are exactly their declared width (no padding between them), so the columns
    # of grids above each other line up; fields leave a small gap to the next column instead.
    padding = imgui.get_style().cell_padding
    imgui.push_style_var(
        imgui.StyleVar_.cell_padding,
        imgui.ImVec2(padding.x if is_table else 0, padding.y / 4),
    )
    opened = imgui.begin_table(
        f"##grid_{prefix}", total_cols, table_flags, imgui.ImVec2(outer_width, 0)
    )
    imgui.pop_style_var()
    if not opened:
        return None
    if has_row_labels:
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_stretch, 1.0)
    for col, width in enumerate(widths):
        header = block.column_headers[col] if col < len(block.column_headers) else ""
        if width is not None:
            imgui.table_setup_column(header, imgui.TableColumnFlags_.width_fixed, width)
        else:
            imgui.table_setup_column(header, imgui.TableColumnFlags_.width_stretch, 1.0)
    if has_col_headers:
        imgui.table_headers_row()
    for row in range(1, max_row + 1):
        if row in block.collapsed_rows and not any(r == row for r, _ in occupied):
            continue
        imgui.table_next_row()
        if has_row_labels:
            imgui.table_set_column_index(0)
            label = block.row_labels[row - 1] if row - 1 < len(block.row_labels) else ""
            imgui.text_disabled(label)
        for col in range(1, max_col + 1):
            imgui.table_set_column_index(col - 1 + col_offset)
            node = cells_by_pos.get((row, col))
            sep = labels_by_pos.get((row, col))
            req = None
            if isinstance(node, UiParameterBlock):
                imgui.push_id(f"{row},{col}")
                req = _render_block(
                    device,
                    node,
                    on_change,
                    deferred_enum,
                    prefix,
                    "",
                    differing_refs,
                    buttons,
                )
                imgui.pop_id()
            elif isinstance(node, UiButton):
                _render_button(device, node, prefix, buttons)
            elif node is not None:
                req = _render_grid_param(
                    device, node, on_change, deferred_enum, differing_refs
                )
            elif sep is not None:
                _render_cell_separator(sep)
            if req is not None:
                popup_request = req
    imgui.end_table()
    return popup_request


def _render_cell_separator(sep: UiSeparator) -> None:
    """A separator in a grid cell: a headline or label as plain text, a ruler across."""
    if sep.text and sep.hint in ("Headline", None):
        imgui.align_text_to_frame_padding()
        imgui.text(sep.text)
    else:
        _render_separator(sep)


def _render_grid_param(
    device: Device,
    param: UiParameter,
    on_change: Callable[[Device, str, str], None],
    deferred_enum: bool,
    differing_refs: frozenset[str],
) -> EnumPopupRequest | None:
    if _shows_as_text(param):
        imgui.align_text_to_frame_padding()
        imgui.text(_value_display(param, param.value))
        _render_suffix(param.suffix)
        _track_help(param.help_context)
        return None
    imgui.set_next_item_width(-px(_CELL_GAP) - _suffix_width(param.suffix))
    widget_id = f"{device.node_id}_{param.ref_id}"
    # GRID/TABLE cells carry no label to tint, so mark a changed value by tinting
    # the widget's own text (combo preview / input), matching the table view.
    changed = param.value != param.default_value
    if changed:
        imgui.push_style_color(imgui.Col_.text, _CHANGED_COLOR)
    read_only = param.access == Access.READ
    imgui.begin_disabled(read_only)
    req = _render_device_param(
        device,
        param,
        widget_id,
        lambda v, d=device, p=param.ref_id: on_change(d, p, v),
        deferred_enum=deferred_enum,
        differs=param.ref_id in differing_refs,
    )
    imgui.end_disabled()
    _track_help(param.help_context)
    _render_suffix(param.suffix)
    if changed:
        imgui.pop_style_color()
        if not read_only and imgui.begin_popup_context_item(f"##reset_{widget_id}"):
            if imgui.menu_item(S.PARAM_RESET_DEFAULT, "", False)[0]:
                on_change(device, param.ref_id, param.default_value)
            imgui.end_popup()
    if req is not None:
        return EnumPopupRequest(device=device, param=req.param)
    return None


def _render_param_table(
    device: Device,
    params: list[UiParameter | UiButton],
    on_change: Callable[[Device, str, str], None],
    deferred_enum: bool,
    prefix: str,
    differing_refs: frozenset[str] = frozenset(),
    buttons: ButtonActions | None = None,
) -> EnumPopupRequest | None:
    if not params:
        return None
    popup_request: EnumPopupRequest | None = None
    table_flags = imgui.TableFlags_.no_saved_settings
    # Labels and values sit at the same positions as the columns of grids on the page.
    reference = min(imgui.get_content_region_avail().x, px(_GRID_REFERENCE_WIDTH))
    label_width = reference * _LIST_LABEL_SHARE
    padding = imgui.get_style().cell_padding
    imgui.push_style_var(imgui.StyleVar_.cell_padding, imgui.ImVec2(0, padding.y))
    opened = imgui.begin_table(
        f"##params_{prefix}", 2, table_flags, imgui.ImVec2(reference, 0)
    )
    imgui.pop_style_var()
    if opened:
        imgui.table_setup_column(
            "Name", imgui.TableColumnFlags_.width_fixed, label_width
        )
        # Values such as a row of radio buttons may reach past the column.
        imgui.table_setup_column(
            "Value",
            imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.no_clip,
        )
        for param in params:
            imgui.table_next_row()
            if isinstance(param, UiButton):
                imgui.table_set_column_index(1)
                _render_button(device, param, prefix, buttons)
                continue
            imgui.table_set_column_index(0)
            # Each level indents by two spaces, as grid row labels do with leading spaces.
            indent = param.indent_level * imgui.calc_text_size("  ").x
            if indent > 0:
                imgui.indent(indent)
            label = param.label
            differs = param.ref_id in differing_refs
            # In multi-edit a diverging value has no single "changed vs default" state to show.
            changed = not differs and param.value != param.default_value
            # Changed parameters stand out. A leading marker plus the colour means the
            # state is not signalled by colour alone.
            if changed:
                imgui.text_colored(_CHANGED_COLOR, "*")
                imgui.same_line(0, px(4))
                imgui.push_style_color(imgui.Col_.text, _CHANGED_COLOR)
                imgui.text_wrapped(label)
                imgui.pop_style_color()
                if imgui.is_item_hovered():
                    imgui.set_tooltip(
                        S.PARAM_CHANGED_TOOLTIP.format(default=_default_display(param))
                    )
            elif isinstance(param.widget, RawDataWidget):
                imgui.push_style_color(
                    imgui.Col_.text,
                    imgui.get_style_color_vec4(imgui.Col_.text_disabled),
                )
                imgui.text_wrapped(label)
                imgui.pop_style_color()
            else:
                imgui.text_wrapped(label)
            _track_help(param.help_context)
            if indent > 0:
                imgui.unindent(indent)
            imgui.table_set_column_index(1)
            if _shows_as_text(param):
                imgui.text(_value_display(param, param.value))
                _render_suffix(param.suffix)
                _track_help(param.help_context)
                continue
            imgui.set_next_item_width(-1 - _suffix_width(param.suffix))
            widget_id = f"{device.node_id}_{param.ref_id}"
            read_only = param.access == Access.READ
            imgui.begin_disabled(read_only)
            req = _render_device_param(
                device,
                param,
                widget_id,
                lambda v, d=device, p=param.ref_id: on_change(d, p, v),
                deferred_enum=deferred_enum,
                differs=differs,
            )
            imgui.end_disabled()
            _track_help(param.help_context)
            # Right-click a changed value to restore the application default.
            if (
                changed
                and not read_only
                and imgui.begin_popup_context_item(f"##reset_{widget_id}")
            ):
                if imgui.menu_item(S.PARAM_RESET_DEFAULT, "", False)[0]:
                    on_change(device, param.ref_id, param.default_value)
                imgui.end_popup()
            _render_suffix(param.suffix)
            if req is not None:
                popup_request = EnumPopupRequest(device=device, param=req.param)
        imgui.end_table()
    return popup_request


_INFO_COLOR = imgui.ImVec4(0.45, 0.72, 1.0, 1.0)
# Width at 100 % scaling a grid column without a declared width keeps when the grid is too wide.
_MIN_STRETCH_COLUMN = 80.0
# Page width at 100 % scaling that percentage column widths of a grid refer to.
_GRID_REFERENCE_WIDTH = 540.0
# Share of that width the labels of a parameter list take.
_LIST_LABEL_SHARE = 0.45
# Gap at 100 % scaling a field in a grid cell leaves to the next column.
_CELL_GAP = 6.0
# Characters a number field has room for at least.
_MIN_NUMBER_CHARS = 3
_SEPARATOR_ERROR_COLOR = imgui.ImVec4(1.0, 0.42, 0.42, 1.0)


def _wrap_width() -> float:
    """Width up to the visible right edge of the window or the current table column."""
    style = imgui.get_style()
    right = imgui.get_scroll_x() + imgui.get_window_width() - style.window_padding.x
    if imgui.get_scroll_max_y() > 0:
        right -= style.scrollbar_size
    x = imgui.get_cursor_pos_x()
    return max(min(right - x, imgui.get_content_region_avail().x), px(80))


def _wrapped_text(text: str, width: float) -> None:
    imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + width)
    imgui.text_unformatted(text)
    imgui.pop_text_wrap_pos()


def _aligned_text(text: str, alignment: str | None) -> None:
    avail = _wrap_width()
    width = imgui.calc_text_size(text).x
    if alignment in ("Center", "Right") and "\n" not in text and width < avail:
        offset = (avail - width) / 2 if alignment == "Center" else avail - width
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + offset)
        avail -= offset
    _wrapped_text(text, avail)


def _render_hint(text: str, color: imgui.ImVec4, glyph: str) -> None:
    """An information or error text in a tinted box with a round icon."""
    pad = px(8)
    icon = imgui.get_font_size() * 1.25
    width = _wrap_width()
    text_width = width - pad * 3 - icon
    text_height = imgui.calc_text_size(text, wrap_width=text_width).y
    top = imgui.get_cursor_screen_pos()
    box_max = imgui.ImVec2(top.x + width, top.y + max(text_height, icon) + pad * 2)
    draw = imgui.get_window_draw_list()
    rounding = imgui.get_style().frame_rounding
    draw.add_rect_filled(
        top,
        box_max,
        imgui.get_color_u32(imgui.ImVec4(color.x, color.y, color.z, 0.12)),
        rounding,
    )
    draw.add_rect(
        top,
        box_max,
        imgui.get_color_u32(imgui.ImVec4(color.x, color.y, color.z, 0.6)),
        rounding,
    )
    centre = imgui.ImVec2(top.x + pad + icon / 2, top.y + pad + icon / 2)
    draw.add_circle_filled(centre, icon / 2, imgui.get_color_u32(color))
    size = imgui.calc_text_size(glyph)
    draw.add_text(
        imgui.ImVec2(centre.x - size.x / 2, centre.y - size.y / 2),
        imgui.get_color_u32(imgui.ImVec4(1, 1, 1, 1)),
        glyph,
    )
    imgui.set_cursor_screen_pos(imgui.ImVec2(top.x + pad * 2 + icon, top.y + pad))
    _wrapped_text(text, text_width)
    imgui.set_cursor_screen_pos(imgui.ImVec2(top.x, box_max.y))
    imgui.dummy(imgui.ImVec2(width, 0))


def _render_separator(sep: UiSeparator) -> None:
    if sep.hint == "HorizontalRuler":
        if sep.text:
            imgui.separator_text(sep.text)
        else:
            imgui.separator()
    elif not sep.text:
        imgui.spacing()
    elif sep.hint == "Headline":
        imgui.separator_text(sep.text)
    elif sep.hint == "Information":
        _render_hint(sep.text, _INFO_COLOR, "i")
    elif sep.hint == "Error":
        _render_hint(sep.text, _SEPARATOR_ERROR_COLOR, "!")
    else:
        _aligned_text(sep.text, sep.alignment)


class EnumPopup:
    def __init__(
        self,
        popup_id: str,
        on_change: Callable[[Device, str, str], None],
    ) -> None:
        self._popup_id = popup_id
        self._on_change = on_change
        self._request: EnumPopupRequest | None = None
        self._active: EnumPopupRequest | None = None

    def request(self, device: Device, param: UiParameter) -> None:
        self._request = EnumPopupRequest(device=device, param=param)

    def render(self) -> None:
        if self._request is not None:
            self._active = self._request
            self._request = None
            imgui.open_popup(self._popup_id)

        if imgui.begin_popup(self._popup_id):
            target = self._active
            if target is not None and isinstance(target.param.widget, EnumWidget):
                for choice in target.param.widget.choices:
                    selected = str(choice.value) == target.param.value
                    if imgui.menu_item(choice.label, "", selected)[0]:
                        self._on_change(
                            target.device, target.param.ref_id, str(choice.value)
                        )
            imgui.end_popup()
        else:
            self._active = None
