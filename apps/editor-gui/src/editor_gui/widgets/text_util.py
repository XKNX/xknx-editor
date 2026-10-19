"""Small text helpers shared across panels."""

from __future__ import annotations

from imgui_bundle import imgui


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
    """Human-readable byte size (e.g. '1.2 MB')."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"
