"""An application's evaluation tree is the same in every language: it reads the texts from the
program when it is evaluated, so a tree built before the translations are overlaid (as the parse
cache keeps it) shows the translated texts, as one built after them does."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from xknxeditor.namespaces import detect_version
from xknxeditor.prod.application import _programs  # pyright: ignore[reportPrivateUsage]
from xknxeditor.prod.data import to_ir
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

from .test_module_instance_comes_back import _XML as _MODULES_XML

_FIXTURES = Path(__file__).parent.parent / "fixtures"
# What a translation writes: the texts an element shows.
_TRANSLATED = ("text", "function_text", "suffix_text", "name")


def _applications() -> list[tuple[str, bytes]]:
    found = [
        (p.name, p.read_bytes()) for p in sorted(_FIXTURES.glob("*_application.xml"))
    ]
    found.append(("modules", _MODULES_XML.encode()))
    return found


def _translate(root: object, seen: set[int]) -> int:
    """Change every translatable text under ``root`` in place, as a translation does; how many."""
    if id(root) in seen:
        return 0
    seen.add(id(root))
    changed = 0
    if isinstance(root, list | tuple):
        for item in root:  # pyright: ignore[reportUnknownVariableType]
            changed += _translate(item, seen)  # pyright: ignore[reportUnknownArgumentType]
        return changed
    if not dataclasses.is_dataclass(root) or isinstance(root, type):
        return 0
    # A module argument's name is no text: placeholders name it ("{{Ch}}").
    shown = not type(root).__name__.endswith("Argument")
    for field in dataclasses.fields(root):
        value = getattr(root, field.name)
        if shown and field.name in _TRANSLATED and isinstance(value, str) and value:
            setattr(root, field.name, f"«{value}»")
            changed += 1
        else:
            changed += _translate(value, seen)
    return changed


@pytest.mark.parametrize(
    ("name", "xml"), [pytest.param(*a, id=a[0]) for a in _applications()]
)
def test_a_tree_built_before_the_translation_shows_it(name: str, xml: bytes) -> None:
    knx = to_ir(xml, detect_version(xml))
    programs = list(_programs(knx))
    before = [DynamicTreeBuilder(program) for program in programs]
    assert _translate(knx, set()) > 0
    for program, early in zip(programs, before, strict=True):
        late = DynamicTreeBuilder(program)
        shown = DynamicUI(program, tree_builder=early).ui()
        assert shown == DynamicUI(program, tree_builder=late).ui(), name
        if re.search(r"«", repr(shown)) is None:
            assert not shown, name
