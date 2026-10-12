"""A small PDF writer for monitor printouts: A4 portrait tables in standard Helvetica.

Uses the 14 standard fonts only (no embedding) with WinAnsi encoding, so the text width is
measured with Helvetica's own glyph widths. Each page has the header row with a rule under it and
a centred page footer; cells that do not fit are cut with an ellipsis.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Sequence

# A4 in points.
PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89

# Helvetica glyph widths (1/1000 em) for WinAnsi codes 32-255; 0 = no glyph.
_WIDTHS_ASCII = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584, 0,
)  # fmt: skip
_WIDTHS_HIGH = (
    556, 0, 222, 556, 333, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0,
    0, 222, 222, 333, 333, 350, 556, 1000, 333, 1000, 500, 333, 944, 0, 500, 667,
    278, 333, 556, 556, 556, 556, 260, 556, 333, 737, 370, 556, 584, 333, 737, 333,
    400, 584, 333, 333, 333, 556, 537, 278, 333, 333, 365, 556, 834, 834, 834, 611,
    667, 667, 667, 667, 667, 667, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278,
    722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611,
    556, 556, 556, 556, 556, 556, 889, 500, 556, 556, 556, 556, 278, 278, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 584, 611, 556, 556, 556, 556, 500, 556, 500,
)  # fmt: skip
_ELLIPSIS = "…"


def winansi(text: str) -> bytes:
    """``text`` in WinAnsi encoding; characters it lacks become ``?``."""
    return text.encode("cp1252", errors="replace")


def char_width(code: int) -> int:
    if 32 <= code < 128:
        return _WIDTHS_ASCII[code - 32] or 278
    if code >= 128:
        return _WIDTHS_HIGH[code - 128] or 278
    return 0


def text_width(text: str, size: float) -> float:
    """Width of ``text`` in points at font size ``size``."""
    return sum(char_width(code) for code in winansi(text)) * size / 1000.0


def fit_text(text: str, width: float, size: float) -> str:
    """``text`` cut to ``width`` points, ending in an ellipsis when it had to be cut."""
    if text_width(text, size) <= width:
        return text
    limit = width * 1000.0 / size - char_width(0x85)
    total = 0
    encoded = winansi(text)
    for index, code in enumerate(encoded):
        total += char_width(code)
        if total > limit:
            return text[:index] + _ELLIPSIS
    return text


def _escape(data: bytes) -> bytes:
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class _Page:
    def __init__(self) -> None:
        self.ops: list[bytes] = []

    def text(self, x: float, y: float, size: float, text: str) -> None:
        self.ops.append(
            b"BT /F1 %.2f Tf %.2f %.2f Td (%s) Tj ET"
            % (size, x, y, _escape(winansi(text)))
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float) -> None:
        self.ops.append(b"%.2f w %.2f %.2f m %.2f %.2f l S" % (width, x1, y1, x2, y2))

    def content(self) -> bytes:
        return b"\n".join(self.ops)


def write_pdf(pages: Sequence[bytes], title: str = "") -> bytes:
    """A PDF document from page content streams (Helvetica as /F1)."""
    objects: list[bytes] = []
    page_count = len(pages) or 1
    first_page = 4
    kids = b" ".join(b"%d 0 R" % (first_page + 2 * i) for i in range(page_count))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, page_count))
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    for content in pages or [b""]:
        number = len(objects) + 1
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
            % (PAGE_WIDTH, PAGE_HEIGHT, number + 1)
        )
        stream = zlib.compress(content)
        objects.append(
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
            + stream
            + b"\nendstream"
        )
    info = len(objects) + 1
    objects.append(
        b"<< /Title (%s) /Producer (XKNX Editor) >>" % _escape(winansi(title))
    )
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (
            len(objects) + 1,
            info,
            xref,
        )
    )
    return bytes(out)


def table_pdf(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    weights: Sequence[float],
    *,
    page_label: Callable[[int], str],
    title: str = "",
    font_size: float = 5.5,
    margin: float = 28.0,
) -> bytes:
    """A table over as many A4 portrait pages as it needs.

    ``weights`` share the page width between the columns; ``page_label`` gives the footer text of
    a 1-based page number."""
    usable = PAGE_WIDTH - 2 * margin
    total = sum(weights) or 1.0
    widths = [usable * weight / total for weight in weights]
    padding = 1.5
    line_height = font_size * 1.45
    top = PAGE_HEIGHT - margin
    footer_y = margin * 0.6
    bottom = margin + line_height
    pages: list[bytes] = []
    index = 0
    while True:
        page = _Page()
        y = top - font_size
        _row(page, headers, widths, margin, y, font_size, padding)
        rule_y = y - font_size * 0.45
        page.line(margin, rule_y, margin + usable, rule_y, 0.5)
        y = rule_y - line_height
        while index < len(rows) and y >= bottom:
            _row(page, rows[index], widths, margin, y, font_size, padding)
            index += 1
            y -= line_height
        label = page_label(len(pages) + 1)
        page.text(
            (PAGE_WIDTH - text_width(label, font_size + 1)) / 2,
            footer_y,
            font_size + 1,
            label,
        )
        pages.append(page.content())
        if index >= len(rows):
            break
    return write_pdf(pages, title)


def _row(
    page: _Page,
    cells: Sequence[str],
    widths: Sequence[float],
    left: float,
    y: float,
    size: float,
    padding: float,
) -> None:
    x = left
    for text, width in zip(cells, widths, strict=False):
        if text:
            page.text(x + padding, y, size, fit_text(text, width - 2 * padding, size))
        x += width
