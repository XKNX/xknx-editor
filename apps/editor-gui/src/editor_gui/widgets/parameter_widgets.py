from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.device import Device
from editor_gui.regional import regional_format
from editor_gui.widgets.backgrounds import (
    box_line,
    child_background,
    content_background,
    group_background,
    side_background,
)
from editor_gui.widgets.checkbox import checkbox, radio_button
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.dpi import px
from editor_gui.widgets.float_format import format_float, general_float, parse_float
from editor_gui.widgets.fonts import bold
from editor_gui.widgets.icons import Icon, Picture, draw_icon
from editor_gui.widgets.strings import S
from editor_gui.widgets.tables import data_table_id
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
from editor_gui.widgets.wrapping import (
    text_height,
    vertical_space,
    wrap_lines,
    wrapped_text,
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

# Parameters changed from their default are tinted to stand out (View > Highlight Changes).
_CHANGED_COLOR = imgui.ImVec4(0.36, 0.71, 1.0, 1.0)
_highlight_changes = True


def set_highlight_changes(on: bool) -> None:
    """Mark parameters changed from their default (colour, ``*`` and tooltip) or not."""
    global _highlight_changes
    _highlight_changes = on


_ERROR_COLOR = imgui.ImVec4(1.0, 0.42, 0.42, 1.0)


@dataclass(frozen=True)
class ButtonActions:
    """Callbacks for Button elements: click, enabled state with tooltip, and last error."""

    on_click: Callable[[Device, UiButton], None]
    state: Callable[[Device, UiButton], tuple[bool, str | None]]
    error: Callable[[Device, UiButton], str | None]


def _render_button(
    device: Device,
    button: UiButton,
    prefix: str,
    buttons: ButtonActions | None,
    *,
    fill: bool = False,
) -> None:
    """A Button; its icon is drawn in front of the text. In a grid cell (``fill``) it takes the
    width of the cell, less a margin on both sides."""
    enabled, tooltip = (
        buttons.state(device, button) if buttons is not None else (False, None)
    )
    enabled = enabled and not button.read_only
    icon = _get_icon(button.icon) if _get_icon is not None and button.icon else None
    width = 0.0
    if fill:
        margin = px(_BUTTON_MARGIN)
        cell = imgui.get_content_region_avail().x - 2 * margin
        if cell >= imgui.get_frame_height():
            width = cell
            imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + margin)
    imgui.begin_disabled(not enabled)
    if icon is None:
        clicked = imgui.button(
            f"{button.text}###btn_{prefix}_{button.id}", imgui.ImVec2(width, 0)
        )
    else:
        clicked = _icon_button(icon, button.text, f"###btn_{prefix}_{button.id}", width)
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


def _icon_button(icon: Icon, text: str, button_id: str, width: float) -> bool:
    """A button showing ``icon`` before ``text``; ``width`` 0 sizes it to its content."""
    style = imgui.get_style()
    size = imgui.get_text_line_height()
    gap = style.item_inner_spacing.x if text else 0.0
    content = size + gap + (imgui.calc_text_size(text).x if text else 0.0)
    clicked = imgui.button(
        button_id, imgui.ImVec2(width or content + style.frame_padding.x * 2, 0)
    )
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    x = low.x + (high.x - low.x - content) / 2
    y = low.y + (high.y - low.y - size) / 2
    draw_icon(icon, imgui.ImVec2(x, y), size)
    if text:
        imgui.get_window_draw_list().add_text(
            imgui.ImVec2(x + size + gap, low.y + style.frame_padding.y),
            imgui.get_color_u32(imgui.Col_.text),
            text,
        )
    return clicked


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


def _reset_menu(widget_id: str, hovered: bool, reset: Callable[[], None]) -> None:
    """Right-click menu restoring the default; ``hovered`` covers every item the value is
    drawn with (e.g. all radio buttons) and its label."""
    popup = f"##reset_{widget_id}"
    if hovered and imgui.is_mouse_released(imgui.MouseButton_.right):
        imgui.open_popup(popup)
    if imgui.begin_popup(popup):
        if imgui.menu_item(S.PARAM_RESET_DEFAULT, "", False)[0]:
            reset()
        imgui.end_popup()


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
# Loads an icon of the product's IconFile baggage while a parameter tree is drawn.
_get_icon: Callable[[str], Icon | None] | None = None


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
    # Only the part within ``width`` is drawn, cut by its texture coordinates: a clip rect would
    # be lost where a grid's table merges its draw channels.
    draw_list = imgui.get_window_draw_list()
    left, right = start.x, start.x + width
    while True:
        x0, x1 = max(x, left), min(x + w, right)
        if x0 < x1:
            draw_list.add_image(
                picture.texture,
                imgui.ImVec2(x0, start.y),
                imgui.ImVec2(x1, start.y + h),
                imgui.ImVec2((x0 - x) / w, 0.0),
                imgui.ImVec2((x1 - x) / w, 1.0),
            )
        x += w
        if alignment != HorizontalAlignment.REPEAT or x >= right:
            break
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
                if begin_combo(f"##{widget_id}", preview):
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
                changed, new_checked = checkbox(f"##{widget_id}", checked, centred=True)
                if changed:
                    on_change("1" if new_checked else "0")
        case RawDataWidget():
            pass
        case PictureWidget() as w:
            _render_picture(w, imgui.get_content_region_avail().x)
        case _:
            _render_text_param(widget_id, param.value, on_change, differs)
    return None


def _small_radio(label: str, active: bool, *, first: bool) -> bool:
    """A radio button about the size of the text, centred in a row of full-height fields;
    the ones after the ``first`` follow on its line."""
    return radio_button(label, active, centred=first)


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
    _set_number_field_width(spin=False)
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


