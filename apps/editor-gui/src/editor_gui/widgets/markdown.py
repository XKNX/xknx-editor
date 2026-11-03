"""Renders the Markdown subset used by application help pages: headings, paragraphs, bullet and
numbered lists, laid out with the sizes and spacing help pages have in Windows tools, or more
compact (the release notes of an update)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.widgets import text_layout
from editor_gui.widgets.dpi import px
from editor_gui.widgets.fonts import bold, semilight
from editor_gui.widgets.wrapping import LINE_GAP, vertical_space, wrapped_text

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)(.+?)\1")
# Size of a heading over the text of 12 px, and its face, by its level (#, ##, ###, deeper): 18 px
# semilight, 16 px semibold, 16 px regular, 12 px semibold.
_HEADINGS = (
    (18.0 / 12.0, "semilight"),
    (16.0 / 12.0, "bold"),
    (16.0 / 12.0, "regular"),
    (1.0, "bold"),
)
# The room between a list item's marker (a bullet or its number, ending there) and its text at
# 100 % scaling, and the bullet's radius.
_MARKER_GAP = 8.0
_BULLET_RADIUS = 2.2


@dataclass(frozen=True)
class Layout:
    """The room at 100 % scaling above and below a heading and around other blocks (paragraphs,
    lists; of two blocks the larger of their rooms parts them), and where a list item's text
    starts."""

    heading_above: float
    heading_below: float
    block_room: float
    item_x: float


# As help pages are laid out in Windows tools.
HELP_PAGE = Layout(heading_above=24.0, heading_below=23.0, block_room=18.0, item_x=48.0)
# Release notes: short sections of short items, closer together and less indented.
COMPACT = Layout(heading_above=16.0, heading_below=8.0, block_room=8.0, item_x=22.0)


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


def _room(block: _Block, above: bool, layout: Layout) -> float:
    if block.kind == "heading":
        return layout.heading_above if above else layout.heading_below
    return layout.block_room


def _gap(previous: _Block, block: _Block, layout: Layout) -> float:
    """Room at 100 % scaling between two blocks; the items of a list lie as close as the lines
    of a text."""
    if previous.kind == block.kind and block.kind in ("bullet", "numbered"):
        return LINE_GAP
    return max(
        _room(previous, above=False, layout=layout),
        _room(block, above=True, layout=layout),
    )


def render_markdown(text: str, layout: Layout = HELP_PAGE) -> None:
    spacing = imgui.get_style().item_spacing
    # Between blocks only the room set here.
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(spacing.x, 0.0))
    previous: _Block | None = None
    for block in _blocks(text):
        if previous is not None:
            # The block above already left the line gap below its last line.
            vertical_space(px(_gap(previous, block, layout) - LINE_GAP))
        if block.kind == "heading":
            _render_heading(block)
        elif block.kind == "paragraph":
            wrapped_text(block.text, imgui.get_content_region_avail().x)
        else:
            _render_item(block, layout.item_x)
        previous = block
    imgui.pop_style_var()


def _render_heading(block: _Block) -> None:
    scale, face = _HEADINGS[min(block.level, len(_HEADINGS)) - 1]
    width = imgui.get_content_region_avail().x
    if face == "bold":
        with bold(scale):
            wrapped_text(block.text, width)
    elif face == "semilight":
        with semilight(scale):
            wrapped_text(block.text, width)
    else:
        imgui.push_font(None, imgui.get_style().font_size_base * scale)
        wrapped_text(block.text, width)
        imgui.pop_font()


def _render_item(block: _Block, item_x: float) -> None:
    """A list item: its marker, then its text (``item_x`` in), whose wrapped lines start below
    its first."""
    left = imgui.get_cursor_screen_pos()
    draw_list = imgui.get_window_draw_list()
    color = imgui.get_color_u32(imgui.Col_.text)
    if block.kind == "bullet":
        draw_list.add_circle_filled(
            imgui.ImVec2(
                left.x + px(item_x - _MARKER_GAP - _BULLET_RADIUS),
                left.y + imgui.get_text_line_height() / 2,
            ),
            px(_BULLET_RADIUS),
            color,
        )
    else:
        # Its number ends where a bullet would.
        number = f"{block.number}."
        end = left.x + px(item_x) - px(_MARKER_GAP)
        text_layout.draw_text_at(
            number, imgui.ImVec2(end - text_layout.text_width(number), left.y)
        )
    imgui.set_cursor_screen_pos(imgui.ImVec2(left.x + px(item_x), left.y))
    wrapped_text(block.text, imgui.get_content_region_avail().x)
