"""The languages of the user interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from editor_gui.strings import S, get_locale


@dataclass(frozen=True)
class Language:
    code: str  # the catalog folder under locales/, e.g. "de"; "en" is the untranslated source
    # The language of product and master data texts, e.g. "de-DE": one of the KNX master data's
    # languages, English for a UI language that is none of them.
    culture: str
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
    Language("hu", "hu-HU", "magyar", lambda: S.LANGUAGE_HUNGARIAN),
    Language("is", "is-IS", "íslenska", lambda: S.LANGUAGE_ICELANDIC),
    Language("it", "it-IT", "italiano", lambda: S.LANGUAGE_ITALIAN),
    Language("nb", "nb-NO", "norsk bokmål", lambda: S.LANGUAGE_NORWEGIAN),
    Language("pl", "pl-PL", "polski", lambda: S.LANGUAGE_POLISH),
    Language("pt", "pt-PT", "português", lambda: S.LANGUAGE_PORTUGUESE),
    Language("ro", "ro-RO", "română", lambda: S.LANGUAGE_ROMANIAN),
    Language("ru", "ru-RU", "русский", lambda: S.LANGUAGE_RUSSIAN),
    Language("sk", "sk-SK", "slovenčina", lambda: S.LANGUAGE_SLOVAK),
    Language("sl", "sl-SI", "slovenščina", lambda: S.LANGUAGE_SLOVENIAN),
    # The KNX master data has no Albanian: products and master data show in English.
    Language("sq", "en-US", "shqip", lambda: S.LANGUAGE_ALBANIAN),
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


def ui_cultures() -> list[str]:
    """The product data languages of the user interface languages, each once."""
    return list(dict.fromkeys(language.culture for language in LANGUAGES))


@dataclass(frozen=True)
class ProductLanguage:
    culture: str  # the language of product data texts, e.g. "sk-SK"
    native: str  # the name in the language itself
    name: Callable[[], str]  # the name in the current language


# The languages product data comes in, the languages of the KNX master data: those of the user
# interface that have one of their own.
PRODUCT_LANGUAGES: tuple[ProductLanguage, ...] = tuple(
    ProductLanguage(lang.culture, lang.native, lang.name)
    for lang in LANGUAGES
    if lang.culture.split("-")[0] == lang.code
)

# The preferred product language chosen in the Language menu; None follows the UI language.
_product_language: str | None = None


def chosen_product_language() -> str | None:
    """The product language chosen in the menu, or None when it follows the UI language."""
    return _product_language


def product_language() -> str:
    """The preferred product language (e.g. "sk-SK"): the chosen one, else the UI language's."""
    return _product_language or culture(get_locale())


def set_product_language(code: str | None) -> None:
    """Prefer product texts in ``code`` ("sk-SK"); None follows the UI language."""
    global _product_language
    known = {lang.culture for lang in PRODUCT_LANGUAGES}
    _product_language = code if code in known else None


def product_text_languages() -> str:
    """The languages product texts are shown in, in order of preference and comma separated: the
    preferred product language, then the UI language ("sk-SK,cs-CZ"). A product that has neither
    shows its own default language."""
    first, second = product_language(), culture(get_locale())
    return first if first == second else f"{first},{second}"