# Gap between a field and the suffix (e.g. a unit) after it, at 100 % scaling.
_SUFFIX_GAP = 5.0


def _suffix_width(suffix: str | None) -> float:
    """Room a suffix shown after a field takes."""
    if not suffix:
        return 0.0
    return imgui.calc_text_size(suffix).x + px(_SUFFIX_GAP)


def _render_suffix(suffix: str | None) -> None:
    """``suffix`` (e.g. a unit) after the previous field, on the field's text line."""
    if suffix:
        imgui.same_line(0.0, px(_SUFFIX_GAP))
        imgui.align_text_to_frame_padding()
        imgui.text(suffix)


def _hint(widget: TimeWidget) -> str | None:
    return widget.hint.value if widget.hint is not None else None


# Whether the page being drawn is in the narrow editor (see render_ui_tree).
_narrow_page = [False]


def _spin_width() -> float:
    return imgui.get_frame_height() * 0.6


def _set_number_field_width(*, spin: bool = True) -> None:
    """A number field fills its column like the other fields; its spin arrows are drawn inside
    its right end."""
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
    if begin_combo(f"##{widget_id}", S.PARAM_DIFFERS):
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
    # On a narrow page (a phone) the field keeps its whole width for the number: arrows that
    # small are hard to hit with a finger, and the keyboard types the number.
    spin = not _narrow_page[0]
    _set_number_field_width(spin=spin)
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
    steps = _spin_buttons(widget_id, enabled=not differs) if spin else 0
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
    "G, 1"…"G, 16" / "ECG, 1"…"ECG, 64" (matching the "G1"/"ECG1"… labelling). Skips labels that
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
    """Whether ``nodes`` show something. An inline block only counts with content of its own: one
    whose pages all moved into the tree (a channel holding its pages in an inline list) is empty,
    so its channel opens its first page instead of an empty one."""
    return any(
        isinstance(n, (UiParameter, UiButton, UiSeparator))
        or (
            isinstance(n, UiParameterBlock)
            and not n.hidden
            and (not n.inline or _has_content(n.children))
        )
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
# (device node id, page key) of the page shown last: a page that comes up selects its help, as a
# click on it does (also the page a device opens on).
_shown_page: list[tuple[int, str]] = []
# (top, bottom) of the selected page's row in the page tree drawn last, on screen.
_selected_row: list[tuple[float, float]] = []
# Space left of the page tree's top level, at 100 % scaling.
_TREE_INDENT = 4.0
# How far the selected page's band starts from the tree's left edge, at 100 % scaling.
_SELECTED_INSET = 6.0
# Whether the page tree drawn last shows its scrollbar (beside the page instead of the line).
_tree_scrolls: list[bool] = []
# Device node id -> (UI nodes, filter, pages) of the last page tree built.
_page_cache: dict[int, tuple[list[UiNode], str, tuple[ParameterPage, ...]]] = {}

_TREE_WIDTH = 210.0
# Narrower than this at 100 % scaling (a phone), the pages are a drop-down above the page, and
# the help below it takes at most this share of the height (it keeps its own height then, see
# stacked_layout).
_STACKED_WIDTH = 480.0
_STACKED_FOOTER_SHARE = 0.7
# The least share of the window's height the page and help get there (the window scrolls then).
_STACKED_PAGE_SHARE = 0.75
# Indent of a page below the one it belongs to in that drop-down, at 100 % scaling.
_CHOICE_INDENT = 14.0
# A page narrower than this at 100 % scaling has each parameter label above its field.
_LABELS_ABOVE_WIDTH = 420.0


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


def _render_page_choice(
    pages: tuple[ParameterPage, ...],
    path: list[ParameterPage],
    selected: str | None,
    width: float,
) -> str | None:
    """The pages as a drop-down (a narrow editor): the selected page with the pages above it,
    and every page indented below the one it belongs to. Returns the key of a page chosen this
    frame."""
    chosen: list[str] = []

    def rows(items: tuple[ParameterPage, ...], depth: int) -> None:
        for page in items:
            indent = depth * px(_CHOICE_INDENT)
            if indent:
                imgui.indent(indent)
            if page.selectable:
                is_selected = page.key == selected
                if imgui.selectable(f"{page.label}##{page.key}", is_selected)[0]:
                    chosen.append(page.key)
                if is_selected and imgui.is_window_appearing():
                    imgui.set_scroll_here_y()
            else:
                imgui.text_disabled(page.label)
            if indent:
                imgui.unindent(indent)
            rows(page.children, depth + 1)

    imgui.set_next_item_width(width)
    preview = " / ".join(page.label.strip() for page in path)
    if begin_combo("##page_choice", preview, imgui.ComboFlags_.height_largest):
        rows(pages, 0)
        imgui.end_combo()
    return chosen[0] if chosen else None


def stacked_layout() -> bool:
    """Whether :func:`render_ui_tree` in the current window puts the page tree above the page
    (a narrow window, a phone)."""
    inner = internal.get_current_window().inner_rect
    return inner.max.x - inner.min.x < px(_STACKED_WIDTH)


def render_ui_tree(
    device: Device,
    nodes: list[UiNode],
    on_change: Callable[[Device, str, str], None],
    deferred_enum: bool = False,
    filter_text: str = "",
    differing_refs: frozenset[str] = frozenset(),
    buttons: ButtonActions | None = None,
    *,
    get_icon: Callable[[str], Icon | None] | None = None,
    get_picture: Callable[[str], Picture | None] | None = None,
    footer: Callable[[float], None] | None = None,
    footer_height: float = 0.0,
) -> EnumPopupRequest | None:
    """The page tree beside the selected page, with filter and multi-device diff markers, filling
    the rest of the window.

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
    if selected is not None:
        _show_page_help(device, selected)

    popup_request: EnumPopupRequest | None = None
    # The page tree, the page and the help fill the rest of the window up to its edges, over its
    # padding, parted by lines only.
    window = internal.get_current_window()
    inner = window.inner_rect
    top = imgui.get_cursor_screen_pos().y
    height = max(inner.max.y - top, imgui.get_frame_height() * 4)
    reached = imgui.ImVec2(window.dc.cursor_max_pos.x, window.dc.cursor_max_pos.y)
    imgui.set_cursor_screen_pos(imgui.ImVec2(inner.min.x, top))
    imgui.push_clip_rect(inner.min, inner.max, False)
    scroll = imgui.WindowFlags_.horizontal_scrollbar

    def tree(size: imgui.ImVec2) -> None:
        _selected_row.clear()
        with child_background(side_background()):
            tree_open = imgui.begin_child("##page_tree", size, 0, scroll)
        if tree_open:
            tree_rect = internal.get_current_window().inner_rect
            _tree_scrolls[:] = [internal.get_current_window().scrollbar_y]
            # The first row's band starts at the tree's top, level with the page's top line.
            _move_cursor_down(imgui.get_style().item_spacing.y / 2)
            imgui.indent(px(_TREE_INDENT))
            clicked = _render_page_tree(
                pages,
                selected.key if selected else None,
                opened,
                bool(needle),
                get_icon,
            )
            imgui.unindent(px(_TREE_INDENT))
            # An item after the tree's last move of the cursor (imgui asserts on a cursor moved
            # past the content without one, e.g. when the filter leaves no page).
            imgui.dummy(imgui.ImVec2(0.0, 0.0))
            if clicked is not None:
                _selected_pages[device.node_id] = clicked
            # The selected row as far as it is scrolled into view.
            if _selected_row:
                row_top, row_bottom = _selected_row[0]
                row_top = max(row_top, tree_rect.min.y)
                row_bottom = min(row_bottom, tree_rect.max.y)
                _selected_row[:] = (
                    [(row_top, row_bottom)] if row_top < row_bottom else []
                )
        imgui.end_child()

    def page(size: imgui.ImVec2) -> EnumPopupRequest | None:
        global _get_picture, _get_icon
        request: EnumPopupRequest | None = None
        with child_background(content_background()):
            content_open = imgui.begin_child(
                "##page_content",
                size,
                imgui.ChildFlags_.always_use_window_padding,
                scroll,
            )
        if content_open:
            _draw_page_top_line()
        if content_open and selected is not None:
            _get_picture = get_picture
            _get_icon = get_icon
            _narrow_page[0] = stacked
            try:
                request = _render_page(
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
                _get_icon = None
                _narrow_page[0] = False
        imgui.end_child()
        return request

    width = inner.max.x - inner.min.x
    stacked = stacked_layout()
    if stacked:
        # Narrow (a phone): the pages as a drop-down above the page, which gets the rest of the
        # height; the help below it takes at most a share of the height.
        least = imgui.get_frame_height() * 3
        footer_height = min(
            footer_height, (inner.max.y - inner.min.y) * _STACKED_FOOTER_SHARE
        )
        pad = imgui.get_style().window_padding.x
        imgui.set_cursor_screen_pos(
            imgui.ImVec2(inner.min.x + pad, top + imgui.get_style().item_spacing.y)
        )
        chosen = _render_page_choice(
            pages, path or [], selected.key if selected else None, width - 2 * pad
        )
        if chosen is not None:
            _selected_pages[device.node_id] = chosen
        line_top = imgui.get_item_rect_max().y + imgui.get_style().item_spacing.y
        imgui.get_window_draw_list().add_rect_filled(
            imgui.ImVec2(inner.min.x, line_top),
            imgui.ImVec2(inner.max.x, line_top + px(1.0)),
            imgui.get_color_u32(imgui.Col_.separator),
        )
        imgui.set_cursor_screen_pos(imgui.ImVec2(inner.min.x, line_top + px(1.0)))
        page_top = imgui.get_cursor_screen_pos().y
        # On a short screen the page keeps most of the window's height and the window scrolls:
        # its upper part (device, buttons, filter) moves out of view.
        # (From where the page is when not scrolled: scrolling does not make it grow.)
        page_height = max(
            inner.max.y - (page_top + window.scroll.y) - footer_height,
            least,
            (inner.max.y - inner.min.y) * _STACKED_PAGE_SHARE - footer_height,
        )
        bottom = page_top + page_height + footer_height
        popup_request = page(imgui.ImVec2(width, page_height))
        if footer is not None:
            imgui.set_cursor_screen_pos(
                imgui.ImVec2(inner.min.x, imgui.get_cursor_screen_pos().y)
            )
            footer(bottom - imgui.get_cursor_screen_pos().y)
    else:
        flags = imgui.TableFlags_.resizable | imgui.TableFlags_.borders_inner_v
        imgui.push_style_var(imgui.StyleVar_.cell_padding, imgui.ImVec2(0.0, 0.0))
        # The table's border only marks where the columns are resized; the line is drawn below.
        imgui.push_style_color(
            imgui.Col_.table_border_light, imgui.ImVec4(0.0, 0.0, 0.0, 0.0)
        )
        # Its id follows the tables' generation: restoring the default layout resets the
        # width the page tree was dragged to, too.
        table_open = imgui.begin_table(
            data_table_id(f"##pages_{device.node_id}"),
            2,
            flags,
            imgui.ImVec2(width, height),
        )
        imgui.pop_style_color()
        imgui.pop_style_var()
        if table_open:
            imgui.table_setup_column(
                "##tree", imgui.TableColumnFlags_.width_fixed, px(_TREE_WIDTH)
            )
            imgui.table_setup_column("##page", imgui.TableColumnFlags_.width_stretch)
            imgui.table_next_row()
            imgui.table_next_column()
            tree(imgui.ImVec2(0, height))
            # The line runs along the tree, from its top to its bottom.
            line_left = imgui.get_item_rect_max().x
            line_top = imgui.get_item_rect_min().y
            line_bottom = imgui.get_item_rect_max().y
            imgui.table_next_column()
            # One pixel beside the table's border: the line between tree and page is 2 px wide.
            _move_cursor_right(px(1.0))
            line_right = imgui.get_cursor_screen_pos().x
            bottom = top + height
            page_height = height
            if footer is not None:
                page_height = max(height - footer_height, imgui.get_frame_height() * 3)
            popup_request = page(imgui.ImVec2(0, page_height))
            if footer is not None:
                _move_cursor_right(px(1.0))
                footer(bottom - imgui.get_cursor_screen_pos().y)
            imgui.end_table()
            _draw_page_line(line_left, line_right, line_top, line_bottom)
    imgui.pop_clip_rect()
    # The area ends at the window's edges, in its padding: it leaves nothing to scroll to (but
    # downwards where it is narrow, when the page reaches below the window).
    end = window.dc.cursor_max_pos
    work = window.work_rect
    window.dc.cursor_max_pos = imgui.ImVec2(
        max(reached.x, min(end.x, work.max.x)),
        max(reached.y, end.y if stacked else min(end.y, work.max.y)),
    )
    return popup_request


def _move_cursor_down(distance: float) -> None:
    pos = imgui.get_cursor_screen_pos()
    imgui.set_cursor_screen_pos(imgui.ImVec2(pos.x, pos.y + distance))


def _draw_page_top_line() -> None:
    """The thin line along the top of the page, level with the top line of the tree's first box."""
    page = internal.get_current_window()
    top_left = page.pos
    right = page.pos.x + page.size.x
    draw_list = imgui.get_window_draw_list()
    # Over the page's padding, which clips what is drawn into it.
    draw_list.push_clip_rect(top_left, imgui.ImVec2(right, top_left.y + 1.0), False)
    draw_list.add_rect_filled(
        top_left,
        imgui.ImVec2(right, top_left.y + 1.0),
        imgui.get_color_u32(box_line()),
    )
    draw_list.pop_clip_rect()


def _move_cursor_right(distance: float) -> None:
    pos = imgui.get_cursor_screen_pos()
    imgui.set_cursor_screen_pos(imgui.ImVec2(pos.x + distance, pos.y))


def _draw_page_line(left: float, right: float, top: float, bottom: float) -> None:
    """The line between the page tree and the page, open beside the selected page, which has the
    page's background, so the two join."""
    draw_list = imgui.get_window_draw_list()
    if _tree_scrolls and _tree_scrolls[0]:
        # The tree's scrollbar parts it from the page.
        draw_list.add_rect_filled(
            imgui.ImVec2(left, top),
            imgui.ImVec2(right, bottom),
            imgui.get_color_u32(content_background()),
        )
        return
    draw_list.add_rect_filled(
        imgui.ImVec2(left, top),
        imgui.ImVec2(right, bottom),
        imgui.get_color_u32(imgui.Col_.separator),
    )
    if _selected_row:
        # Between the row's thin lines, which meet the line at its top and bottom.
        row_top, row_bottom = _selected_row[0]
        draw_list.add_rect_filled(
            imgui.ImVec2(left, row_top + 1.0),
            imgui.ImVec2(right, row_bottom),
            imgui.get_color_u32(content_background()),
        )


def _show_page_help(device: Device, page: ParameterPage) -> None:
    shown = (device.node_id, page.key)
    if _shown_page == [shown]:
        return
    _shown_page[:] = [shown]
    if page.block is not None and page.block.help_context:
        _selected_help[:] = [page.block.help_context]


def _render_page_tree(
    pages: tuple[ParameterPage, ...],
    selected: str | None,
    opened: frozenset[str],
    open_all: bool,
    get_icon: Callable[[str], Icon | None] | None,
    *,
    top_level: bool = True,
) -> str | None:
    """Draw ``pages`` as tree nodes; returns the key of a page clicked this frame. A top-level
    page with pages below is a group, boxed with them."""
    clicked: str | None = None
    icon_size = imgui.get_font_size()
    space = imgui.calc_text_size(" ").x
    for page in pages:
        # A row spans the tree's width, so the selected one joins the page beside it, and is as
        # high as a field.
        flags = (
            imgui.TreeNodeFlags_.span_full_width | imgui.TreeNodeFlags_.frame_padding
        )
        if not page.children:
            flags |= (
                imgui.TreeNodeFlags_.leaf | imgui.TreeNodeFlags_.no_tree_push_on_open
            )
        if page.selectable:
            flags |= (
                imgui.TreeNodeFlags_.open_on_arrow
                | imgui.TreeNodeFlags_.open_on_double_click
            )
        if open_all:
            imgui.set_next_item_open(True, imgui.Cond_.always)
        elif page.key in opened:
            imgui.set_next_item_open(True, imgui.Cond_.once)
        icon = get_icon(page.icon) if get_icon is not None and page.icon else None
        pad = " " * math.ceil((icon_size + space) / space) if icon is not None else ""
        is_selected = page.key == selected
        is_group = top_level and bool(page.children)
        row_top, row_bottom = _row_band()
        if is_group:
            _draw_band(row_top, row_bottom, group_background(), 0.0)
        if is_selected:
            # The selected page is a band in the page's background, open toward the page, which it
            # joins; hovering or pressing it draws nothing over it.
            _draw_band(row_top, row_bottom, content_background(), px(_SELECTED_INSET))
            _selected_row[:] = [(row_top, row_bottom)]
            imgui.push_style_color(imgui.Col_.header_hovered, imgui.ImVec4(0, 0, 0, 0))
            imgui.push_style_color(imgui.Col_.header_active, imgui.ImVec4(0, 0, 0, 0))
        # Where the row's own content starts: its rect spans the tree's full width.
        row_x = imgui.get_cursor_screen_pos().x
        is_open = imgui.tree_node_ex(f"{pad}{page.label}###{page.key}", flags)
        if is_selected:
            imgui.pop_style_color(2)
        if icon is not None:
            top = imgui.get_item_rect_min()
            x = row_x + imgui.get_tree_node_to_label_spacing()
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
                page.children, selected, opened, open_all, get_icon, top_level=False
            )
            clicked = clicked or below
            imgui.tree_pop()
            if is_group:
                # The line closing the group's box below its last page.
                _draw_box_line(_row_band()[0])
    return clicked


