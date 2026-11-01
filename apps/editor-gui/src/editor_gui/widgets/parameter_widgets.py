from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, replace

from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.device import Device
from editor_gui.regional import regional_format
from editor_gui.widgets import text_layout
from editor_gui.widgets.backgrounds import (
    box_line,
    child_background,
    content_background,
    group_background,
    page_button,
    page_line,
    side_background,
)
from editor_gui.widgets.checkbox import checkbox, radio_button
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.dpi import px
from editor_gui.widgets.float_format import format_float, general_float, parse_float
from editor_gui.widgets.fonts import bold, bold_font, semilight
from editor_gui.widgets.icons import Icon, Picture, draw_icon
from editor_gui.widgets.strings import S
from editor_gui.widgets.tables import data_table_id
from editor_gui.widgets.time_format import (
    format_time,
    parse_time,
    time_pattern,
    unit_ms,
)
from editor_gui.widgets.tree import context_menu
from editor_gui.widgets.value_pickers import (
    format_date,
    render_color_param,
    render_date_param,
)
from editor_gui.widgets.wrapping import (
    LINE_GAP,
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
    EnumChoice,
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
    """A Button, 24 px high; its icon is drawn in front of the text. In a grid cell (``fill``) it
    takes the width of the cell, less a margin on each side, and is centred on where a field of
    the row is, reaching into the row's spacing: the row is as high as one with a field."""
    enabled, tooltip = (
        buttons.state(device, button) if buttons is not None else (False, None)
    )
    enabled = enabled and not button.read_only
    icon = _get_icon(button.icon) if _get_icon is not None and button.icon else None
    width = 0.0
    window = internal.get_current_window()
    field_bottom = imgui.get_cursor_screen_pos().y + imgui.get_frame_height()
    content_bottom = window.dc.cursor_max_pos.y
    if fill:
        cell = (
            imgui.get_content_region_avail().x
            - px(_BUTTON_MARGIN_LEFT)
            - px(_BUTTON_MARGIN_RIGHT)
        )
        if cell >= imgui.get_frame_height():
            width = cell
            imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + px(_BUTTON_MARGIN_LEFT))
        imgui.set_cursor_pos_y(
            imgui.get_cursor_pos_y()
            - (px(_BUTTON_HEIGHT) - imgui.get_frame_height()) / 2
        )
    size = imgui.ImVec2(width, px(_BUTTON_HEIGHT))
    # A light face with a thin border, as the page's fields are light.
    imgui.push_style_color(imgui.Col_.button, page_button())
    imgui.push_style_color(imgui.Col_.border, page_line())
    imgui.push_style_var(imgui.StyleVar_.frame_border_size, 1.0)
    imgui.begin_disabled(not enabled)
    if icon is None:
        clicked = imgui.button(f"{button.text}###btn_{prefix}_{button.id}", size)
    else:
        clicked = _icon_button(icon, button.text, f"###btn_{prefix}_{button.id}", size)
    imgui.end_disabled()
    imgui.pop_style_var()
    imgui.pop_style_color(2)
    if fill:
        window.dc.cursor_max_pos = imgui.ImVec2(
            window.dc.cursor_max_pos.x, max(content_bottom, field_bottom)
        )
    if tooltip and imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
        imgui.set_tooltip(tooltip)
    if clicked and enabled and buttons is not None:
        buttons.on_click(device, button)
    error = buttons.error(device, button) if buttons is not None else None
    if error:
        imgui.push_style_color(imgui.Col_.text, _ERROR_COLOR)
        imgui.text_wrapped(error)
        imgui.pop_style_color()


