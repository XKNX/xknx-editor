"""Help pages in Markdown: their blocks, their inline formatting reduced to text, and headings in
the faces and sizes of the reference's help (18 px semilight, 16 px semibold, 16 px, 12 px
semibold over text of 12 px)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets import markdown
from editor_gui.widgets.wrapping import LINE_GAP


@pytest.fixture
def frame() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    imgui.set_next_window_size(imgui.ImVec2(600, 400))
    imgui.begin("Help", None, imgui.WindowFlags_.no_saved_settings)
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def test_the_blocks_of_a_help_page() -> None:
    blocks = markdown._blocks(  # pyright: ignore[reportPrivateUsage]
        "## Fehlende **Unterstützung**\n\nEin Text\nüber zwei Zeilen.\n\n"
        "- Erstens\n* Zweitens\n1. Eins\n"
    )
    assert [(b.kind, b.text, b.level, b.number) for b in blocks] == [
        ("heading", "Fehlende Unterstützung", 2, ""),
        ("paragraph", "Ein Text über zwei Zeilen.", 0, ""),
        ("bullet", "Erstens", 0, ""),
        ("bullet", "Zweitens", 0, ""),
        ("numbered", "Eins", 0, "1"),
    ]


def test_inline_markdown_is_reduced_to_its_text() -> None:
    assert markdown.plain(
        "Siehe [Doku](http://x) und ![Bild](a.png) `Code` *kursiv*"
    ) == ("Siehe Doku und  Code kursiv")


@pytest.mark.usefixtures("frame")
@pytest.mark.parametrize(
    ("level", "face", "scale"),
    [
        (1, "semilight", 18 / 12),
        (2, "bold", 16 / 12),
        (3, "regular", 16 / 12),
        (4, "bold", 1.0),
    ],
)
def test_headings_have_the_faces_and_sizes_of_the_reference(
    monkeypatch: pytest.MonkeyPatch, level: int, face: str, scale: float
) -> None:
    used: list[tuple[str, float]] = []

    def recorder(name: str):
        @contextlib.contextmanager
        def push(scale: float = 1.0) -> Iterator[None]:
            used.append((name, scale))
            yield

        return push

    monkeypatch.setattr(markdown, "bold", recorder("bold"))
    monkeypatch.setattr(markdown, "semilight", recorder("semilight"))
    base = imgui.get_style().font_size_base
    markdown.render_markdown("#" * level + " Bekannte Limitationen")
    if face == "regular":
        assert used == []
        # Pushed and popped again: the text is drawn at 16/12 of the text size.
        assert imgui.get_font_size() == pytest.approx(base)
    else:
        assert used == [(face, pytest.approx(scale))]


@pytest.mark.usefixtures("frame")
def test_list_items_lie_as_close_as_lines() -> None:
    top = imgui.get_cursor_pos_y()
    markdown.render_markdown("- one\n- two")
    line = imgui.get_text_line_height()
    assert imgui.get_cursor_pos_y() - top == pytest.approx(2 * (line + LINE_GAP))


@pytest.mark.usefixtures("frame")
def test_paragraphs_are_parted_by_18_px() -> None:
    # 18 px between two paragraphs (a line pitch of 16 px and a gap of 16 px at 100 %).
    top = imgui.get_cursor_pos_y()
    markdown.render_markdown("One")
    one = imgui.get_cursor_pos_y() - top
    top = imgui.get_cursor_pos_y()
    markdown.render_markdown("One\n\nTwo")
    assert imgui.get_cursor_pos_y() - top == pytest.approx(2 * one + 18 - LINE_GAP)


@pytest.mark.usefixtures("frame")
def test_a_numbered_item_ends_its_number_before_its_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The text 48 px in, its number ending 8 px before it.
    drawn: list[tuple[str, float]] = []
    monkeypatch.setattr(
        markdown.text_layout,
        "draw_text_at",
        lambda text, origin: drawn.append((text, origin.x)),
    )

    def wrapped(text: str, _width: float) -> None:
        drawn.append((text, imgui.get_cursor_screen_pos().x))
        imgui.dummy(imgui.ImVec2(1, 1))

    monkeypatch.setattr(markdown, "wrapped_text", wrapped)
    monkeypatch.setattr(markdown.text_layout, "text_width", lambda _text: 9.0)
    left = imgui.get_cursor_screen_pos().x
    markdown.render_markdown("1. Einen oberen Trigger-Wert")
    assert drawn == [
        ("1.", left + 48 - 8 - 9),
        ("Einen oberen Trigger-Wert", left + 48),
    ]