def _row_band() -> tuple[float, float]:
    """Top and bottom on screen of the tree row drawn next, with half the spacing to the rows
    above and below it, so the bands of neighbouring rows touch."""
    half = imgui.get_style().item_spacing.y / 2
    top = imgui.get_cursor_screen_pos().y
    return top - half, top + imgui.get_frame_height() + half


def _draw_band(top: float, bottom: float, color: imgui.ImVec4, inset: float) -> None:
    """A row's band across the tree in ``color``, ``inset`` from its left edge, with a thin line
    above and below it (and left of it when inset). The line below is the first line of the next
    row, so the lines of neighbouring bands coincide."""
    inner = internal.get_current_window().inner_rect
    left, right = inner.min.x + inset, inner.max.x
    draw_list = imgui.get_window_draw_list()
    draw_list.add_rect_filled(
        imgui.ImVec2(left, top), imgui.ImVec2(right, bottom), imgui.get_color_u32(color)
    )
    _draw_box_line(top, left)
    _draw_box_line(bottom, left)
    if inset:
        draw_list.add_rect_filled(
            imgui.ImVec2(left, top),
            imgui.ImVec2(left + 1.0, bottom + 1.0),
            imgui.get_color_u32(box_line()),
        )


def _draw_box_line(y: float, left: float | None = None) -> None:
    inner = internal.get_current_window().inner_rect
    x = inner.min.x if left is None else left
    imgui.get_window_draw_list().add_rect_filled(
        imgui.ImVec2(x, y),
        imgui.ImVec2(inner.max.x, y + 1.0),
        imgui.get_color_u32(box_line()),
    )


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
    # Whether the last thing drawn was a table (a grid or a list of parameters): the next one
    # then joins it row against row.
    after_grid = False

    def join_rows() -> None:
        if after_grid:
            imgui.set_cursor_pos_y(
                imgui.get_cursor_pos_y() - imgui.get_style().item_spacing.y
            )

    def flush() -> None:
        nonlocal popup_request, table_idx, after_grid
        if not pending_params:
            return
        join_rows()
        after_grid = True
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
            after_grid = False
        elif isinstance(node, UiParameter):
            if not needle or needle in node.label.lower():
                pending_params.append(node)
        elif isinstance(node, UiParameterBlock):
            if needle and not _node_matches(node, needle):
                continue
            flush()
            if node.hidden:
                continue
            is_grid = node.layout in (
                ParameterBlockLayout.GRID,
                ParameterBlockLayout.TABLE,
            )
            if is_grid:
                join_rows()
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
            after_grid = is_grid
            if req is not None:
                popup_request = req
        elif isinstance(node, UiSeparator):
            if needle:
                continue  # separators are noise while filtering
            flush()
            _render_separator(node)
            after_grid = False
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