def _icon_button(icon: Icon, text: str, button_id: str, size: imgui.ImVec2) -> bool:
    """A button showing a 16 px ``icon`` before ``text``; a ``size.x`` of 0 sizes it to its
    content."""
    style = imgui.get_style()
    side = px(_BUTTON_ICON)
    gap = style.item_inner_spacing.x if text else 0.0
    content = side + gap + (imgui.calc_text_size(text).x if text else 0.0)
    clicked = imgui.button(
        button_id,
        imgui.ImVec2(size.x or content + style.frame_padding.x * 2, size.y),
    )
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    x = low.x + (high.x - low.x - content) / 2
    y = low.y + (high.y - low.y - side) / 2
    draw_icon(icon, imgui.ImVec2(x, y), side)
    if text:
        line = imgui.get_text_line_height()
        imgui.get_window_draw_list().add_text(
            imgui.ImVec2(x + side + gap, low.y + (high.y - low.y - line) / 2),
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
                return choice_text(choice)
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


# A parameter row as the reference tool lays it out (at 100 % scaling): its content is its value's
# control or the lines of its label (16 px each) when they take more, with 9 px around it. The
# controls are a line of text (16 px), radio buttons (24 px), a float without steps (a plain text
# box, 18 px) or another field (22 px). Label and control are centred in the content.
_ROW_MARGIN = 9.0
_LINE_CONTENT = 16.0
_FIELD_CONTENT = 22.0
_RADIO_CONTENT = 24.0
_STEPLESS_CONTENT = 18.0
# Where the content starts below the row's top: the reference puts its 9 px as 3 px above the
# content and 6 px below, and draws its text 2 px lower in a line than the editor does; the
# grids' rows are placed so, too.
_CONTENT_TOP = 4.0
# How much shorter a stepless float's field is than other fields (its frame padding is less).
_STEPLESS_SHRINK = 2.0


@dataclass(frozen=True, slots=True)
class _RowLayout:
    """A parameter row's height, and the tops of its label and its value from the row's top."""

    height: float
    label_top: float
    value_top: float


def _stepless(param: UiParameter) -> bool:
    """Whether ``param`` is a float without steps, shown in a plain text box."""
    return isinstance(param.widget, FloatWidget) and not param.widget.increment


def _control(param: UiParameter, text_row: bool) -> tuple[float, float]:
    """The height of ``param``'s control in its row, and how far its text is below its top."""
    pad = imgui.get_style().frame_padding.y
    if text_row:
        return px(_LINE_CONTENT), 0.0
    if isinstance(param.widget, EnumWidget) and param.widget.radio:
        return px(_RADIO_CONTENT), pad
    if _stepless(param):
        return px(_STEPLESS_CONTENT), pad - px(_STEPLESS_SHRINK)
    return px(_FIELD_CONTENT), pad


def _row_layout(param: UiParameter, text_row: bool, label_width: float) -> _RowLayout:
    """The layout of ``param``'s row beside its label ``label_width`` wide. Label and control
    are centred in the row's content; a one-line label is level with the control's text."""
    if param.value != param.default_value and _highlight_changes:
        label_width -= imgui.calc_text_size("*").x + px(4)
    labels = max(len(wrap_lines(param.label, max(label_width, 1.0))), 1)
    lines = labels * px(_LINE_CONTENT)
    control, text_offset = _control(param, text_row)
    content = max(control, lines)
    top = px(_CONTENT_TOP)
    label_top = top + _round(max(0.0, (content - lines) / 2))
    # The control's text is level with where a one-line label beside it would be.
    value_top = (
        top
        + _round((content - control) / 2)
        + _round((control - px(_LINE_CONTENT)) / 2)
        - text_offset
    )
    return _RowLayout(content + px(_ROW_MARGIN), label_top, value_top)


def _round(value: float) -> float:
    """``value`` to whole pixels, halves up (as the reference lays out)."""
    return float(math.floor(value + 0.5))


def _text_row(param: UiParameter) -> bool:
    """Whether ``param``'s row shows only texts: its value as text, or no field at all
    (TypeNone). Such a row is as high as a line of text, not as a field."""
    return _shows_as_text(param) or param.widget is None


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


@contextmanager
def device_images(
    get_icon: Callable[[str], Icon | None] | None,
    get_picture: Callable[[str], Picture | None] | None = None,
) -> Generator[None]:
    """The parameters drawn inside show the device's icons and pictures (outside a parameter
    page, e.g. its channel tables)."""
    global _get_icon, _get_picture
    saved = _get_icon, _get_picture
    _get_icon, _get_picture = get_icon, get_picture
    try:
        yield
    finally:
        _get_icon, _get_picture = saved


def choice_text(choice: EnumChoice) -> str:
    """The text of an enumeration entry; one shown only as its icon (a colour) has the icon's
    name where no icon can be drawn."""
    return choice.label or choice.icon or str(choice.value)


def _choice_icon(choice: EnumChoice | None) -> Icon | None:
    if choice is None or not choice.icon or _get_icon is None:
        return None
    return _get_icon(choice.icon)


def _fills_row(choice: EnumChoice, icon: Icon | None) -> bool:
    """Whether an entry shows as its image across the whole row: one without a text whose
    picture repeats or stretches (a colour)."""
    return (
        icon is not None
        and not choice.label.strip()
        and choice.picture_alignment
        in (HorizontalAlignment.REPEAT, HorizontalAlignment.STRETCH)
    )


def _draw_choice(
    choice: EnumChoice,
    icon: Icon | None,
    draw: imgui.ImDrawList,
    low: imgui.ImVec2,
    high: imgui.ImVec2,
) -> None:
    """An enumeration entry in the box ``low``-``high``: its icon before its text, or its image
    across the box (see :func:`_fills_row`)."""
    style = imgui.get_style()
    size = imgui.get_text_line_height()
    top = low.y + (high.y - low.y - size) / 2
    draw.push_clip_rect(low, high, True)
    if icon is not None and _fills_row(choice, icon):
        draw.add_image(
            icon.texture,
            imgui.ImVec2(low.x, top),
            imgui.ImVec2(high.x, top + size),
        )
    else:
        x = low.x
        if icon is not None:
            draw_icon(icon, imgui.ImVec2(x, top), size)
            x += size + style.item_inner_spacing.x
        draw.add_text(
            imgui.ImVec2(x, top),
            imgui.get_color_u32(imgui.Col_.text),
            choice.label if icon is not None else choice_text(choice),
        )
    draw.pop_clip_rect()


def _render_enum_combo(
    widget_id: str,
    widget: EnumWidget,
    current: EnumChoice | None,
    preview: str,
    on_change: Callable[[str], None],
) -> None:
    """The drop-down of an enumeration; entries with an icon show it before their text, a
    colour across the row. ``current`` None: ``preview`` is shown (e.g. "<differs>")."""
    icon = _choice_icon(current)
    style = imgui.get_style()
    inset = px(_COMBO_TEXT_INSET)
    # The box the chosen entry is drawn in, left of the arrow.
    start = imgui.get_cursor_screen_pos()
    low = imgui.ImVec2(start.x + inset, start.y)
    high = imgui.ImVec2(
        start.x + imgui.calc_item_width() - imgui.get_frame_height(),
        start.y + imgui.get_frame_height(),
    )
    draw = imgui.get_window_draw_list()
    custom = current is not None and icon is not None
    shown = "" if custom else (choice_text(current) if current else preview)
    imgui.push_style_var(
        imgui.StyleVar_.frame_padding, imgui.ImVec2(inset, style.frame_padding.y)
    )
    opened = begin_combo(f"##{widget_id}", shown)
    imgui.pop_style_var()
    if opened:
        for i, choice in enumerate(widget.choices):
            selected = current is not None and choice.value == current.value
            choice_icon = _choice_icon(choice)
            if choice_icon is None:
                clicked = imgui.selectable(f"{choice_text(choice)}##{i}", selected)[0]
            else:
                clicked = imgui.selectable(
                    f"##{i}",
                    selected,
                    0,
                    imgui.ImVec2(0, imgui.get_text_line_height()),
                )[0]
                _draw_choice(
                    choice,
                    choice_icon,
                    imgui.get_window_draw_list(),
                    imgui.get_item_rect_min(),
                    imgui.get_item_rect_max(),
                )
            if clicked:
                on_change(str(choice.value))
            if selected:
                imgui.set_item_default_focus()
        imgui.end_combo()
    if custom and current is not None:
        _draw_choice(current, icon, draw, low, high)


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
            preview = choice_text(w.choices[current_idx]) if w.choices else param.value
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
                        f"{choice_text(choice)}##{widget_id}_{i}",
                        selected,
                        first=i == 0,
                    ):
                        on_change(str(choice.value))
            else:
                current = None if differs or not w.choices else w.choices[current_idx]
                _render_enum_combo(widget_id, w, current, preview, on_change)
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
        case None:
            pass  # TypeNone: its text only, e.g. an empty line between parameters
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


