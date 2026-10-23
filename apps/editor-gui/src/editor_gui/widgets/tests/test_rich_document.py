"""Editing a comment: typing, deleting, formatting the selection and undo."""

from __future__ import annotations

from editor_gui.widgets.rich_document import BOLD, ITALIC, UNDERLINE, RichDocument
from xknxeditor.proj.core.rich_text import Span


def test_typing_and_paragraphs() -> None:
    doc = RichDocument()
    for ch in "Hello":
        doc.insert(ch)
    doc.insert("\n")
    doc.insert("World")
    assert doc.lines == ["Hello", "World"]
    assert doc.caret == (1, 5)
    doc.move_to((0, 5))
    doc.backspace(word=True)
    assert doc.lines == ["", "World"]
    doc.delete()
    assert doc.lines == ["World"]
    assert doc.rich_text() == ((Span("World"),),)
    doc.select_all()
    doc.delete_selection()
    assert doc.rich_text() == ()


def test_formatting_the_selection_and_what_is_typed_next() -> None:
    doc = RichDocument.from_rich_text(((Span("plain bold"),),))
    doc.move_to((0, 6))
    doc.move_to((0, 10), extend=True)
    assert not doc.active(BOLD)
    doc.toggle(BOLD)
    assert doc.active(BOLD)
    assert doc.rich_text() == ((Span("plain "), Span("bold", bold=True)),)
    # Typed after bold text, a character is bold; switched off, the next is not.
    doc.move_to((0, 10))
    doc.insert("!")
    doc.toggle(BOLD)
    doc.insert("?")
    assert doc.rich_text() == ((Span("plain "), Span("bold!", bold=True), Span("?")),)
    # Over two paragraphs, italic and underline add to bold.
    doc.insert("\n")
    doc.insert("next")
    doc.move_to((0, 6))
    doc.move_to((1, 2), extend=True)
    doc.toggle(ITALIC)
    doc.toggle(UNDERLINE)
    assert doc.rich_text() == (
        (
            Span("plain "),
            Span("bold!", bold=True, italic=True, underline=True),
            Span("?", italic=True, underline=True),
        ),
        (Span("ne", italic=True, underline=True), Span("xt")),
    )


def test_undo_takes_back_typing_at_once() -> None:
    doc = RichDocument()
    for ch in "abc":
        doc.insert(ch)
    doc.insert(" ")
    doc.select_all()
    doc.toggle(BOLD)
    assert doc.undo()
    assert doc.rich_text() == ((Span("abc "),),)
    assert doc.undo()
    assert doc.rich_text() == ()
    assert not doc.undo()
    assert doc.redo()
    assert doc.lines == ["abc "]


def test_words_and_rich_paste() -> None:
    doc = RichDocument.from_rich_text(((Span("one two"),), (Span("three"),)))
    assert doc.word_at((0, 5)) == ((0, 4), (0, 7))
    assert doc.right((0, 0), word=True) == (0, 4)
    assert doc.left((1, 0)) == (0, 7)
    doc.move_to((0, 4))
    doc.move_to((1, 2), extend=True)
    assert doc.selected_text() == "two\nth"
    copied = doc.selected_rich_text()
    doc.move_to((1, 5))
    doc.insert_rich(((Span("x", bold=True),), (Span("y"),)))
    assert doc.lines == ["one two", "threex", "y"]
    assert doc.rich_text()[1] == (Span("three"), Span("x", bold=True))
    assert copied == ((Span("two"),), (Span("th"),))