def _indented_cell_text(text: str, origin: float) -> str:
    """``text`` without the leading spaces of its lines, the cursor moved to the indent level
    they stand for. Authors indent grid labels with spaces to line them up with the indented
    parameters of the list; how far spaces reach depends on the font, so the text goes to the
    nearest indent level (counted from the grid's left edge ``origin``) instead."""
    spaces = len(text) - len(text.lstrip(" "))
    if not spaces:
        return text
    x = imgui.get_cursor_screen_pos().x
    natural = x - origin + spaces * imgui.calc_text_size(" ").x
    step = px(_INDENT_STEP)
    imgui.set_cursor_screen_pos(
        imgui.ImVec2(
            origin + round(natural / step) * step, imgui.get_cursor_screen_pos().y
        )
    )
    return "\n".join(line.lstrip(" ") for line in text.split("\n"))


def _keep_column_starts(
    widths: list[float | None], min_width: float
) -> list[float | None]:
    """Widths that keep every column at its declared start. A table column is at least
    ``min_width`` wide (the frame padding), so a narrower one (e.g. 1 %) would push the
    following columns right; the next column gives the difference back."""
    kept: list[float | None] = []
    excess = 0.0
    for width in widths:
        if width is None:
            kept.append(None)
            excess = 0.0
            continue
        width -= excess
        excess = max(0.0, min_width - width)
        kept.append(width + excess)
    return kept


