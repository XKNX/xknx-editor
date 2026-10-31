"""Text in XKNX Sans laid out as WPF does: its widths, its line breaks, and drawn with kerning.

ImGui adds up the advance widths of the glyphs and does not kern. WPF (in its "ideal" text
formatting mode, the default) kerns each pair of characters and rounds the advance of each glyph,
with its kerning, to 1/300 of a device-independent pixel. Text measured and wrapped here breaks
at the same places as in a WPF TextBlock, and text drawn here keeps the same gaps between its
letters.

The advances and the kerning come from the font files themselves (their "cmap", "hmtx" and "kern"
tables), where to break from ``line_break_table`` (measured from WPF). Characters the font lacks
(Chinese and Japanese letters, icons) are measured by ImGui.
"""

from __future__ import annotations

import struct
import unicodedata
from functools import cache, lru_cache
from itertools import pairwise
from pathlib import Path

from imgui_bundle import imgui

from editor_gui.widgets import line_break_table as table
from editor_gui.widgets.dpi import px

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
# The ImGui font size (ascent plus descent) for letters one em high: the font's line is 14/12 em.
_LINE_PER_EM = 14.0 / 12.0
# WPF keeps the advance of each glyph in 1/300 of a device-independent pixel.
_STEPS_PER_DIP = 300
# WPF shapes a line in pieces of 125 characters: a character that starts one is not kerned with
# the one before, unless it is a letter (see _kerned).
_PIECE = 125


class FontMetrics:
    """The advance of each character and the kerning of each pair, in font units."""

    def __init__(self, data: bytes) -> None:
        tables = _tables(data)
        self.units_per_em: int = struct.unpack_from(">H", data, tables["head"] + 18)[0]
        metrics = struct.unpack_from(">H", data, tables["hhea"] + 34)[0]
        hmtx = tables["hmtx"]
        widths = [
            struct.unpack_from(">H", data, hmtx + 4 * i)[0] for i in range(metrics)
        ]
        glyphs = _cmap(data, tables["cmap"])
        self.advances: dict[int, int] = {
            code: widths[min(glyph, metrics - 1)] for code, glyph in glyphs.items()
        }
        self.kerning: dict[tuple[int, int], int] = {}
        if "kern" in tables:
            codes: dict[int, list[int]] = {}
            for code, glyph in glyphs.items():
                codes.setdefault(glyph, []).append(code)
            for (left, right), value in _kern(data, tables["kern"]).items():
                for a in codes.get(left, ()):
                    for b in codes.get(right, ()):
                        self.kerning[a, b] = value


def _tables(data: bytes) -> dict[str, int]:
    count = struct.unpack_from(">H", data, 4)[0]
    offsets: dict[str, int] = {}
    for i in range(count):
        tag, _checksum, offset, _length = struct.unpack_from(
            ">4sIII", data, 12 + 16 * i
        )
        offsets[tag.decode("latin-1")] = offset
    return offsets


def _cmap(data: bytes, cmap: int) -> dict[int, int]:
    """The glyph of each character, from the Unicode subtable (format 12, else format 4)."""
    count = struct.unpack_from(">H", data, cmap + 2)[0]
    found: dict[int, int] = {}
    for i in range(count):
        platform, encoding, offset = struct.unpack_from(">HHI", data, cmap + 4 + 8 * i)
        if platform == 3 and encoding in (1, 10):
            found[struct.unpack_from(">H", data, cmap + offset)[0]] = cmap + offset
    glyphs: dict[int, int] = {}
    if 12 in found:
        subtable = found[12]
        groups = struct.unpack_from(">I", data, subtable + 12)[0]
        for i in range(groups):
            start, end, glyph = struct.unpack_from(">III", data, subtable + 16 + 12 * i)
            for code in range(start, end + 1):
                glyphs[code] = glyph + code - start
        return glyphs
    subtable = found[4]
    segments = struct.unpack_from(">H", data, subtable + 6)[0] // 2
    ends = subtable + 14
    starts = ends + 2 * segments + 2
    deltas = starts + 2 * segments
    ranges = deltas + 2 * segments
    for i in range(segments):
        end = struct.unpack_from(">H", data, ends + 2 * i)[0]
        start = struct.unpack_from(">H", data, starts + 2 * i)[0]
        delta = struct.unpack_from(">h", data, deltas + 2 * i)[0]
        offset = struct.unpack_from(">H", data, ranges + 2 * i)[0]
        for code in range(start, min(end, 0xFFFE) + 1):
            if offset:
                at = ranges + 2 * i + offset + 2 * (code - start)
                glyph = struct.unpack_from(">H", data, at)[0]
                glyph = (glyph + delta) & 0xFFFF if glyph else 0
            else:
                glyph = (code + delta) & 0xFFFF
            if glyph:
                glyphs[code] = glyph
    return glyphs