# A spin arrow at 100 % scaling: its width, its height, and how far its middle is from the right
# edge of its field.
_SPIN_ARROW_WIDTH = 5.0
_SPIN_ARROW_HEIGHT = 3.0
_SPIN_ARROW_RIGHT = 9.0


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
        mid_x = rect_max.x - px(_SPIN_ARROW_RIGHT)
        mid_y = top.y + size.y / 2
        half = px(_SPIN_ARROW_WIDTH) / 2
        tip = mid_y - px(_SPIN_ARROW_HEIGHT) / 2 * step
        base = mid_y + px(_SPIN_ARROW_HEIGHT) / 2 * step
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


def _raw_label(node: UiTab | UiParameterBlock) -> str:
    fallback = "Tab" if isinstance(node, UiTab) else ""
    return node.text or node.name or node.id or fallback


def _repeated_labels(nodes: Iterable[UiNode]) -> frozenset[str]:
    """The labels that several of the tabs and blocks among ``nodes`` share."""
    counts = Counter(
        _raw_label(node) for node in nodes if isinstance(node, UiTab | UiParameterBlock)
    )
    return frozenset(label for label, count in counts.items() if count > 1)


def _instance_label(node: UiTab | UiParameterBlock, repeated: frozenset[str]) -> str:
    """Disambiguate a repeated module instance's tab/block label with its instance index.

    Some apps (e.g. the MDT DALI gateway) give every repeated instance the same text — 16 group tabs
    all "G,", 64 ECG blocks all "ECG ," — because the instance-number placeholder isn't substituted
    into the title; only the id differs (``…_MI-1`` … ``_MI-64``). Append that index so they read
    "G, 1"…"G, 16" / "ECG, 1"…"ECG, 64" (matching the "G1"/"ECG1"… labelling). Only a label that
    siblings share (``repeated``) gets it: instances with texts of their own ("Channel B: Kitchen")
    keep them, as do labels that already carry the number.
    """
    label = _raw_label(node)
    match = re.search(r"_MI-(\d+)", node.id or "")
    if label in repeated and match and match.group(1) not in re.findall(r"\d+", label):
        return f"{label.rstrip(' ,;:')} {match.group(1)}".strip()
    return label


