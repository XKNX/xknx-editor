"""Read-only viewer for devices configured by the Siemens ETS3 Generic Plug-in.

Works for any device whose application names the plug-in in its ``<Extension>`` and whose product
data carries the plug-in's plain-XML application description as baggage (e.g. the N 512/11
switching actuator). It reads only XML baggages (the description and its translation files) and
the settings the plug-in stored in the device's BinaryData; other baggages (the plug-in installer)
are never opened. There is no editing and no download path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .blob import PluginSettings, parse_settings
from .description import Description, parse_description, parse_translations
from .view import GenericView, MemoryCheck, Page, Row, build_view

__all__ = [
    "Description",
    "GenericView",
    "MemoryCheck",
    "Page",
    "PluginSettings",
    "Row",
    "Sources",
    "build_view",
    "find_sources",
    "is_xml_baggage",
    "parse_description",
    "parse_settings",
    "parse_translations",
    "pick_language",
    "read_view",
]


def is_xml_baggage(baggage_id: str) -> bool:
    """Whether a baggage id names an ``.xml`` file (KNX ids encode "." as ".2E")."""
    lowered = baggage_id.lower()
    return lowered.endswith((".2exml", ".xml"))


@dataclass(slots=True)
class Sources:
    """The description and the translations found among a device's XML baggages."""

    description: Description
    translations: dict[str, dict[str, str]]


def find_sources(
    baggage_ids: Iterable[str], read: Callable[[str], bytes | None]
) -> Sources | None:
    """The description and translations among ``baggage_ids`` (XML ones only), read by
    ``read``; None when no description is among them."""
    description: Description | None = None
    translations: dict[str, dict[str, str]] = {}
    for baggage_id in baggage_ids:
        if not is_xml_baggage(baggage_id):
            continue
        data = read(baggage_id)
        if not data:
            continue
        if description is None:
            parsed = parse_description(data)
            if parsed is not None:
                description = parsed
                continue
        for language, texts in parse_translations(data).items():
            translations.setdefault(language, {}).update(texts)
    if description is None:
        return None
    return Sources(description=description, translations=translations)


def pick_language(
    translations: dict[str, dict[str, str]], wanted: str
) -> dict[str, str]:
    """The texts for ``wanted`` (e.g. "de-DE"), else the same language in another region,
    else English, else none (the description's own texts are then used)."""
    if wanted in translations:
        return translations[wanted]
    base = wanted.split("-")[0].lower()
    for code, texts in translations.items():
        if code.split("-")[0].lower() == base:
            return texts
    for code, texts in translations.items():
        if code.lower().startswith("en"):
            return texts
    return {}


def read_view(
    sources: Sources,
    blob: bytes,
    language: str = "en-US",
    globals_: dict[str, str] | None = None,
) -> GenericView | None:
    """The view of a device's BinaryData ``blob``; None when it holds no plug-in settings."""
    settings = parse_settings(blob)
    if settings is None:
        return None
    texts = pick_language(sources.translations, language)
    return build_view(sources.description, settings, texts, globals_)