def _kern(data: bytes, kern: int) -> dict[tuple[int, int], int]:
    """The pairs of the first horizontal format-0 subtable of the "kern" table."""
    count = struct.unpack_from(">H", data, kern + 2)[0]
    at = kern + 4
    for _ in range(count):
        _version, length, coverage = struct.unpack_from(">HHH", data, at)
        if coverage >> 8 == 0 and coverage & 1:
            pairs = struct.unpack_from(">H", data, at + 6)[0]
            return {
                (left, right): value
                for left, right, value in (
                    struct.unpack_from(">HHh", data, at + 14 + 6 * i)
                    for i in range(pairs)
                )
            }
        at += length
    return {}


@cache
def metrics(face: str) -> FontMetrics:
    """The metrics of the font file ``face`` (a path below the assets folder)."""
    return FontMetrics((_ASSETS / face).read_bytes())


# The font files measured here; ImGui names a font after the file it was loaded from.
FACES = ("XKNXSans-Regular.ttf", "XKNXSans-Bold.ttf")


def _face(font: imgui.ImFont) -> str | None:
    """The font file of ``font`` if it is measured here (its slanted variants as well)."""
    name = (
        font.get_debug_name()
        .replace("\\", "/")
        .rsplit("/", 1)[-1]
        .split(",")[0]
        .strip()
    )
    return name if name in FACES else None


def _current() -> FontMetrics | None:
    face = _face(imgui.get_font())
    return metrics(f"fonts/{face}") if face else None


def font_size() -> float:
    """The size of the current font before ImGui rounds it to whole pixels (as its glyphs are
    baked): text laid out and drawn at it is as large as asked for, also at 125 % scaling or in a
    headline 14/12 times the text size."""
    style = imgui.get_style()
    return (
        imgui.get_current_context().font_size_base
        * style.font_scale_main
        * style.font_scale_dpi
    )


def _letter(char: str) -> bool:
    return unicodedata.category(char)[0] == "L"


class _Paragraph:
    """The advances of a paragraph's characters in the current font, kerned and not."""

    def __init__(self, text: str) -> None:
        self.text = text
        font = _current()
        # Advances in 1/300 device-independent pixels; characters the font lacks are measured
        # in pixels by ImGui (``self.extra``).
        kerned: list[int] = []
        plain: list[int] = []
        self.extra = [0.0] * (len(text) + 1)
        size = font_size()
        baked = imgui.get_font().get_font_baked(size)
        baked_scale = size / baked.size
        em = size / _LINE_PER_EM / px(1.0)
        scale = em * _STEPS_PER_DIP / font.units_per_em if font else 0.0
        advances = font.advances if font else {}
        pairs = font.kerning if font else {}
        for index, char in enumerate(text):
            units = advances.get(ord(char))
            if units is None:
                kerned.append(0)
                plain.append(0)
                advance = baked.find_glyph(ord(char)).advance_x * baked_scale
                self.extra[index + 1] = self.extra[index] + advance
                continue
            self.extra[index + 1] = self.extra[index]
            plain.append(round(units * scale))
            following = text[index + 1] if index + 1 < len(text) else ""
            kern = pairs.get((ord(char), ord(following)), 0) if following else 0
            kerned.append(round((units + kern) * scale))
        self.kerned = kerned
        self.plain = plain
        self.sums = [0]
        for value in kerned:
            self.sums.append(self.sums[-1] + value)

    def width(self, start: int, end: int) -> float:
        """The width of the line ``text[start:end]``, in pixels.

        Each character is kerned with the next one, the last one with the first character of
        the next line as well. WPF shapes a line in pieces of 125 characters, and a piece that
        starts with a character other than a letter is not kerned with the piece before.
        """
        if end <= start:
            return 0.0
        steps = self.sums[end] - self.sums[start]
        for index in range(start + _PIECE - 1, end, _PIECE):
            if index + 1 < len(self.text) and not _letter(self.text[index + 1]):
                steps += self.plain[index] - self.kerned[index]
        extra = self.extra[end] - self.extra[start]
        return steps / _STEPS_PER_DIP * px(1.0) + extra


def line_width(text: str, start: int = 0, end: int | None = None) -> float:
    """The width of ``text[start:end]`` as one line in the current font, in pixels."""
    return _Paragraph(text).width(start, len(text) if end is None else end)


def text_width(text: str) -> float:
    """The width of ``text`` (one line) in the current font, in pixels."""
    return line_width(text)


def _class(char: str) -> int:
    found = table.CLASSES.get(ord(char))
    if found is not None:
        return found
    category = unicodedata.category(char)
    if category == "Nd":
        return table.DIGIT
    code = ord(char)
    if 0x2E80 <= code <= 0x9FFF or 0xAC00 <= code <= 0xD7AF or 0xF900 <= code <= 0xFAFF:
        return table.IDEOGRAPH
    if 0x20000 <= code <= 0x3FFFF:
        return table.IDEOGRAPH
    return table.LETTER


