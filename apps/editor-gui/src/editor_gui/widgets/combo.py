"""Drop-downs drawn like the ones of Windows dialogs: the whole field on one background a little
darker than an input field (lighter in a dark theme), with a small solid arrow instead of ImGui's
arrow button. The theme's frame rounding and border stay."""

from __future__ import annotations

from imgui_bundle import imgui

# How far the drop-down background moves from an input field's toward the text color: a white
# field gives the light gray (242) of a Windows drop-down next to dark gray (#333) text.
_BACKGROUND_SHADE = 0.064
# The arrow's width relative to the font size (8 px at the 14 px font); it is half as high.
_ARROW_WIDTH = 4.0 / 7.0


def _mix(a: imgui.ImVec4, b: imgui.ImVec4, t: float) -> imgui.ImVec4:
    return imgui.ImVec4(
        a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, a.w
    )


def combo_background(hovered: bool = False) -> imgui.ImVec4:
    """The background of a drop-down, from the theme's input field background."""
    style = imgui.get_style()
    field = style.color_(
        imgui.Col_.frame_bg_hovered if hovered else imgui.Col_.frame_bg
    )
    return _mix(field, style.color_(imgui.Col_.text), _BACKGROUND_SHADE)


def begin_combo(label: str, preview: str, flags: int = 0) -> bool:
    """``imgui.begin_combo`` drawn as a Windows drop-down; end it with ``imgui.end_combo``."""
    background = combo_background()
    hovered = combo_background(hovered=True)
    colors = (
        (imgui.Col_.frame_bg, background),
        (imgui.Col_.frame_bg_hovered, hovered),
    )
    arrow = not flags & (
        imgui.ComboFlags_.no_arrow_button | imgui.ComboFlags_.no_preview
    )
    if arrow:
        # ImGui draws no arrow button; the text keeps clear of the arrow drawn instead.
        if not flags & imgui.ComboFlags_.width_fit_preview:
            room = (
                imgui.calc_item_width()
                - imgui.get_frame_height()
                - imgui.get_style().frame_padding.x
            )
            preview = _fit(preview, room)
        flags |= imgui.ComboFlags_.no_arrow_button
    for col, color in colors:
        imgui.push_style_color(col, color)
    opened = imgui.begin_combo(label, preview, flags)
    imgui.pop_style_color(len(colors))
    if arrow:
        _draw_arrow()
    return opened


def combo(
    label: str, current: int, items: list[str], popup_max_height_in_items: int = -1
) -> tuple[bool, int]:
    """``imgui.combo`` drawn as a Windows drop-down: returns whether the choice changed and the
    index chosen."""
    if popup_max_height_in_items > 0:
        style = imgui.get_style()
        height = (
            imgui.get_font_size() + style.item_spacing.y
        ) * popup_max_height_in_items + style.window_padding.y * 2
        imgui.set_next_window_size_constraints(
            imgui.ImVec2(0.0, 0.0), imgui.ImVec2(imgui.FLT_MAX, height)
        )
    preview = items[current] if 0 <= current < len(items) else ""
    changed = False
    if begin_combo(label, preview):
        for index, item in enumerate(items):
            selected = index == current
            if imgui.selectable(f"{item}##{index}", selected)[0] and not selected:
                current = index
                changed = True
            if selected:
                imgui.set_item_default_focus()
        imgui.end_combo()
    return changed, current


def _fit(text: str, room: float) -> str:
    """``text`` shortened with an ellipsis to fit ``room`` pixels."""
    if room <= 0 or imgui.calc_text_size(text).x <= room:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if imgui.calc_text_size(text[:middle] + "…").x <= room:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "…"


def _draw_arrow() -> None:
    """A small solid arrow at the right end of the drop-down just drawn."""
    low = imgui.get_item_rect_min()
    high = imgui.get_item_rect_max()
    box = high.y - low.y
    draw = imgui.get_window_draw_list()
    width = round(imgui.get_font_size() * _ARROW_WIDTH)
    height = width / 2.0
    left = round(high.x - box / 2.0 - width / 2.0)
    top = round((low.y + high.y) / 2.0 - height / 2.0)
    draw.add_triangle_filled(
        imgui.ImVec2(left, top),
        imgui.ImVec2(left + width, top),
        imgui.ImVec2(left + width / 2.0, top + height),
        imgui.get_color_u32(imgui.Col_.text),
    )
