"""Renders the Markdown subset used by application help pages: headings, paragraphs, bullet and
numbered lists, laid out with the sizes and spacing help pages have in Windows tools."""

from __future__ import annotations

import re
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.widgets.dpi import px
from editor_gui.widgets.fonts import bold
from editor_gui.widgets.wrapping import LINE_GAP, vertical_space, wrapped_text

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)(.+?)\1")
# Size of a heading over the text, and whether it is bold, by its level (#, ##, ###, deeper):
# level 2 and 3 are about 16 px over text of 12 px, level 2 semibold.
_HEADINGS = ((1.6, True), (1.28, True), (1.28, False), (1.0, True))
# Room at 100 % scaling above and below a heading, and around other blocks (paragraphs, lists);
# of two blocks the larger of their rooms parts them.
_HEADING_ABOVE = 25.0
_HEADING_BELOW = 23.0
_BLOCK_ROOM = 12.0
# Where a list item's marker and its text start, at 100 % scaling, and the bullet's radius.
_MARKER_X = 18.0
_ITEM_X = 30.0
_BULLET_RADIUS = 2.2


@dataclass(frozen=True)
class _Block:
    kind: str  # "heading", "paragraph", "bullet" or "numbered"
    text: str
    level: int = 0  # a heading's level
    number: str = ""  # a numbered item's number


def plain(text: str) -> str:
    """Inline Markdown reduced to its visible text."""
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    return _EMPHASIS.sub(r"\2", text)


def _blocks(text: str) -> list[_Block]:
    blocks: list[_Block] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(_Block("paragraph", plain(" ".join(paragraph))))
            paragraph.clear()

    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush()
        elif m := _HEADING.match(line):
            flush()
            blocks.append(_Block("heading", plain(m.group(2)), level=len(m.group(1))))
        elif m := _BULLET.match(line):
            flush()
            blocks.append(_Block("bullet", plain(m.group(1))))
        elif m := _NUMBERED.match(line):
            flush()
            blocks.append(_Block("numbered", plain(m.group(2)), number=m.group(1)))
        else:
            paragraph.append(line.strip())
    flush()
    return blocks


def _room(block: _Block, above: bool) -> float:
    if block.kind == "heading":
        return _HEADING_ABOVE if above else _HEADING_BELOW
    return _BLOCK_ROOM


def _gap(previous: _Block, block: _Block) -> float:
    """Room at 100 % scaling between two blocks; the items of a list lie as close as the lines
    of a text."""
    if previous.kind == block.kind and block.kind in ("bullet", "numbered"):
        return LINE_GAP
    return max(_room(previous, above=False), _room(block, above=True))


def render_markdown(text: str) -> None:
    spacing = imgui.get_style().item_spacing
    # Between blocks only the room set here.
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(spacing.x, 0.0))
    previous: _Block | None = None
    for block in _blocks(text):
        if previous is not None:
            # The block above already left the line gap below its last line.
            vertical_space(px(_gap(previous, block) - LINE_GAP))
        if block.kind == "heading":
            _render_heading(block)
        elif block.kind == "paragraph":
            wrapped_text(block.text, imgui.get_content_region_avail().x)
        else:
            _render_item(block)
        previous = block
    imgui.pop_style_var()


def _render_heading(block: _Block) -> None:
    scale, is_bold = _HEADINGS[min(block.level, len(_HEADINGS)) - 1]
    width = imgui.get_content_region_avail().x
    if is_bold:
        with bold(scale):
            wrapped_text(block.text, width)
    else:
        imgui.push_font(None, imgui.get_style().font_size_base * scale)
        wrapped_text(block.text, width)
        imgui.pop_font()


def _render_item(block: _Block) -> None:
    """A list item: its marker, then its text, whose wrapped lines start below its first."""
    left = imgui.get_cursor_screen_pos()
    draw_list = imgui.get_window_draw_list()
    color = imgui.get_color_u32(imgui.Col_.text)
    if block.kind == "bullet":
        draw_list.add_circle_filled(
            imgui.ImVec2(
                left.x + px(_MARKER_X), left.y + imgui.get_text_line_height() / 2
            ),
            px(_BULLET_RADIUS),
            color,
        )
    else:
        draw_list.add_text(
            imgui.ImVec2(left.x + px(_MARKER_X) - px(_BULLET_RADIUS), left.y),
            color,
            f"{block.number}.",
        )
    imgui.set_cursor_screen_pos(imgui.ImVec2(left.x + px(_ITEM_X), left.y))
    wrapped_text(block.text, imgui.get_content_region_avail().x)
