"""The text of a rich text field while it is edited: paragraphs of characters, each bold, italic
and/or underlined, with a caret and a selection; and the editing steps on them."""

from __future__ import annotations

from dataclasses import dataclass, field

from xknxeditor.proj.core.rich_text import RichText, Span

BOLD = 1
ITALIC = 2
UNDERLINE = 4

# A place in the text: (paragraph, character index in it).
Pos = tuple[int, int]


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


@dataclass
class _Snapshot:
    lines: list[str]
    formats: list[list[int]]
    caret: Pos
    anchor: Pos


@dataclass
class RichDocument:
    """Paragraphs (``lines``) with one format (``BOLD | ITALIC | UNDERLINE``) per character."""

    lines: list[str] = field(default_factory=lambda: [""])
    formats: list[list[int]] = field(default_factory=lambda: [[]])
    caret: Pos = (0, 0)
    # The other end of the selection; equal to ``caret`` without one.
    anchor: Pos = (0, 0)
    # The format typed text gets: the one before the caret, or as switched with no selection.
    typing_format: int = 0
    _undo: list[_Snapshot] = field(default_factory=list[_Snapshot])
    _redo: list[_Snapshot] = field(default_factory=list[_Snapshot])
    # Whether the last step was typing, which a further character joins in one undo step.
    _typing: bool = False

    @classmethod
    def from_rich_text(cls, text: RichText) -> RichDocument:
        doc = cls()
        if text:
            doc.lines = []
            doc.formats = []
            for paragraph in text:
                doc.lines.append("".join(span.text for span in paragraph))
                doc.formats.append(
                    [_format_of(span) for span in paragraph for _ in span.text]
                )
        return doc

    def rich_text(self) -> RichText:
        paragraphs: list[tuple[Span, ...]] = []
        for line, formats in zip(self.lines, self.formats, strict=True):
            spans: list[Span] = []
            start = 0
            for i in range(1, len(line) + 1):
                if i == len(line) or formats[i] != formats[start]:
                    fmt = formats[start]
                    spans.append(
                        Span(
                            line[start:i],
                            bold=bool(fmt & BOLD),
                            italic=bool(fmt & ITALIC),
                            underline=bool(fmt & UNDERLINE),
                        )
                    )
                    start = i
            paragraphs.append(tuple(spans))
        if len(paragraphs) == 1 and not paragraphs[0]:
            return ()
        return tuple(paragraphs)

    # --- selection -------------------------------------------------------------------------

    def has_selection(self) -> bool:
        return self.caret != self.anchor

    def selection(self) -> tuple[Pos, Pos]:
        return (min(self.caret, self.anchor), max(self.caret, self.anchor))

    def selected_text(self) -> str:
        start, end = self.selection()
        return self._text_between(start, end)

    def selected_rich_text(self) -> RichText:
        start, end = self.selection()
        part = RichDocument(lines=[], formats=[])
        for line in range(start[0], end[0] + 1):
            a = start[1] if line == start[0] else 0
            b = end[1] if line == end[0] else len(self.lines[line])
            part.lines.append(self.lines[line][a:b])
            part.formats.append(self.formats[line][a:b])
        return part.rich_text()

    def _text_between(self, start: Pos, end: Pos) -> str:
        if start[0] == end[0]:
            return self.lines[start[0]][start[1] : end[1]]
        parts = [self.lines[start[0]][start[1] :]]
        parts += self.lines[start[0] + 1 : end[0]]
        parts.append(self.lines[end[0]][: end[1]])
        return "\n".join(parts)

    def move_to(self, pos: Pos, *, extend: bool = False) -> None:
        """Put the caret at ``pos``; ``extend`` keeps the selection's other end."""
        self.caret = self.clamp(pos)
        if not extend:
            self.anchor = self.caret
        self.typing_format = self._format_at_caret()
        self._typing = False

    def select_all(self) -> None:
        self.anchor = (0, 0)
        self.caret = (len(self.lines) - 1, len(self.lines[-1]))
        self._typing = False

    def clamp(self, pos: Pos) -> Pos:
        line = max(0, min(pos[0], len(self.lines) - 1))
        return (line, max(0, min(pos[1], len(self.lines[line]))))

    def _format_at_caret(self) -> int:
        line, col = self.caret
        formats = self.formats[line]
        if col > 0:
            return formats[col - 1]
        return formats[0] if formats else 0

    # --- caret movement --------------------------------------------------------------------

    def left(self, pos: Pos, *, word: bool = False) -> Pos:
        line, col = pos
        if col == 0:
            return (line - 1, len(self.lines[line - 1])) if line > 0 else pos
        if not word:
            return (line, col - 1)
        text = self.lines[line]
        while col > 0 and not _is_word_char(text[col - 1]):
            col -= 1
        while col > 0 and _is_word_char(text[col - 1]):
            col -= 1
        return (line, col)

    def right(self, pos: Pos, *, word: bool = False) -> Pos:
        line, col = pos
        text = self.lines[line]
        if col >= len(text):
            return (line + 1, 0) if line + 1 < len(self.lines) else pos
        if not word:
            return (line, col + 1)
        while col < len(text) and _is_word_char(text[col]):
            col += 1
        while col < len(text) and not _is_word_char(text[col]):
            col += 1
        return (line, col)

    def word_at(self, pos: Pos) -> tuple[Pos, Pos]:
        """The word (or the run of other characters) at ``pos``, for a double click."""
        line, col = pos
        text = self.lines[line]
        if not text:
            return pos, pos
        probe = min(col, len(text) - 1)
        kind = _is_word_char(text[probe])
        start = end = probe
        while start > 0 and _is_word_char(text[start - 1]) == kind:
            start -= 1
        while end < len(text) and _is_word_char(text[end]) == kind:
            end += 1
        return (line, start), (line, end)

    # --- editing ---------------------------------------------------------------------------

    def insert(self, text: str) -> None:
        """Type or paste ``text`` (lines separated by "\\n") in the typing format, replacing the
        selection."""
        if not text:
            return
        typing = len(text) == 1 and text != "\n"
        if not (typing and self._typing and not self.has_selection()):
            self._remember()
        fmt = self.typing_format
        self._delete_selection()
        line, col = self.caret
        head, tail = self.lines[line][:col], self.lines[line][col:]
        head_formats, tail_formats = self.formats[line][:col], self.formats[line][col:]
        parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if len(parts) == 1:
            self.lines[line] = head + parts[0] + tail
            self.formats[line] = head_formats + [fmt] * len(parts[0]) + tail_formats
            self.caret = (line, col + len(parts[0]))
        else:
            new_lines = [head + parts[0], *parts[1:-1], parts[-1] + tail]
            new_formats = [
                head_formats + [fmt] * len(parts[0]),
                *([fmt] * len(p) for p in parts[1:-1]),
                [fmt] * len(parts[-1]) + tail_formats,
            ]
            self.lines[line : line + 1] = new_lines
            self.formats[line : line + 1] = new_formats
            self.caret = (line + len(parts) - 1, len(parts[-1]))
        self.anchor = self.caret
        self.typing_format = fmt
        self._typing = typing

    def insert_rich(self, text: RichText) -> None:
        """Paste formatted text, replacing the selection."""
        if not text:
            return
        self._remember()
        self._delete_selection()
        pasted = RichDocument.from_rich_text(text)
        line, col = self.caret
        tail, tail_formats = self.lines[line][col:], self.formats[line][col:]
        self.lines[line] = self.lines[line][:col] + pasted.lines[0]
        self.formats[line] = self.formats[line][:col] + pasted.formats[0]
        if len(pasted.lines) == 1:
            end = (line, col + len(pasted.lines[0]))
        else:
            self.lines[line + 1 : line + 1] = pasted.lines[1:]
            self.formats[line + 1 : line + 1] = pasted.formats[1:]
            end = (line + len(pasted.lines) - 1, len(pasted.lines[-1]))
        self.lines[end[0]] += tail
        self.formats[end[0]] += tail_formats
        self.caret = self.anchor = end
        self.typing_format = self._format_at_caret()
        self._typing = False

    def backspace(self, *, word: bool = False) -> None:
        if not self.has_selection():
            self.anchor = self.left(self.caret, word=word)
        self.delete_selection()

    def delete(self, *, word: bool = False) -> None:
        if not self.has_selection():
            self.anchor = self.right(self.caret, word=word)
        self.delete_selection()

    def delete_selection(self) -> None:
        if not self.has_selection():
            return
        self._remember()
        self._delete_selection()
        self.typing_format = self._format_at_caret()
        self._typing = False

    def _delete_selection(self) -> None:
        if not self.has_selection():
            return
        (l1, c1), (l2, c2) = self.selection()
        self.lines[l1 : l2 + 1] = [self.lines[l1][:c1] + self.lines[l2][c2:]]
        self.formats[l1 : l2 + 1] = [self.formats[l1][:c1] + self.formats[l2][c2:]]
        self.caret = self.anchor = (l1, c1)

    def toggle(self, flag: int) -> None:
        """Bold, italic or underline on or off: for the selection, or for what is typed next."""
        if not self.has_selection():
            self.typing_format ^= flag
            self._typing = False
            return
        self._remember()
        on = not self.active(flag)
        start, end = self.selection()
        for line in range(start[0], end[0] + 1):
            a = start[1] if line == start[0] else 0
            b = end[1] if line == end[0] else len(self.lines[line])
            formats = self.formats[line]
            for i in range(a, b):
                formats[i] = formats[i] | flag if on else formats[i] & ~flag
        self._typing = False

    def active(self, flag: int) -> bool:
        """Whether the selection (all of it) or the typing format has ``flag``."""
        if not self.has_selection():
            return bool(self.typing_format & flag)
        start, end = self.selection()
        seen = False
        for line in range(start[0], end[0] + 1):
            a = start[1] if line == start[0] else 0
            b = end[1] if line == end[0] else len(self.lines[line])
            for fmt in self.formats[line][a:b]:
                seen = True
                if not fmt & flag:
                    return False
        return seen

    # --- undo ------------------------------------------------------------------------------

    def _snapshot(self) -> _Snapshot:
        return _Snapshot(
            list(self.lines),
            [list(f) for f in self.formats],
            self.caret,
            self.anchor,
        )

    def _remember(self) -> None:
        self._undo.append(self._snapshot())
        del self._undo[:-200]
        self._redo.clear()

    def _restore(self, snapshot: _Snapshot) -> None:
        self.lines, self.formats = snapshot.lines, snapshot.formats
        self.caret, self.anchor = snapshot.caret, snapshot.anchor
        self.typing_format = self._format_at_caret()
        self._typing = False

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        return True


def _format_of(span: Span) -> int:
    return (
        (BOLD if span.bold else 0)
        | (ITALIC if span.italic else 0)
        | (UNDERLINE if span.underline else 0)
    )
