"""Overlay KNX language translations onto a parsed application IR.

A ``.knxprod`` application XML carries its authoring-language text inline on each element and provides
per-language overrides under ``<Manufacturer><Languages><Language Identifier="de-DE">``. Each
``TranslationElement`` points (by ``RefId``) at an element ``Id`` and lists ``Translation`` entries
(``AttributeName`` -> localized ``Text``). :func:`apply_translations` writes the chosen language's
texts back onto the referenced IR objects in place, so the dynamic-UI parser yields localized labels
(parameter/tab/block names, com-object texts, enum captions, ...).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.knx import Knx
    from xknxeditor.namespaces.intermediate.language_data_t import LanguageData
    from xknxeditor.namespaces.intermediate.manufacturer_data_t_manufacturer import (
        ManufacturerDataManufacturer,
    )

# metadata["name"] (KNX PascalCase, e.g. "Text") -> python field name (e.g. "text"), cached per type.
_FIELD_MAP_CACHE: dict[type, dict[str, str]] = {}


def apply_translations(knx: Knx, language: str) -> None:
    """Overlay ``language``'s translations onto the application IR in place.

    ``language`` may be a full KNX identifier ("de-DE") or a prefix ("de"), or several in order of
    preference, comma separated ("sk-SK,cs-CZ"); the best matching ``<Language>`` block per
    manufacturer is used. A language the product is written in keeps its own texts, as does a
    product that has none of them."""
    md = knx.manufacturer_data
    if md is None:
        return
    for manufacturer in md.manufacturer:
        langs = manufacturer.languages
        if langs is None or not langs.language:
            continue
        chosen = _pick_language(
            langs.language, language, _default_language(manufacturer)
        )
        if chosen is None:
            continue
        index = _index_by_id(manufacturer)
        for unit in chosen.translation_unit:
            for element in unit.translation_element:
                target = index.get(element.ref_id)
                if target is None:
                    continue
                field_map = _field_name_map(type(target))
                for translation in element.translation:
                    field_name = field_map.get(translation.attribute_name)
                    if field_name is not None:
                        setattr(target, field_name, translation.text)


def _pick_language(
    languages: list[LanguageData], wanted: str, default: str = ""
) -> LanguageData | None:
    """Best match for ``wanted``: the block of the first of its comma separated languages the
    product has. ``None`` when that first one is ``default``, the language the product is written
    in (its own texts are used then), or when the product has none of them."""
    for one in (w.strip() for w in wanted.split(",")):
        if default and _same_language(one, default):
            return None
        chosen = _pick_one(languages, one)
        if chosen is not None:
            return chosen
    return None


def _same_language(a: str, b: str) -> bool:
    return a.lower() == b.lower() or a.lower().split("-")[0] == b.lower().split("-")[0]


def _default_language(manufacturer: ManufacturerDataManufacturer) -> str:
    """The language a manufacturer's application program is written in ("" when not given)."""
    programs = manufacturer.application_programs
    if programs is None:
        return ""
    return next(
        (
            p.default_language
            for p in programs.application_program
            if p.default_language
        ),
        "",
    )


def _pick_one(languages: list[LanguageData], wanted: str) -> LanguageData | None:
    """Best match for ``wanted``: exact identifier, else the same language in another region or
    without one ("de-DE" -> "de", "de-AT"; "de" -> "de-DE")."""
    wanted_l = wanted.lower()
    exact = next(
        (lang for lang in languages if lang.identifier.lower() == wanted_l), None
    )
    if exact is not None:
        return exact
    base = wanted_l.split("-")[0]
    return next(
        (lang for lang in languages if lang.identifier.lower().split("-")[0] == base),
        None,
    )


def _index_by_id(root: Any) -> dict[str, object]:
    """Index every dataclass object under ``root`` that has a non-empty string ``id`` attribute."""
    index: dict[str, object] = {}
    stack: list[Any] = [root]
    seen: set[int] = set()
    while stack:
        obj = stack.pop()
        if obj is None or id(obj) in seen:
            continue
        if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
            continue
        seen.add(id(obj))
        oid = getattr(obj, "id", None)
        if isinstance(oid, str) and oid:
            index.setdefault(oid, obj)
        for f in dataclasses.fields(obj):
            value: Any = getattr(obj, f.name)
            if isinstance(value, list):
                stack.extend(value)  # pyright: ignore[reportUnknownArgumentType]
            elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                stack.append(value)
    return index


def _field_name_map(cls: type) -> dict[str, str]:
    cached = _FIELD_MAP_CACHE.get(cls)
    if cached is not None:
        return cached
    mapping: dict[str, str] = {}
    for f in dataclasses.fields(cls):
        meta_name = f.metadata.get("name")
        if isinstance(meta_name, str):
            mapping[meta_name] = f.name
    _FIELD_MAP_CACHE[cls] = mapping
    return mapping