def _grid_pos(
    cell: str | None, rows: int = 0, columns: int = 0
) -> tuple[int, int] | None:
    """The 1-based ``(row, column)`` of a ``Cell`` attribute. With declared ``rows`` and
    ``columns``, a cell beyond them lands in the last one (e.g. a one-row grid whose cells name
    the channel number as their row)."""
    if not cell:
        return None
    try:
        row, col = (int(part) for part in cell.split(","))
    except ValueError:
        return None
    if rows:
        row = min(row, rows)
    if columns:
        col = min(col, columns)
    return row, col


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
    rows, columns = len(block.row_labels), len(block.column_widths)

    for node in block.children:
        if isinstance(node, UiParameter | UiButton | UiParameterBlock):
            pos = _grid_pos(node.cell, rows, columns)
            if pos is not None:
                cells_by_pos[pos] = node
            elif isinstance(node, UiParameterBlock):
                uncelled_blocks.append(node)
            else:
                uncelled.append(node)
        elif isinstance(node, UiSeparator):
            pos = _grid_pos(node.cell, rows, columns)
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
    widths = _keep_column_starts(widths, _MIN_GRID_COLUMN)
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
    # A table column is at least the frame padding wide; a narrow first column (e.g. 1 %) must
    # keep its declared width so the next column starts where declared. The padding only
    # applies while the table lays out its columns, before anything in a cell is drawn.
    frame_padding = imgui.get_style().frame_padding
    imgui.push_style_var(
        imgui.StyleVar_.frame_padding, imgui.ImVec2(_MIN_GRID_COLUMN, frame_padding.y)
    )
    imgui.push_style_var(
        imgui.StyleVar_.cell_padding,
        imgui.ImVec2(padding.x if is_table else 0, padding.y / 4),
    )
    origin = imgui.get_cursor_screen_pos().x
    opened = imgui.begin_table(
        f"##grid_{prefix}", total_cols, table_flags, imgui.ImVec2(outer_width, 0)
    )
    imgui.pop_style_var()
    if not opened:
        imgui.pop_style_var()
        return None
    layout_padding = True
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
        imgui.pop_style_var()
        layout_padding = False
    for row in range(1, max_row + 1):
        if row in block.collapsed_rows and not any(r == row for r, _ in occupied):
            continue
        imgui.table_next_row()
        if layout_padding:
            imgui.pop_style_var()
            layout_padding = False
        if has_row_labels:
            imgui.table_set_column_index(0)
            label = block.row_labels[row - 1] if row - 1 < len(block.row_labels) else ""
            imgui.text_disabled(label)
        # The cells of a row are centred on its tallest one (e.g. a label of two lines). A
        # label's height is known once it is drawn at its cell's width, so the row uses the
        # heights its labels had in the previous frame.
        frame_height = imgui.get_frame_height()
        row_key = f"{prefix}:{row}"
        estimate = max(
            [0.0]
            + [
                _celltext_height(sep, widths[col - 1] if col <= len(widths) else None)
                for (sep_row, col), sep in labels_by_pos.items()
                if sep_row == row
            ]
        )
        row_height = max(frame_height, _label_heights.get(row_key, estimate))
        drawn_height = 0.0
        for col in range(1, max_col + 1):
            imgui.table_set_column_index(col - 1 + col_offset)
            node = cells_by_pos.get((row, col))
            sep = labels_by_pos.get((row, col))
            req = None
            if row_height > frame_height and isinstance(node, UiParameter | UiButton):
                imgui.set_cursor_pos_y(
                    imgui.get_cursor_pos_y() + (row_height - frame_height) / 2
                )
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
                _render_button(device, node, prefix, buttons, fill=True)
            elif node is not None:
                req = _render_grid_param(
                    device, node, on_change, deferred_enum, differing_refs
                )
            elif sep is not None:
                cell_key = f"{row_key}:{col}"
                own = _label_heights.get(
                    cell_key, _celltext_height(sep, imgui.get_content_region_avail().x)
                )
                _label_heights[cell_key] = _render_cell_separator(
                    sep, origin, row_height, own
                )
                drawn_height = max(drawn_height, _label_heights[cell_key])
            if req is not None:
                popup_request = req
        _label_heights[row_key] = drawn_height
    if layout_padding:
        imgui.pop_style_var()
    imgui.end_table()
    return popup_request


