"""Small text helpers shared across panels."""

from __future__ import annotations

from imgui_bundle import imgui

# How much of the item spacing above it a control's hint leaves out: it sits close to its
# control, so it reads as its part and not as the next one's.
_HINT_PULL = 0.75


def control_hint(text: str) -> None:
    """The grey explanation of the control just above it, wrapped at the right edge and closer to
    that control than the usual spacing. Leave more room after it than before the next control
    (:func:`after_control_hint`), so the hint is clearly the one above's."""
    style = imgui.get_style()
    imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - style.item_spacing.y * _HINT_PULL)
    imgui.push_text_wrap_pos(0.0)
    imgui.text_disabled(text)
    imgui.pop_text_wrap_pos()


def after_control_hint() -> None:
    """The room after a control with its :func:`control_hint`, before the next control."""
    imgui.dummy(imgui.ImVec2(0.0, imgui.get_style().item_spacing.y))


def text_clipped_tooltip(
    text: str, *, disabled: bool = False, width: float | None = None
) -> None:
    """Render ``text`` and, when it is clipped by the current column/region, show the full value
    as a tooltip on hover. Use in table cells whose content can overflow (decoded values, names).
    ``width`` clips it to fewer pixels than the region has, e.g. to leave room for an icon."""
    start = imgui.get_cursor_screen_pos()
    avail = imgui.get_content_region_avail().x if width is None else width
    if width is not None:
        bottom = start.y + imgui.get_text_line_height_with_spacing()
        imgui.push_clip_rect(start, imgui.ImVec2(start.x + width, bottom), True)
    if disabled:
        imgui.text_disabled(text)
    else:
        imgui.text(text)
    if width is not None:
        imgui.pop_clip_rect()
    hovered = imgui.is_item_hovered() and (
        width is None or imgui.get_mouse_pos().x < start.x + width
    )
    if text and hovered and imgui.calc_text_size(text).x > avail:
        imgui.set_tooltip(text)


def human_size(num_bytes: int) -> str:
    """A size in the unit that suits it, bytes whole and larger units with one decimal in the
    regional format, its unit as the UI language writes it ("1.2 MB", "1,2 MB", "1,2 МБ")."""
    from editor_gui.regional import regional_format
    from editor_gui.widgets.strings import S

    units = (S.SIZE_BYTES, S.SIZE_KB, S.SIZE_MB, S.SIZE_GB)
    size = float(num_bytes)
    step = 0
    while size >= 1024.0 and step < len(units) - 1:
        size /= 1024.0
        step += 1
    if step == 0:
        return units[0].format(size=f"{num_bytes}")
    return units[step].format(
        size=f"{size:.1f}".replace(".", regional_format().decimal)
    )


def fit_text(text: str, room: float) -> str:
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