def com_object_channels(
    nodes: Iterable[UiNode],
) -> dict[str, tuple[str, str, int]]:
    """The channel of each group object in a device's parameter tree, by its ComObjectRef id:
    ``(channel id, label as the page tree shows it, position of the channel)``. A group object in
    no channel (or in a channel-independent block) has none; one the tree lists twice keeps the
    place it is listed first, as the device's group objects do."""
    first: dict[str, tuple[str, str, int] | None] = {}
    count = 0

    def walk(children: Iterable[UiNode], channel: tuple[str, str, int] | None) -> None:
        nonlocal count
        children = tuple(children)
        repeated = _repeated_labels(children)
        for node in children:
            if isinstance(node, UiComObject):
                first.setdefault(node.ref_id, channel)
            elif isinstance(node, UiTab) and not node.independent:
                count += 1
                label = _instance_label(node, repeated)
                walk(node.children, (node.id or label, label, count))
            elif isinstance(node, UiTab | UiParameterBlock):
                walk(node.children, channel)

    walk(nodes, None)
    return {ref: channel for ref, channel in first.items() if channel is not None}


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
    # A channel's page: the channel's id, as :func:`com_object_channels` names it.
    channel: str | None = None


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
    repeated = _repeated_labels(children)
    for node in children:
        if isinstance(node, UiTab):
            key = f"{prefix}_{node.id}"
            if node.independent:
                inner, sub = _split_pages(node.children, key, seen)
                content.extend(inner)
                pages.extend(sub)
                continue
            inner, sub = _split_pages(node.children, key, seen)
            selectable = _has_content(inner)
            # A channel that shows nothing and leads to no page is not a parameter page. Some
            # devices carry channels that only group communication objects (no parameters); those
            # belong to the Group Objects view, not the page tree.
            if selectable or _first_selectable(tuple(sub)) is not None:
                label = _instance_label(node, repeated)
                pages.append(
                    ParameterPage(
                        key=_unique_key(key, seen),
                        label=label,
                        icon=node.icon,
                        content=inner,
                        children=tuple(sub),
                        selectable=selectable,
                        channel=node.id or label,
                    )
                )
        elif _is_page(node):
            assert isinstance(node, UiParameterBlock)
            key = f"{prefix}_{node.id}"
            if node.layout == ParameterBlockLayout.LIST:
                inner, sub = _split_pages(node.children, key, seen)
            else:
                inner, sub = node.children, []
            # A parameter block that shows nothing and leads to no sub-page is not a parameter
            # page. Some devices carry empty placeholder blocks (ABB AE/S4 "Blank 1"/"Blank 2":
            # the only parameters they reference are Dummy-type with Access="None", so the block
            # emits with no children). ETS does not list them; dropping them here matches.
            if not _has_content(inner) and _first_selectable(tuple(sub)) is None:
                continue
            pages.append(
                ParameterPage(
                    key=_unique_key(key, seen),
                    label=_instance_label(node, repeated),
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
    channel_menu: Callable[[str], None] | None = None,
) -> EnumPopupRequest | None:
    """The page tree beside the selected page, with filter and multi-device diff markers, filling
    the rest of the window.

    ``get_icon`` loads a page icon by name and ``get_picture`` a picture by baggage id.

    ``footer`` is drawn below the page, about ``footer_height`` high, and is passed the
    exact height left for it.

    ``channel_menu`` draws the items of a channel page's context menu, given the channel's id.
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
                channel_menu=channel_menu,
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
            imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + px(_PAGE_TOP))
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


# Room at 100 % scaling above a page's content besides the window padding (14 px in all).
_PAGE_TOP = 4.0
# Size at 100 % scaling of a page's icon in the page tree (16 px, as in the reference).
_PAGE_ICON = 16.0
# Room above and below a page's text in the page tree beyond a field's, at 100 % scaling: rows
# 30 px apart.
_PAGE_ROW_EXTRA = 1.0


def _render_page_tree(
    pages: tuple[ParameterPage, ...],
    selected: str | None,
    opened: frozenset[str],
    open_all: bool,
    get_icon: Callable[[str], Icon | None] | None,
    *,
    top_level: bool = True,
    channel_menu: Callable[[str], None] | None = None,
) -> str | None:
    """Draw ``pages`` as tree nodes; returns the key of a page clicked this frame. A top-level
    page with pages below is a group, boxed with them. ``channel_menu`` draws the items of a
    channel page's context menu."""
    clicked: str | None = None
    icon_size = px(_PAGE_ICON)
    if top_level:
        _hovered_page[1] = None
    for page in pages:
        # A row spans the tree's width, so the selected one joins the page beside it, and is a
        # little higher than a field.
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
        is_selected = page.key == selected
        # The selected page and the one under the mouse (in the frame before) are bold.
        emphasis = is_selected or page.key == _hovered_page[0]
        if emphasis:
            imgui.push_font(bold_font(), imgui.get_style().font_size_base)
        space = imgui.calc_text_size(" ").x
        icon = get_icon(page.icon) if get_icon is not None and page.icon else None
        pad = " " * math.ceil((icon_size + space) / space) if icon is not None else ""
        # Spaces in front of a page's text indent it, as the product data lays out its pages.
        if page.block is not None:
            pad += " " * page.block.text_indent
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
        frame = imgui.get_style().frame_padding
        imgui.push_style_var(
            imgui.StyleVar_.frame_padding,
            imgui.ImVec2(frame.x, frame.y + px(_PAGE_ROW_EXTRA)),
        )
        is_open = imgui.tree_node_ex(f"{pad}{page.label}###{page.key}", flags)
        imgui.pop_style_var()
        if emphasis:
            imgui.pop_font()
        if imgui.is_item_hovered():
            _hovered_page[1] = page.key
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
        if (
            page.channel is not None
            and channel_menu is not None
            and context_menu(f"##page_ctx_{page.key}")
        ):
            channel_menu(page.channel)
            imgui.end_popup()
        if page.block is not None:
            _track_help(page.block.help_context)
        if is_open and page.children:
            # The pages of a group (a channel) are not indented below it; only the levels
            # below them are.
            step = imgui.get_style().indent_spacing if is_group else 0.0
            if step:
                imgui.unindent(step)
            below = _render_page_tree(
                page.children,
                selected,
                opened,
                open_all,
                get_icon,
                top_level=False,
                channel_menu=channel_menu,
            )
            if step:
                imgui.indent(step)
            clicked = clicked or below
            imgui.tree_pop()
            if is_group:
                # The line closing the group's box below its last page.
                _draw_box_line(_row_band()[0])
    if top_level:
        _hovered_page[0] = _hovered_page[1]
    return clicked


# The page under the mouse in the frame before, and the one found in this frame so far.
_hovered_page: list[str | None] = [None, None]


def _row_band() -> tuple[float, float]:
    """Top and bottom on screen of the tree row drawn next, with half the spacing to the rows
    above and below it, so the bands of neighbouring rows touch."""
    half = imgui.get_style().item_spacing.y / 2
    top = imgui.get_cursor_screen_pos().y
    return top - half, top + _page_row_height() + half


def _page_row_height() -> float:
    """Height of a row of the page tree, without the spacing to the next one."""
    return imgui.get_frame_height() + 2 * px(_PAGE_ROW_EXTRA)


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
    _drawn_last[0] = _TOP
    if block is not None and block.layout != ParameterBlockLayout.LIST:
        _closer_to_last(grid=True)
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
    return req


# What a page drew last, for the room the next item keeps to it: nothing yet (its top), a
# separator line, a grid of nothing but lines, or anything else ("").
_TOP = "top"
_LINE = "line"
_LINES = "lines"
_drawn_last = [""]
# Room at 100 % scaling a grid keeps less than a separator does to the top of a page and to a
# grid of lines above it, and a grid or an information or error box to a separator line above it.
_GRID_AFTER_TOP = 5.0
_GRID_AFTER_LINES = 5.0
_AFTER_LINE = 6.0


def _closer_to_last(*, grid: bool) -> None:
    """Move the next item up to where the reference puts a grid (``grid``), or an information
    or error box, after what the page drew last (headlines, texts and fields keep their room)."""
    last = _drawn_last[0]
    if last == _LINE:
        distance = _AFTER_LINE
    elif grid and last == _TOP:
        distance = _GRID_AFTER_TOP
    elif grid and last == _LINES:
        distance = _GRID_AFTER_LINES
    else:
        return
    imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - px(distance))


