"""Every text of the strings modules can be shown in every UI language.

Reads each property of the strings classes in English and in every translated language: it
has to give a non-empty text (or a dict of them, e.g. a label per field), and a translation has
to keep the ``{placeholders}`` of the English text, as the code fills those in with
``format()``.
"""

from __future__ import annotations

import importlib
import inspect
import string
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from editor_gui import strings
from editor_gui.languages import TRANSLATED
from editor_gui.widgets.strings import S as widget_strings

_ROOT = Path(__file__).resolve().parents[1] / "src" / "editor_gui"


def _modules() -> list[ModuleType]:
    """The strings modules: ``strings/__init__.py`` and every ``strings.py``."""
    found: list[ModuleType] = []
    for path in sorted(_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        if path.name == "strings.py":
            parts = path.relative_to(_ROOT.parent).with_suffix("").parts
        elif path.name == "__init__.py" and path.parent.name == "strings":
            parts = path.parent.relative_to(_ROOT.parent).parts
        else:
            continue
        found.append(importlib.import_module(".".join(parts)))
    return found


def _texts() -> Iterator[tuple[str, object, str]]:
    """``(id, strings object, property name)`` of every text property."""
    for module in _modules():
        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__:
                continue
            instance = cls()
            for name, member in vars(cls).items():
                if isinstance(member, property):
                    yield f"{module.__name__}.{cls.__name__}.{name}", instance, name


_TEXTS = list(_texts())


def _placeholders(text: str) -> set[str]:
    return {
        field
        for _literal, field, _spec, _conversion in string.Formatter().parse(text)
        if field is not None
    }


@pytest.fixture(autouse=True)
def _english_afterwards() -> Iterator[None]:
    yield
    strings.set_locale("en")


def test_texts_found() -> None:
    assert len(_TEXTS) > 1000


def _pairs(english: object, text: object) -> Iterator[tuple[str, object, object]]:
    """``(key, English, translated)`` of a text property: one pair, or one per dict entry."""
    if isinstance(english, dict) and isinstance(text, dict):
        for key, value in english.items():
            yield f"[{key!r}]", value, text.get(key)
    else:
        yield "", english, text


@pytest.mark.parametrize("language", ["en", *TRANSLATED])
def test_texts_in_language(language: str) -> None:
    problems: list[str] = []
    for text_id, instance, name in _TEXTS:
        strings.set_locale("en")
        english_value = getattr(instance, name)
        strings.set_locale(language)
        value = getattr(instance, name)
        # a label per field: two fields with the same label couldn't be told apart
        if isinstance(value, dict) and len(set(value.values())) != len(value):
            problems.append(f"{text_id}: the same text for two keys: {value!r}")
        for key, english, text in _pairs(english_value, value):
            where = text_id + key
            if not isinstance(english, str) or not isinstance(text, str) or not text:
                problems.append(f"{where}: {text!r}")
                continue
            try:
                same = _placeholders(text) == _placeholders(english)
            except ValueError as err:  # e.g. an unmatched "{"
                problems.append(f"{where}: {err}: {text!r}")
                continue
            if not same:
                problems.append(
                    f"{where}: {text!r} has other placeholders than {english!r}"
                )
    assert not problems, f"{len(problems)} texts in {language}:\n" + "\n".join(
        problems[:20]
    )


@pytest.mark.parametrize("language", ["en", *TRANSLATED])
def test_time_units_in_language(language: str) -> None:
    strings.set_locale(language)
    units = (
        "Hours",
        "Minutes",
        "Seconds",
        "HundredMilliseconds",
        "TenMilliseconds",
        "Milliseconds",
    )
    names = [widget_strings.time_unit(unit) for unit in units]
    assert all(names)
    assert len(set(names)) == len(units)
    # a unit without a translation is shown as it is
    assert widget_strings.time_unit("Days") == "Days"