def _celltext_height(sep: UiSeparator, width: float | None) -> float:
    """Height of a label separator's text in a cell ``width`` wide (0 for other separators)."""
    if not sep.text or sep.hint not in ("Headline", None):
        return 0.0
    text = "\n".join(line.lstrip(" ") for line in sep.text.split("\n"))
    if width:
        width = max(width - px(_CELL_GAP), 1.0)
    if sep.hint == "Headline":
        with bold(_HEADLINE_SCALE):
            return imgui.calc_text_size(text, wrap_width=width if width else -1.0).y
    return imgui.calc_text_size(text, wrap_width=width if width else -1.0).y


def _render_cell_separator(
    sep: UiSeparator, origin: float, row_height: float, height: float
) -> float:
    """A separator in a grid cell: a headline in bold, a label as plain text, a ruler across.
    Text wraps where the fields of the cell end (the cell's right edge less the gap to the next
    column); a label ``height`` high is centred in a row ``row_height`` high. Returns the height
    of the text as drawn."""
    if sep.text and sep.hint in ("Headline", None):
        imgui.set_cursor_pos_y(
            imgui.get_cursor_pos_y() + max(0.0, (row_height - height) / 2)
        )
        right = (
            imgui.get_cursor_pos_x()
            + imgui.get_content_region_avail().x
            - px(_CELL_GAP)
        )
        text = _indented_cell_text(sep.text, origin)
        imgui.push_text_wrap_pos(max(right, imgui.get_cursor_pos_x() + 1.0))
        if sep.hint == "Headline":
            with bold(_HEADLINE_SCALE):
                imgui.text_unformatted(text)
        else:
            imgui.text_unformatted(text)
        imgui.pop_text_wrap_pos()
        return imgui.get_item_rect_size().y
    _render_separator(sep)
    return 0.0


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
    widget_id = f"{device.node_id}_{param.ref_id}"
    # GRID/TABLE cells carry no label to tint, so mark a changed value by tinting
    # the widget's own text (combo preview / input), matching the table view.
    changed = param.value != param.default_value
    tinted = changed and _highlight_changes
    if tinted:
        imgui.push_style_color(imgui.Col_.text, _CHANGED_COLOR)
    read_only = param.access == Access.READ
    imgui.begin_group()
    imgui.set_next_item_width(-px(_CELL_GAP) - _suffix_width(param.suffix))
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
    imgui.end_group()
    if tinted:
        imgui.pop_style_color()
    if changed and not read_only:
        _reset_menu(
            widget_id,
            imgui.is_item_hovered(),
            lambda: on_change(device, param.ref_id, param.default_value),
        )
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
    # On a narrow page (a phone) each label is above its field, which gets the whole width.
    above = reference < px(_LABELS_ABOVE_WIDTH)
    padding = imgui.get_style().cell_padding
    imgui.push_style_var(imgui.StyleVar_.cell_padding, imgui.ImVec2(0, padding.y))
    opened = imgui.begin_table(
        f"##params_{prefix}", 1 if above else 2, table_flags, imgui.ImVec2(reference, 0)
    )
    imgui.pop_style_var()
    if opened:
        if not above:
            imgui.table_setup_column(
                "Name", imgui.TableColumnFlags_.width_fixed, label_width
            )
        # Values such as a row of radio buttons may reach past the column.
        imgui.table_setup_column(
            "Value",
            imgui.TableColumnFlags_.width_stretch | imgui.TableColumnFlags_.no_clip,
        )
        value_column = 0 if above else 1
        # Every row is as high as one with an input field.
        row_height = imgui.get_frame_height() + 2 * padding.y
        for param in params:
            imgui.table_next_row(
                imgui.TableRowFlags_.none, 0.0 if above else row_height
            )
            if isinstance(param, UiButton):
                imgui.table_set_column_index(value_column)
                _render_button(device, param, prefix, buttons)
                continue
            imgui.table_set_column_index(0)
            # Each level indents by one step, the levels space-indented grid labels are placed on
            # (see _indented_cell_text).
            indent = param.indent_level * px(_INDENT_STEP)
            if indent > 0:
                imgui.indent(indent)
            label = param.label
            differs = param.ref_id in differing_refs
            imgui.align_text_to_frame_padding()
            # In multi-edit a diverging value has no single "changed vs default" state to show.
            changed = not differs and param.value != param.default_value
            # Changed parameters stand out. A leading marker plus the colour means the
            # state is not signalled by colour alone.
            if changed and _highlight_changes:
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
            label_hovered = imgui.is_item_hovered()
            _track_help(param.help_context)
            if indent > 0:
                imgui.unindent(indent)
            if above:
                imgui.table_next_row(imgui.TableRowFlags_.none, row_height)
            imgui.table_set_column_index(value_column)
            # Above each other, the field is indented like its label.
            value_indent = indent if above else 0.0
            if value_indent > 0:
                imgui.indent(value_indent)
            if _shows_as_text(param):
                imgui.align_text_to_frame_padding()
                imgui.text(_value_display(param, param.value))
                _render_suffix(param.suffix)
                _track_help(param.help_context)
                if value_indent > 0:
                    imgui.unindent(value_indent)
                continue
            widget_id = f"{device.node_id}_{param.ref_id}"
            read_only = param.access == Access.READ
            imgui.begin_group()
            imgui.set_next_item_width(-1 - _suffix_width(param.suffix))
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
            _render_suffix(param.suffix)
            imgui.end_group()
            if value_indent > 0:
                imgui.unindent(value_indent)
            # Right-click a changed value or its label to restore the application default.
            if changed and not read_only:
                _reset_menu(
                    widget_id,
                    label_hovered or imgui.is_item_hovered(),
                    lambda d=device, p=param: on_change(d, p.ref_id, p.default_value),
                )
            if req is not None:
                popup_request = EnumPopupRequest(device=device, param=req.param)
        imgui.end_table()
    return popup_request