def _only_lines(block: UiParameterBlock) -> bool:
    """Whether a grid shows nothing but separator lines."""
    return bool(block.children) and all(
        isinstance(node, UiSeparator) and not node.text for node in block.children
    )


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
    repeated = _repeated_labels(children)
    # Whether the last thing drawn was a table (a grid or a list of parameters): the next one
    # then joins it row against row.
    after_grid = False
    # Whether it was a grid: its last row already holds the spacing below its fields, so a
    # separator after it does without the usual spacing too.
    after_cells = False

    def join_rows() -> None:
        if after_grid:
            imgui.set_cursor_pos_y(
                imgui.get_cursor_pos_y() - imgui.get_style().item_spacing.y
            )

    def flush() -> None:
        nonlocal popup_request, table_idx, after_grid, after_cells
        if not pending_params:
            return
        join_rows()
        after_grid = True
        after_cells = False
        req = _render_param_table(
            device,
            pending_params,
            on_change,
            deferred_enum,
            f"{prefix}_{table_idx}",
            differing_refs,
            buttons,
        )
        _drawn_last[0] = ""
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
            _drawn_last[0] = ""
            after_grid = after_cells = False
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
                _closer_to_last(grid=True)
                # Nothing in its cells is placed after what came before the grid.
                _drawn_last[0] = ""
            req = _render_block(
                device,
                node,
                on_change,
                deferred_enum,
                prefix,
                needle,
                differing_refs,
                buttons,
                repeated,
            )
            if is_grid:
                _drawn_last[0] = _LINES if _only_lines(node) else ""
            elif not node.inline:
                _drawn_last[0] = ""
            after_grid = after_cells = is_grid
            if req is not None:
                popup_request = req
        elif isinstance(node, UiSeparator):
            if needle:
                continue  # separators are noise while filtering
            flush()
            if after_cells:
                join_rows()
            if node.text and node.hint in ("Information", "Error"):
                _closer_to_last(grid=False)
            _render_separator(node)
            _drawn_last[0] = "" if node.text else _LINE
            after_grid = after_cells = False
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
    repeated: frozenset[str] = frozenset(),
) -> EnumPopupRequest | None:
    """``repeated``: the labels the block's siblings share (see :func:`_instance_label`)."""
    if block.hidden:
        return None
    block_prefix = f"{prefix}_{block.id}"

    if block.layout in (ParameterBlockLayout.GRID, ParameterBlockLayout.TABLE):
        return _render_grid_block(
            device,
            block,
            on_change,
            deferred_enum,
            block_prefix,
            differing_refs,
            buttons,
        )

    if block.inline:
        return _render_children(
            device,
            block.children,
            on_change,
            deferred_enum,
            block_prefix,
            needle,
            differing_refs,
            buttons,
        )

    label = _instance_label(block, repeated)
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


