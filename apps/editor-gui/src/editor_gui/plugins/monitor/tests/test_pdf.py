"""Tests for the PDF printout writer."""

from __future__ import annotations

import io

from pypdf import PdfReader

from editor_gui.plugins.monitor.pdf import fit_text, table_pdf, text_width


def test_text_width_uses_helvetica_metrics() -> None:
    assert text_width("i", 10) == 2.22
    assert text_width("W", 10) == 9.44
    assert text_width("Ä", 1000) == 667


def test_fit_text_adds_an_ellipsis() -> None:
    assert fit_text("short", 100, 8) == "short"
    cut = fit_text("A very long device name that does not fit", 40, 8)
    assert cut.endswith("…")
    assert text_width(cut, 8) <= 40
    assert len(cut) > 2


def test_table_spans_pages_with_footer() -> None:
    rows = [[str(i), f"Device {i}", "x" * 200] for i in range(250)]
    data = table_pdf(
        ["#", "Name", "Info"],
        rows,
        [1, 3, 4],
        page_label=lambda page: f"Page {page}",
        title="Group Monitor",
    )
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) >= 2
    first = reader.pages[0].extract_text()
    assert "Name" in first
    assert "Device 0" in first
    assert "Page 1" in first
    assert "Page 2" in reader.pages[1].extract_text()
    assert "…" in first
    box = reader.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (595, 842)


def test_empty_table_has_one_page() -> None:
    data = table_pdf(["#"], [], [1], page_label=lambda page: f"Seite {page}")
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1
    assert "Seite 1" in reader.pages[0].extract_text()