# An information box as in Windows tools: a blue border, the page tinted with that blue, and a
# lighter blue round icon.
_INFO_BORDER = imgui.ImVec4(62 / 255, 120 / 255, 179 / 255, 1.0)
_INFO_ICON = imgui.ImVec4(55 / 255, 148 / 255, 194 / 255, 1.0)
_INFO_TINT = 0.11
# An error box the same way in red, with a cross in its icon.
_ERROR_BORDER = imgui.ImVec4(200 / 255, 50 / 255, 50 / 255, 1.0)
_ERROR_ICON = imgui.ImVec4(210 / 255, 52 / 255, 52 / 255, 1.0)
_ERROR_TINT = 0.15
# Width at 100 % scaling a grid column without a declared width keeps when the grid is too wide.
_MIN_STRETCH_COLUMN = 80.0
# Page width at 100 % scaling that percentage column widths of a grid refer to.
_GRID_REFERENCE_WIDTH = 540.0
# Heights of grid labels as last drawn, per row and per cell (see _render_grid_cells).
_label_heights: dict[str, float] = {}
# Narrowest a grid column may get (a declared width of 1 % is about 5 px).
_MIN_GRID_COLUMN = 1.0
# Indent of a parameter label per IndentLevel.
_INDENT_STEP = 8.0
# Share of that width the labels of a parameter list take.
_LIST_LABEL_SHARE = 0.45
# Gap at 100 % scaling a field in a grid cell leaves to the next column.
_CELL_GAP = 6.0
# Space left and right of a button in a grid cell.
_BUTTON_MARGIN = 7.0
# Size of a headline over the text: the semibold 10.5 pt over 9 pt of Windows dialogs, as wide as
# that with the emboldened font.
_HEADLINE_SCALE = 1.1
# Room at 100 % scaling beyond the usual spacing above and below a separator line, below a
# headline and above and below an information or error box.
_RULER_ABOVE = 14.0
_RULER_BELOW = 7.0
_HEADLINE_BELOW = 6.0
_HINT_ABOVE = 9.0
_HINT_BELOW = 5.0


