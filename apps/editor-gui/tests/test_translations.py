"""Every text passed to ``_()`` in a strings module has a translation in every UI language.

Reads the compiled catalogs, so a .po that was changed but not compiled fails too.
"""

from __future__ import annotations

import ast
import re
import struct
from pathlib import Path
from typing import cast

import pytest
from editor_gui.languages import TRANSLATED

_ROOT = Path(__file__).resolve().parents[1] / "src" / "editor_gui"
_LANGUAGES = TRANSLATED


def _modules() -> list[tuple[Path, str, Path]]:
    """``(module, domain, locale dir)`` of every strings module with a translator."""
    found: list[tuple[Path, str, Path]] = []
    for module in sorted(_ROOT.rglob("*.py")):
        if "tests" in module.parts or not (
            module.name == "strings.py" or module.parent.name == "strings"
        ):
            continue
        match = re.search(r'create_translator\(\s*"([^"]+)"', module.read_text("utf-8"))
        if match is None:
            continue
        base = module.parent.parent if module.name == "__init__.py" else module.parent
        found.append((module, match.group(1), base / "locales"))
    return found


def _read_mo(data: bytes) -> dict[str, str]:
    """The ``msgid -> msgstr`` entries of a compiled catalog (little-endian GNU .mo)."""
    magic, _revision, count, ids_at, strs_at = struct.unpack_from("<5I", data)
    assert magic == 0x950412DE, "not a little-endian .mo file"
    entries: dict[str, str] = {}
    for index in range(count):
        id_len, id_at = struct.unpack_from("<2I", data, ids_at + 8 * index)
        str_len, str_at = struct.unpack_from("<2I", data, strs_at + 8 * index)
        msgid = data[id_at : id_at + id_len].decode("utf-8")
        entries[msgid] = data[str_at : str_at + str_len].decode("utf-8")
    return entries


def _texts(module: Path) -> set[str]:
    texts: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text("utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            texts.add(node.args[0].value)
    return texts


@pytest.mark.parametrize("language", _LANGUAGES)
@pytest.mark.parametrize(
    ("module", "domain", "locale_dir"),
    [pytest.param(*entry, id=entry[1]) for entry in _modules()],
)
def test_texts_are_translated(
    module: Path, domain: str, locale_dir: Path, language: str
) -> None:
    catalog = locale_dir / language / "LC_MESSAGES" / f"{domain}.mo"
    assert catalog.exists(), f"no {language} catalog for {domain}"
    translations = _read_mo(catalog.read_bytes())
    missing = sorted(text for text in _texts(module) if not translations.get(text))
    assert not missing, (
        f"{len(missing)} {language} texts missing in {domain}: {missing[:10]}"
    )


def test_all_strings_modules_found() -> None:
    domains = {domain for _module, domain, _dir in _modules()}
    assert {"project", "editor_gui", "widgets", "monitor"} <= domains


def _plurals(module: Path) -> set[tuple[str, str]]:
    """The ``(singular, plural)`` texts passed to ``_.plural`` in a strings module."""
    texts: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(module.read_text("utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "plural"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_"
            and len(node.args) >= 2
            and all(
                isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                for arg in node.args[:2]
            )
        ):
            singular, plural = (cast(ast.Constant, arg).value for arg in node.args[:2])
            texts.add((singular, plural))
    return texts


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"%\(\w+\)[sd]|\{\w+[^}]*\}", text))


@pytest.mark.parametrize("language", _LANGUAGES)
@pytest.mark.parametrize(
    ("module", "domain", "locale_dir"),
    [pytest.param(*entry, id=entry[1]) for entry in _modules()],
)
def test_counts_have_every_plural_form(
    module: Path, domain: str, locale_dir: Path, language: str
) -> None:
    # A text with a count has as many forms as the language's plural rule, each with the
    # placeholders of the English text.
    catalog = locale_dir / language / "LC_MESSAGES" / f"{domain}.mo"
    translations = _read_mo(catalog.read_bytes())
    rule = re.search(r"nplurals=(\d+)", translations.get("", ""))
    forms = int(rule.group(1)) if rule else 2
    for singular, plural in sorted(_plurals(module)):
        translated = translations.get(f"{singular}\x00{plural}", "").split("\x00")
        assert len(translated) == forms and all(translated), (
            f"{language} {domain}: {singular!r} needs {forms} forms, has {translated}"
        )
        for form in translated:
            assert _placeholders(form) == _placeholders(plural), (
                f"{language} {domain}: {form!r} lacks placeholders of {plural!r}"
            )