def _whole_pixel_starts(widths: list[float | None]) -> list[float | None]:
    """Widths in whole pixels that start each column at its declared start, rounded: imgui cuts
    each width down to whole pixels, which adds up over the columns. A column without a width
    (it stretches) starts the count again."""
    whole: list[float | None] = []
    end = 0.0
    for width in widths:
        if width is None:
            whole.append(None)
            end = 0.0
            continue
        start = round(end)
        end += width
        whole.append(float(round(end) - start))
    return whole


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
            _repeated_labels(uncelled_blocks),
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
    widths = _whole_pixel_starts(_keep_column_starts(widths, _MIN_GRID_COLUMN))
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
    cell_padding_y = padding.y / 4
    imgui.push_style_var(
        imgui.StyleVar_.cell_padding,
        imgui.ImVec2(padding.x if is_table else 0, cell_padding_y),
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
        # A row of labels only is as high as one with fields, as the grid rows of the page are
        # (a cell with a field counts the spacing below it).
        spacing = imgui.get_style().item_spacing.y
        imgui.table_next_row(0, row_height + spacing + 2 * cell_padding_y)
        if layout_padding:
            imgui.pop_style_var()
            layout_padding = False
        if has_row_labels:
            imgui.table_set_column_index(0)
            label = block.row_labels[row - 1] if row - 1 < len(block.row_labels) else ""
            imgui.text_disabled(label)
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
                drawn, room = _render_cell_separator(sep, origin, row_height, own)
                _label_heights[cell_key] = drawn
                drawn_height = max(drawn_height, room)
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
    wrap = max(width - px(_CELL_GAP), 1.0) if width else math.inf
    if sep.hint == "Headline":
        with bold(_HEADLINE_SCALE):
            return _cell_text_height(len(wrap_lines(text, wrap)))
    with semilight():
        return _cell_text_height(len(wrap_lines(text, wrap)))


def _cell_text_height(lines: int) -> float:
    """Room a grid cell's text of ``lines`` lines in the current font takes in its row: that many
    lines of the reference's line spacing (Segoe UI's, 1.33 em), less the gap below the last
    one, which the row's spacing holds. In whole pixels, as the reference lays out."""
    return round(lines * text_layout.font_size() * _CELL_LINE - px(LINE_GAP))


def _render_cell_separator(
    sep: UiSeparator, origin: float, row_height: float, height: float
) -> tuple[float, float]:
    """A separator in a grid cell: a headline in bold, a label as plain text, a ruler across.
    Text wraps where the fields of the cell end (the cell's right edge less the gap to the next
    column); a label ``height`` high is centred in a row ``row_height`` high. Returns the height
    of the text as drawn, and the room it takes in its row (see :func:`_cell_text_height`)."""
    if sep.text and sep.hint in ("Headline", None):
        # On a whole pixel, as the reference lays out.
        imgui.set_cursor_pos_y(
            imgui.get_cursor_pos_y() + max(0.0, math.floor((row_height - height) / 2))
        )
        right = (
            imgui.get_cursor_pos_x()
            + imgui.get_content_region_avail().x
            - px(_CELL_GAP)
        )
        text = _indented_cell_text(sep.text, origin)
        # Broken into lines as other texts are.
        wrap = max(right - imgui.get_cursor_pos_x(), 1.0)
        with bold(_HEADLINE_SCALE) if sep.hint == "Headline" else semilight():
            wrapped_text(text, wrap)
            drawn = imgui.get_item_rect_size().y
            return drawn, _cell_text_height(len(wrap_lines(text, wrap)))
    if not sep.text:
        # A line ends where the fields of its cell end, so the lines of neighbouring cells keep
        # that gap between them.
        vertical_space(px(_RULER_ABOVE))
        _render_ruler(max(imgui.get_content_region_avail().x - px(_CELL_GAP), 1.0))
        vertical_space(px(_RULER_BELOW))
        return 0.0, 0.0
    _render_separator(sep)
    return 0.0, 0.0


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
    # In a cell a suffix keeps its gap after it too.
    suffix = _suffix_width(param.suffix) + (px(_SUFFIX_GAP) if param.suffix else 0.0)
    imgui.set_next_item_width(-px(_CELL_GAP) - suffix)
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
    # A row as wide as a separator's row; the labels take their share of it, the fields the
    # rest up to where the separators end.
    reference = min(imgui.get_content_region_avail().x, px(_LIST_ROW_WIDTH))
    # On a whole pixel, as the reference lays out (imgui would cut it down).
    label_width = round(reference * _LIST_LABEL_SHARE)
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
        # Above each other (a phone), a row is as high as one with an input field.
        row_height = imgui.get_frame_height() + 2 * padding.y
        for param in params:
            text_row = not isinstance(param, UiButton) and _text_row(param)
            indent = (
                0.0
                if isinstance(param, UiButton)
                else param.indent_level * px(_INDENT_STEP)
            )
            # Side by side, a row is laid out as the reference tool lays it out (see _row_layout).
            layout = (
                None
                if above or isinstance(param, UiButton)
                else _row_layout(param, text_row, label_width - indent)
            )
            imgui.table_next_row(
                imgui.TableRowFlags_.none,
                layout.height if layout is not None else 0.0,
            )
            if isinstance(param, UiButton):
                imgui.table_set_column_index(value_column)
                _render_button(device, param, prefix, buttons)
                continue
            imgui.table_set_column_index(0)
            # The row's top, above the cell padding the table puts in front of a cell's content.
            row_top = imgui.get_cursor_pos_y() - padding.y
            # Each level indents by one step, the levels space-indented grid labels are placed on
            # (see _indented_cell_text).
            if indent > 0:
                imgui.indent(indent)
            label = param.label
            differs = param.ref_id in differing_refs
            if layout is not None:
                imgui.set_cursor_pos_y(row_top + layout.label_top)
            elif not text_row:
                imgui.align_text_to_frame_padding()
            # In multi-edit a diverging value has no single "changed vs default" state to show.
            changed = not differs and param.value != param.default_value
            # Changed parameters stand out. A leading marker plus the colour means the
            # state is not signalled by colour alone.
            if changed and _highlight_changes:
                imgui.text_colored(_CHANGED_COLOR, "*")
                imgui.same_line(0, px(4))
                imgui.push_style_color(imgui.Col_.text, _CHANGED_COLOR)
                _wrapped_label(label)
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
                _wrapped_label(label)
                imgui.pop_style_color()
            else:
                _wrapped_label(label)
            label_hovered = imgui.is_item_hovered()
            _track_help(param.help_context)
            if indent > 0:
                imgui.unindent(indent)
            if above:
                imgui.table_next_row(
                    imgui.TableRowFlags_.none, 0.0 if text_row else row_height
                )
            imgui.table_set_column_index(value_column)
            if layout is not None:
                imgui.set_cursor_pos_y(row_top + layout.value_top)
            # Above each other, the field is indented like its label.
            value_indent = indent if above else 0.0
            if value_indent > 0:
                imgui.indent(value_indent)
            if _shows_as_text(param):
                imgui.text(_value_display(param, param.value))
                _render_suffix(param.suffix)
                _track_help(param.help_context)
                if value_indent > 0:
                    imgui.unindent(value_indent)
                continue
            widget_id = f"{device.node_id}_{param.ref_id}"
            read_only = param.access == Access.READ
            imgui.begin_group()
            imgui.set_next_item_width(-px(_LIST_ROW_END) - _suffix_width(param.suffix))
            imgui.begin_disabled(read_only)
            # A float without steps is a plain text box, shorter than other fields.
            compact = layout is not None and _stepless(param)
            if compact:
                frame = imgui.get_style().frame_padding
                imgui.push_style_var(
                    imgui.StyleVar_.frame_padding,
                    imgui.ImVec2(frame.x, frame.y - px(_STEPLESS_SHRINK)),
                )
            req = _render_device_param(
                device,
                param,
                widget_id,
                lambda v, d=device, p=param.ref_id: on_change(d, p, v),
                deferred_enum=deferred_enum,
                differs=differs,
            )
            if compact:
                imgui.pop_style_var()
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
# Page width at 100 % scaling that percentage column widths of a grid refer to: as wide as a
# separator (see _SEPARATOR_WIDTH).
_GRID_REFERENCE_WIDTH = 532.0
# Width at 100 % scaling of a separator's text, line or box: its row of 535 px less the 3 px it
# keeps from the row's left edge.
_SEPARATOR_WIDTH = 532.0
# An information or error box at 100 % scaling: its text starts 37 px from the box's left edge
# (border 1 px, margin 5 px, a 16 px icon with 5 px before and 10 px after it) and ends 6 px from
# its right edge (margin and border).
_HINT_ICON = 16.0
_HINT_ICON_LEFT = 11.0
_HINT_TEXT_LEFT = 37.0
_HINT_TEXT_RIGHT = 6.0
# Heights of grid labels as last drawn, per row and per cell (see _render_grid_cells).
_label_heights: dict[str, float] = {}
# Narrowest a grid column may get (a declared width of 1 % is about 5 px).
_MIN_GRID_COLUMN = 1.0
# Indent of a parameter label per IndentLevel.
_INDENT_STEP = 8.0
# Width at 100 % scaling of a row of a parameter list (that of a separator's row), the share of it
# the labels take, and the room its fields leave at its end (the separators end there too).
_LIST_ROW_WIDTH = 535.0
_LIST_LABEL_SHARE = 0.45
_LIST_ROW_END = _LIST_ROW_WIDTH - _SEPARATOR_WIDTH
# Gap at 100 % scaling a field in a grid cell leaves to the next column.
_CELL_GAP = 6.0
# Distance at 100 % scaling of the text of a drop-down from its left edge.
_COMBO_TEXT_INSET = 5.0
# Line spacing of the reference's texts (Segoe UI: ascent and descent 2724 / 2048 of the em) per
# ImGui font size (of which the em is 12/14).
_CELL_LINE = 2724 / 2048 * 12 / 14
# Space at 100 % scaling left and right of a button in a grid cell, its height (2 px more than a
# field's), and the size of its icon.
_BUTTON_MARGIN_LEFT = 4.0
_BUTTON_MARGIN_RIGHT = 10.0
_BUTTON_HEIGHT = 24.0
_BUTTON_ICON = 16.0
# Size of a headline over the text: 14 px semibold over the 12 px text.
_HEADLINE_SCALE = 14.0 / 12.0
# Room at 100 % scaling above and below a headline's text beyond the usual spacing: a headline
# is 29 px high, its baseline 20 px below its top (as the reference draws them, measured against
# its parameter rows).
_HEADLINE_ABOVE = 1.0
_HEADLINE_BELOW = 4.0
# Room at 100 % scaling beyond the usual spacing above and below a separator line, above an
# information or error box and below it (less than the usual spacing), and above and below a
# separator's text. The reference keeps 5 px above and below each separator, centres a line in a
# row of 21 px and draws a text in lines of 16 px; measured from baseline to baseline (or line)
# that is 26 px between two texts, 27 px from a headline to a text, 19 px from a text to a line,
# 30 px from a line to a headline, 14 px from a text to a box and 23 px from a box to a text.
_RULER_ABOVE = 10.0
_RULER_BELOW = 10.0
_HINT_ABOVE = 5.0
_HINT_BELOW = -1.0
_TEXT_ABOVE = 6.0
_TEXT_BELOW = 4.0
# The room in an information or error box above its text and below it (with its last line's gap,
# 6 px around the text as in the reference).
_HINT_PAD = 8.0
_HINT_PAD_BELOW = 6.0


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
    """Width of the page's separators: their texts, lines and boxes."""
    return min(imgui.get_content_region_avail().x, px(_SEPARATOR_WIDTH))


def _render_ruler(width: float | None = None) -> None:
    """A horizontal line as wide as the page's rows, or ``width``; drawn like a separator, which
    would reach the window's edge."""
    if width is None:
        width = _page_width()
    top = imgui.get_cursor_screen_pos()
    color = imgui.get_color_u32(page_line())
    imgui.get_window_draw_list().add_rect_filled(
        top, imgui.ImVec2(top.x + width, top.y + 1.0), color
    )
    imgui.dummy(imgui.ImVec2(width, 1.0))


def _wrapped_label(label: str) -> None:
    """A parameter's label in the rest of its column, broken into lines as separator texts are
    (at spaces and after hyphens)."""
    wrapped_text(label, max(imgui.get_content_region_avail().x, 1.0))


def _aligned_text(text: str, alignment: str | None) -> None:
    avail = _wrap_width()
    width = text_layout.text_width(text)
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
    pad = px(_HINT_PAD)
    icon = px(_HINT_ICON)
    width = _wrap_width()
    text_width = width - px(_HINT_TEXT_LEFT) - px(_HINT_TEXT_RIGHT)
    with semilight():
        lines_height = text_height(len(wrap_lines(text, text_width)))
    top = imgui.get_cursor_screen_pos()
    box_max = imgui.ImVec2(
        top.x + width, top.y + pad + max(lines_height, icon) + px(_HINT_PAD_BELOW)
    )
    draw = imgui.get_window_draw_list()
    # Corners as round as those of fields.
    rounding = imgui.get_style().frame_rounding
    draw.add_rect_filled(top, box_max, imgui.get_color_u32(fill), rounding)
    draw.add_rect(top, box_max, imgui.get_color_u32(border), rounding)
    centre = imgui.ImVec2(
        top.x + px(_HINT_ICON_LEFT) + icon / 2, top.y + pad + icon / 2
    )
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
    imgui.set_cursor_screen_pos(imgui.ImVec2(top.x + px(_HINT_TEXT_LEFT), top.y + pad))
    with semilight():
        wrapped_text(text, text_width)
    # The next item follows the usual spacing plus _HINT_BELOW (which may be less than none).
    imgui.set_cursor_screen_pos(imgui.ImVec2(top.x, box_max.y + px(_HINT_BELOW)))
    imgui.dummy(imgui.ImVec2(width, 0))


def _mix(a: imgui.ImVec4, b: imgui.ImVec4, t: float) -> imgui.ImVec4:
    return imgui.ImVec4(
        a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, 1.0
    )


def _render_separator(sep: UiSeparator) -> None:
    """A separator without a text is a line, whatever its hint; one with a text is a headline, an
    information or error box, or else (also a horizontal ruler with a text) the text alone."""
    if not sep.text:
        vertical_space(px(_RULER_ABOVE))
        _render_ruler()
        vertical_space(px(_RULER_BELOW))
    elif sep.hint == "Headline":
        with bold(_HEADLINE_SCALE):
            imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + px(_HEADLINE_ABOVE))
            _aligned_text(sep.text, sep.alignment)
        vertical_space(px(_HEADLINE_BELOW))
    elif sep.hint == "Information":
        vertical_space(px(_HINT_ABOVE))
        fill = _mix(content_background(), _INFO_BORDER, _INFO_TINT)
        _render_hint(sep.text, _INFO_BORDER, fill, _INFO_ICON, "i")
    elif sep.hint == "Error":
        vertical_space(px(_HINT_ABOVE))
        fill = _mix(content_background(), _ERROR_BORDER, _ERROR_TINT)
        _render_hint(sep.text, _ERROR_BORDER, fill, _ERROR_ICON, "")
    else:
        vertical_space(px(_TEXT_ABOVE))
        with semilight():
            _aligned_text(sep.text, sep.alignment)
        vertical_space(px(_TEXT_BELOW))


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
                for i, choice in enumerate(target.param.widget.choices):
                    selected = str(choice.value) == target.param.value
                    if imgui.menu_item(f"{choice_text(choice)}##{i}", "", selected)[0]:
                        self._on_change(
                            target.device, target.param.ref_id, str(choice.value)
                        )
            imgui.end_popup()
        else:
            self._active = None