def _mark(char: str) -> bool:
    return unicodedata.category(char) in ("Mn", "Mc", "Me")


def break_opportunities(text: str) -> list[int]:
    """The places in a paragraph ``text`` (no line feeds) a line may start at: after the spaces
    it starts with, and where WPF may break between two characters, directly or after spaces."""
    places: list[int] = []
    previous: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if char == " ":
            end = index
            while end < len(text) and text[end] == " ":
                end += 1
            if (
                end < len(text)
                and not _mark(text[end])
                and (
                    previous is None
                    or table.AFTER_SPACES[_class(previous)] >> _class(text[end]) & 1
                )
            ):
                places.append(end)
            index = end
            continue
        if (
            previous is not None
            and text[index - 1] != " "
            and not _mark(char)
            and table.DIRECT[_class(previous)] >> _class(char) & 1
        ):
            places.append(index)
        if not _mark(char):
            previous = char
        index += 1
    return places


def line_starts(text: str, width: float) -> list[int]:
    """Where the lines of a paragraph ``text`` wrapped at ``width`` pixels start, the first line
    aside: at the last place a line may start whose line still fits, and within a word only when
    no such place fits."""
    places = [*break_opportunities(text), len(text)]
    paragraph = _Paragraph(text)
    starts: list[int] = []
    start = 0
    while start < len(text):
        # A line that starts with spaces (after a word cut in two) may break after them, as
        # after a letter and spaces.
        lead = start
        while lead < len(text) and text[lead] == " ":
            lead += 1
        candidates = [p for p in places if p > start]
        if (
            start
            and start < lead < len(text)
            and lead not in candidates
            and table.AFTER_SPACES[table.LETTER] >> _class(text[lead]) & 1
        ):
            candidates = sorted([lead, *candidates])
        best = None
        for place in candidates:
            # Spaces at the end of a line hang over its edge.
            end = start + len(text[start:place].rstrip(" "))
            if paragraph.width(start, end) <= width:
                best = place
            else:
                break
        if best is None:
            best = _cut(text, paragraph, start, candidates[0], width)
        if best >= len(text):
            break
        starts.append(best)
        start = best
    return starts


def _cut(text: str, paragraph: _Paragraph, start: int, stop: int, width: float) -> int:
    """Where a line from ``start`` cuts a word that is too wide for it (``stop`` is the next
    place a line may start): after the last character that fits, spaces counted, but not before
    a mark; after the first character if none fits."""
    cut = start + 1
    while cut < stop and (paragraph.width(start, cut + 1) <= width or _mark(text[cut])):
        cut += 1
    return cut


def wrap(text: str, width: float) -> list[str]:
    """``text`` broken into lines at most ``width`` pixels wide, as a WPF TextBlock breaks it;
    a line feed starts a new line. The lines lose the spaces they end with."""
    name = imgui.get_font().get_debug_name()
    return list(_wrap(text, width, name, font_size(), px(1.0)))


@lru_cache(maxsize=8192)
def _wrap(
    text: str, width: float, _font: str, _size: float, _scale: float
) -> tuple[str, ...]:
    """``wrap`` in the current font, cached by the font, its size and the scaling as well."""
    lines: list[str] = []
    for paragraph in text.replace("\u2029", "\n").replace("\u2028", "\n").split("\n"):
        cuts = [0, *line_starts(paragraph, width), len(paragraph)]
        lines.extend(paragraph[a:b].rstrip(" ") for a, b in pairwise(cuts))
    return tuple(lines)


def draw_text(text: str) -> None:
    """``text`` (one line) at the cursor as one item, laid out at the size of the current font
    before rounding (see ``font_size``) with the gaps WPF gives its letters.

    Each glyph starts at the whole pixel nearest to where WPF places it and keeps the size ImGui
    rasterized it at, so its bitmap is drawn as rasterized (not blurred across two pixels);
    glyphs whose own advances lead to those pixels are drawn together."""
    if _current() is None:
        imgui.text_unformatted(text)
        return
    paragraph = _Paragraph(text)
    origin = imgui.get_cursor_screen_pos()
    font = imgui.get_font()
    baked = font.get_font_baked(font_size())
    size = baked.size
    draw = imgui.get_window_draw_list()
    color = imgui.get_color_u32(imgui.Col_.text)
    y = round(origin.y)
    places = [round(origin.x + paragraph.width(0, index)) for index in range(len(text))]
    start = 0
    reached = 0.0
    for index in range(len(text) + 1):
        if index < len(text) and index > start and places[index] == round(reached):
            reached += baked.find_glyph(ord(text[index])).advance_x
            continue
        if index > start:
            pos = imgui.ImVec2(places[start], y)
            draw.add_text(font, size, pos, color, text[start:index])
        if index < len(text):
            start = index
            reached = places[index] + baked.find_glyph(ord(text[index])).advance_x
    imgui.dummy(
        imgui.ImVec2(paragraph.width(0, len(text)), imgui.get_text_line_height())
    )