def _wrap_width() -> float:
    """Width up to the visible right edge of the window or the current table column, at most up
    to where the fields of the page end."""
    style = imgui.get_style()
    right = imgui.get_scroll_x() + imgui.get_window_width() - style.window_padding.x
    if imgui.get_scroll_max_y() > 0:
        right -= style.scrollbar_size
    x = imgui.get_cursor_pos_x()
    width = min(right - x, imgui.get_content_region_avail().x, _page_width())
    return max(width, px(80))


def _page_width() -> float:
    """Width of the page's rows of labels and fields, where text and separators end, too."""
    return min(imgui.get_content_region_avail().x, px(_GRID_REFERENCE_WIDTH))


def _render_ruler(text: str | None) -> None:
    """A horizontal line as wide as the page's rows, with ``text`` in it as a title; drawn like
    a separator, which would reach the window's edge."""
    style = imgui.get_style()
    width = _page_width()
    top = imgui.get_cursor_screen_pos()
    draw_list = imgui.get_window_draw_list()
    color = imgui.get_color_u32(imgui.Col_.separator)
    if not text:
        draw_list.add_rect_filled(top, imgui.ImVec2(top.x + width, top.y + 1.0), color)
        imgui.dummy(imgui.ImVec2(width, 1.0))
        return
    pad = style.separator_text_padding
    thickness = style.separator_text_border_size
    size = imgui.calc_text_size(text)
    height = size.y + pad.y * 2
    middle = top.y + height / 2
    text_x = top.x + pad.x
    gap = style.item_spacing.x

    def line(x0: float, x1: float) -> None:
        if x1 > x0:
            draw_list.add_rect_filled(
                imgui.ImVec2(x0, middle - thickness / 2),
                imgui.ImVec2(x1, middle + thickness / 2),
                color,
            )

    line(top.x, text_x - gap)
    line(text_x + size.x + gap, top.x + width)
    draw_list.add_text(
        imgui.ImVec2(text_x, top.y + pad.y), imgui.get_color_u32(imgui.Col_.text), text
    )
    imgui.dummy(imgui.ImVec2(width, height))


def _aligned_text(text: str, alignment: str | None) -> None:
    avail = _wrap_width()
    width = imgui.calc_text_size(text).x
    if alignment in ("Center", "Right") and "\n" not in text and width < avail:
        offset = (avail - width) / 2 if alignment == "Center" else avail - width
        imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + offset)
        avail -= offset
    wrapped_text(text, avail)


def _render_hint(
    text: str,
    border: imgui.ImVec4,
    fill: imgui.ImVec4,
    color: imgui.ImVec4,
    glyph: str,
) -> None:
    """An information or error text in a box of ``fill`` with a ``border``, and a round icon of
    ``color`` showing ``glyph``, or a cross without one."""
    pad = px(8)
    icon = imgui.get_font_size() * 1.25
    width = _wrap_width()
    text_width = width - pad * 3 - icon
    lines_height = text_height(len(wrap_lines(text, text_width)))
    top = imgui.get_cursor_screen_pos()
    box_max = imgui.ImVec2(top.x + width, top.y + max(lines_height, icon) + pad * 2)
    draw = imgui.get_window_draw_list()
    # Corners as round as those of fields.
    rounding = imgui.get_style().frame_rounding
    draw.add_rect_filled(top, box_max, imgui.get_color_u32(fill), rounding)
    draw.add_rect(top, box_max, imgui.get_color_u32(border), rounding)
    centre = imgui.ImVec2(top.x + pad + icon / 2, top.y + pad + icon / 2)
    draw.add_circle_filled(centre, icon / 2, imgui.get_color_u32(color))
    white = imgui.get_color_u32(imgui.ImVec4(1, 1, 1, 1))
    if glyph:
        size = imgui.calc_text_size(glyph)
        draw.add_text(
            imgui.ImVec2(centre.x - size.x / 2, centre.y - size.y / 2), white, glyph
        )
    else:
        arm = icon * 0.2
        for sign in (1.0, -1.0):
            draw.add_line(
                imgui.ImVec2(centre.x - arm, centre.y - arm * sign),
                imgui.ImVec2(centre.x + arm, centre.y + arm * sign),
                white,
                px(1.6),
            )
    imgui.set_cursor_screen_pos(imgui.ImVec2(top.x + pad * 2 + icon, top.y + pad))
    wrapped_text(text, text_width)
    imgui.set_cursor_screen_pos(imgui.ImVec2(top.x, box_max.y))
    imgui.dummy(imgui.ImVec2(width, 0))


def _mix(a: imgui.ImVec4, b: imgui.ImVec4, t: float) -> imgui.ImVec4:
    return imgui.ImVec4(
        a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, 1.0
    )


def _render_separator(sep: UiSeparator) -> None:
    if sep.hint == "HorizontalRuler":
        vertical_space(px(_RULER_ABOVE))
        _render_ruler(sep.text)
        vertical_space(px(_RULER_BELOW))
    elif not sep.text:
        imgui.spacing()
    elif sep.hint == "Headline":
        with bold(_HEADLINE_SCALE):
            imgui.align_text_to_frame_padding()
            _aligned_text(sep.text, sep.alignment)
        vertical_space(px(_HEADLINE_BELOW))
    elif sep.hint == "Information":
        vertical_space(px(_HINT_ABOVE))
        fill = _mix(content_background(), _INFO_BORDER, _INFO_TINT)
        _render_hint(sep.text, _INFO_BORDER, fill, _INFO_ICON, "i")
        vertical_space(px(_HINT_BELOW))
    elif sep.hint == "Error":
        vertical_space(px(_HINT_ABOVE))
        fill = _mix(content_background(), _ERROR_BORDER, _ERROR_TINT)
        _render_hint(sep.text, _ERROR_BORDER, fill, _ERROR_ICON, "")
        vertical_space(px(_HINT_BELOW))
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
