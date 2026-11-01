"""Record where WPF may break a line, as the table ``widgets/line_break_table.py`` of the editor.

Run on Windows after ``build.py`` (it wraps text in WPF TextBlocks, in Segoe UI Semilight 12):

    uv run python tools/xknx_sans/record_line_breaks.py

For each pair of characters x, y it tries "aa aaaa" + x + y + "bbbb" and "aa aaaa" + x + " " + y +
"bbbb" in a TextBlock just wide enough for "aa aaaa" + x: a line starts at y if WPF may break
between them (directly, or after spaces). Letters, digits and ideographs are probed through one
of each ("a", "0", "中"). The characters that behave alike form a class; the table holds the class of
each character and, per class, the classes a line may start with after it.

The other characters of the font are probed in pairs with each other. Japanese and Chinese
punctuation and the kana (which the font lacks, but the texts of those languages use) are probed
against one character of each class only, and join the class they behave like.
"""

from __future__ import annotations

import subprocess
import tempfile
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WIDGETS = REPO / "apps" / "editor-gui" / "src" / "editor_gui" / "widgets"
FONT = WIDGETS.parent / "assets" / "fonts" / "XKNXSans-Regular.ttf"
TABLE = WIDGETS / "line_break_table.py"
PREFIX = "aa aaaa"
LETTER, DIGIT, IDEOGRAPH = "a", "0", "中"
# Ranges probed against the classes only: CJK symbols and punctuation, kana, full-width forms.
EXTRA_RANGES = ((0x3000, 0x303F), (0x3040, 0x30FF), (0xFF01, 0xFF65))
# Hard line breaks (line and paragraph separator): the editor starts a new line at them itself.
HARD_BREAKS = {0x2028, 0x2029}


def _font_codes() -> list[int]:
    import sys

    sys.path.insert(0, str(WIDGETS.parents[1]))
    from editor_gui.widgets.text_layout import FontMetrics

    return sorted(FontMetrics(FONT.read_bytes()).advances)


def _probed(code: int) -> bool:
    """Whether ``code`` is probed itself (not through a letter, digit or ideograph)."""
    char = chr(code)
    category = unicodedata.category(char)
    if (
        code <= 0x20
        or code in HARD_BREAKS
        or category in ("Cc", "Mn", "Mc", "Me", "Co", "Cn")
    ):
        return False
    return category[0] not in "LN" or category in ("Nl", "No")


def _escape(text: str) -> str:
    return "".join(
        char if 0x20 < ord(char) < 0x7F and char != "\\" else f"\\u{ord(char):06x}"
        for char in text
    )


def _line_starts(cases: list[tuple[str, str]]) -> list[int]:
    """For each (prefix, text), where WPF starts the second line of text (-1: none)."""
    with tempfile.TemporaryDirectory() as folder:
        source, result = Path(folder) / "in.txt", Path(folder) / "out.txt"
        source.write_text(
            "\n".join(f"{_escape(p)}\t{_escape(t)}" for p, t in cases), encoding="utf-8"
        )
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(Path(__file__).with_name("wpf_line_starts.ps1")),
                "-family",
                "Segoe UI Semilight",
                "-size",
                "12",
                "-in",
                str(source),
                "-out",
                str(result),
            ],
            check=True,
        )
        return [int(value) for value in result.read_text(encoding="utf-8-sig").split()]


def _breaks(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], tuple[bool, bool]]:
    """For each pair: whether a line may start at the second character, directly after the
    first, and after the first and a space."""
    cases: list[tuple[str, str]] = []
    for x, y in pairs:
        cases.append((PREFIX + x, f"{PREFIX}{x}{y}bbbb"))
        cases.append((PREFIX + x, f"{PREFIX}{x} {y}bbbb"))
    starts = _line_starts(cases)
    at = len(PREFIX) + 1
    return {
        pair: (starts[2 * i] == at, starts[2 * i + 1] == at + 1)
        for i, pair in enumerate(pairs)
    }


