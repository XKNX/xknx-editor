"""The languages of the user interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from editor_gui.strings import S


@dataclass(frozen=True)
class Language:
    code: str  # the catalog folder under locales/, e.g. "de"; "en" is the untranslated source
    culture: str  # the language of product and master data texts, e.g. "de-DE"
    native: str  # the name in the language itself
    name: Callable[[], str]  # the name in the current language


LANGUAGES: tuple[Language, ...] = (
    Language("en", "en-US", "English", lambda: S.LANGUAGE_ENGLISH),
    Language("de", "de-DE", "Deutsch", lambda: S.LANGUAGE_GERMAN),
    Language("nl", "nl-NL", "Nederlands", lambda: S.LANGUAGE_DUTCH),
    Language("es", "es-ES", "español", lambda: S.LANGUAGE_SPANISH),
    Language("el", "el-GR", "Ελληνικά", lambda: S.LANGUAGE_GREEK),
    Language("zh", "zh-CN", "中文(中国)", lambda: S.LANGUAGE_CHINESE),
    Language("ja", "ja-JP", "日本語", lambda: S.LANGUAGE_JAPANESE),
    Language("cs", "cs-CZ", "čeština", lambda: S.LANGUAGE_CZECH),
    Language("da", "da-DK", "dansk", lambda: S.LANGUAGE_DANISH),
    Language("fi", "fi-FI", "suomi", lambda: S.LANGUAGE_FINNISH),
    Language("fr", "fr-FR", "français", lambda: S.LANGUAGE_FRENCH),
    Language("it", "it-IT", "italiano", lambda: S.LANGUAGE_ITALIAN),
    Language("nb", "nb-NO", "norsk bokmål", lambda: S.LANGUAGE_NORWEGIAN),
    Language("pl", "pl-PL", "polski", lambda: S.LANGUAGE_POLISH),
    Language("pt", "pt-PT", "português", lambda: S.LANGUAGE_PORTUGUESE),
    Language("ru", "ru-RU", "русский", lambda: S.LANGUAGE_RUSSIAN),
    Language("sv", "sv-SE", "svenska", lambda: S.LANGUAGE_SWEDISH),
    Language("tr", "tr-TR", "Türkçe", lambda: S.LANGUAGE_TURKISH),
    Language("uk", "uk-UA", "українська", lambda: S.LANGUAGE_UKRAINIAN),
)
# The translated languages: every UI text has a translation in each of them.
TRANSLATED: tuple[str, ...] = tuple(language.code for language in LANGUAGES[1:])
# Other codes the operating system may give for a language: Norwegian as "no" or "nn".
_ALIASES = {"no": "nb", "nn": "nb"}


def supported(code: str) -> str:
    """``code`` ("de", "de-DE", "de_DE") as a language of the user interface, else English."""
    wanted = code.replace("_", "-").split("-")[0].lower()
    wanted = _ALIASES.get(wanted, wanted)
    return wanted if any(language.code == wanted for language in LANGUAGES) else "en"


def culture(code: str) -> str:
    """The product data language of the user interface language ``code``, e.g. "de-DE"."""
    wanted = supported(code)
    return next(language.culture for language in LANGUAGES if language.code == wanted)
