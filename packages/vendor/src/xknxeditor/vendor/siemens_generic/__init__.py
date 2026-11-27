"""Siemens Generic Plug-in XML configuration, editing and comparison."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from .blob import PluginSettings, parse_settings
from .description import Description, parse_description, parse_translations
from .view import (
    ComparePage,
    CompareRow,
    GenericView,
    MemoryCheck,
    Page,
    Row,
    ViewComparison,
    build_view,
    compare_views,
)

__all__ = [
    "ComparePage",
    "CompareRow",
    "Description",
    "GenericView",
    "MemoryCheck",
    "Page",
    "PluginSettings",
    "Row",
    "Sources",
    "ViewComparison",
    "build_view",
    "compare_views",
    "find_sources",
    "is_xml_baggage",
    "parse_description",
    "parse_settings",
    "parse_translations",
    "pick_language",
    "plugin_version",
    "read_view",
]


def is_xml_baggage(baggage_id: str) -> bool:
    """Whether a baggage id names an ``.xml`` file (KNX ids encode "." as ".2E")."""
    lowered = baggage_id.lower()
    return lowered.endswith((".2exml", ".xml"))


# The plug-in's setup in the baggage names its version, e.g. "...PlugInSetup_V2_0_37.msi".
_SETUP = re.compile(r"PlugInSetup_V(\d+)_(\d+)_(\d+)\.msi$", re.IGNORECASE)


def plugin_version(baggage_ids: Iterable[str]) -> tuple[int, int, int] | None:
    """The version of the plug-in whose setup is among ``baggage_ids`` (KNX ids encode
    characters as ".XX", e.g. "_" as ".5F"); None when none is."""
    for baggage_id in baggage_ids:
        name = re.sub(
            r"\.([0-9A-F]{2})", lambda m: chr(int(m.group(1), 16)), baggage_id
        )
        found = _SETUP.search(name)
        if found is not None:
            major, minor, build = (int(v) for v in found.groups())
            return major, minor, build
    return None


@dataclass(slots=True)
class Sources:
    """The description and the translations found among a device's XML baggages."""

    description: Description
    translations: dict[str, dict[str, str]]


def find_sources(
    baggage_ids: Iterable[str], read: Callable[[str], bytes | None]
) -> Sources | None:
    """The description and translations among ``baggage_ids`` (XML ones only), read by
    ``read``, with the version of the plug-in whose setup is among them; None when no
    description is among them."""
    baggage_ids = tuple(baggage_ids)
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
    description = replace(description, plugin_version=plugin_version(baggage_ids))
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
    show_unfitted: bool = False,
) -> GenericView | None:
    """The view of a device's BinaryData ``blob``; None when it holds no plug-in settings."""
    settings = parse_settings(blob)
    if settings is None:
        return None
    texts = pick_language(sources.translations, language)
    return build_view(sources.description, settings, texts, globals_, show_unfitted)