def main() -> None:
    chars = [LETTER, DIGIT, IDEOGRAPH] + [
        chr(c) for c in _font_codes() if _probed(c) and c != 0x20
    ]
    print(len(chars), "characters, probed in pairs")
    found = _breaks([(x, y) for x in chars for y in chars])

    def signature(char: str, partners: list[str]) -> tuple[tuple[bool, bool], ...]:
        return tuple(found[char, p] for p in partners) + tuple(
            found[p, char] for p in partners
        )

    classes: dict[tuple[tuple[bool, bool], ...], list[str]] = {}
    for char in chars:
        classes.setdefault(signature(char, chars), []).append(char)
    members = sorted(
        classes.values(),
        key=lambda m: (m[0] not in (LETTER, DIGIT, IDEOGRAPH), -len(m)),
    )
    reps = [m[0] for m in members]
    extra = [
        chr(c)
        for low, high in EXTRA_RANGES
        for c in range(low, high + 1)
        if chr(c) not in chars and unicodedata.category(chr(c))[0] in "LPSZN"
    ]
    print(len(extra), "more characters, probed against", len(reps), "classes")
    found.update(
        _breaks(
            [(x, r) for x in extra for r in reps]
            + [(r, x) for x in extra for r in reps]
        )
    )
    by_signature = {signature(r, reps): i for i, r in enumerate(reps)}
    unmatched: list[str] = []
    for char in extra:
        index = by_signature.get(signature(char, reps))
        if index is None:
            unmatched.append(char)
        else:
            members[index].append(char)
    if unmatched:
        # Characters that behave like none of the classes: probed with each other as well.
        found.update(_breaks([(x, y) for x in unmatched for y in unmatched]))
        groups: dict[tuple[tuple[bool, bool], ...], list[str]] = {}
        for char in unmatched:
            groups.setdefault(signature(char, reps + unmatched), []).append(char)
        for group in groups.values():
            members.append(group)
            reps.append(group[0])
    _write(members, reps, found)


def _write(
    members: list[list[str]],
    reps: list[str],
    found: dict[tuple[str, str], tuple[bool, bool]],
) -> None:
    direct = []
    spaced = []
    for x in reps:
        direct.append(sum(1 << j for j, y in enumerate(reps) if found[x, y][0]))
        spaced.append(sum(1 << j for j, y in enumerate(reps) if found[x, y][1]))
    lines = [
        '"""Where WPF may break a line: the class of each character and, per class, the classes a',
        "line may start with after it. Generated by tools/xknx_sans/record_line_breaks.py; do not",
        'edit."""',
        "",
        "# The comments list the characters of each class, some of which look like others.",
        "# ruff: noqa: RUF003",
        "",
        "# Letters, digits and ideographs (CJK) that are not listed in CLASSES.",
        "LETTER = 0",
        "DIGIT = 1",
        "IDEOGRAPH = 2",
        "",
        "# The class of each other character, by code point.",
        "CLASSES: dict[int, int] = {",
    ]
    for index, group in enumerate(members):
        if index <= 2:
            group = [c for c in group if c not in (LETTER, DIGIT, IDEOGRAPH)]
        if not group:
            continue
        names = " ".join(
            c if c.isprintable() and not c.isspace() else f"U+{ord(c):04X}"
            for c in group[:12]
        )
        lines.append(f"    # {names}{' …' if len(group) > 12 else ''}")
        codes = [f"0x{ord(c):04X}: {index}," for c in sorted(group)]
        for start in range(0, len(codes), 6):
            lines.append("    " + " ".join(codes[start : start + 6]))
    lines += [
        "}",
        "",
        "# Per class: the classes a line may start with directly after it (bit per class).",
        "DIRECT: tuple[int, ...] = (",
        *(f"    0x{value:X}," for value in direct),
        ")",
        "# Per class: the classes a line may start with after it and spaces.",
        "AFTER_SPACES: tuple[int, ...] = (",
        *(f"    0x{value:X}," for value in spaced),
        ")",
        "",
    ]
    TABLE.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    subprocess.run(["ruff", "format", str(TABLE)], check=True)
    print(f"{len(reps)} classes written to {TABLE.relative_to(REPO)}")


if __name__ == "__main__":
    main()
