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
)
# The translated languages: every UI text has a translation in each of them.
TRANSLATED: tuple[str, ...] = tuple(language.code for language in LANGUAGES[1:])


def supported(code: str) -> str:
    """``code`` ("de", "de-DE", "de_DE") as a language of the user interface, else English."""
    wanted = code.replace("_", "-").split("-")[0].lower()
    return wanted if any(language.code == wanted for language in LANGUAGES) else "en"


def culture(code: str) -> str:
    """The product data language of the user interface language ``code``, e.g. "de-DE"."""
    wanted = supported(code)
    return next(language.culture for language in LANGUAGES if language.code == wanted)
