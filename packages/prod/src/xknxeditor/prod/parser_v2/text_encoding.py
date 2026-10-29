"""The character encoding of a device's ``Text`` parameters, chosen the way the reference tool does.

The application's static ``Options`` decide (``TextParameterEncodingSelector``, default
``UseTextParameterEncodingCodePage``):

- ``UseTextParameterEncodingCodePage``: the application's ``TextParameterEncoding``, or the system
  code page when it names none;
- ``UseProjectCodePage``: the project's ``CodePage`` (``ProjectInformation/@CodePage``); a project
  without one uses the system code page ("system language");
- ``UseWindowsAnsiCodePage``: the system code page.

The system code page is the Windows ANSI code page of the system language (cp1252 for Western
European languages, cp1251 for Cyrillic ones, ...). It is derived from the language of the
operating system's locale, so every operating system picks the same code page for the same
language.
"""

from __future__ import annotations

import locale

from xknxeditor.namespaces.intermediate import ApplicationProgram
from xknxeditor.namespaces.intermediate.application_program_static_t_options_text_parameter_encoding_selector import (
    TextEncodingSelector,
)

# The project code pages offered for selection, in the reference tool's order; ``None`` is the
# system language. ``us-ascii`` and ``utf-8`` are valid in a project file too but not offered.
PROJECT_CODE_PAGES: tuple[str | None, ...] = (
    "iso-8859-1",
    "iso-8859-2",
    "iso-8859-3",
    "iso-8859-4",
    "iso-8859-5",
    "iso-8859-6",
    "iso-8859-7",
    "iso-8859-8",
    "iso-8859-9",
    "iso-8859-10",
    "iso-8859-13",
    "iso-8859-15",
    None,
)
# Every value ``ProjectInformation/@CodePage`` may hold (knx:TextEncoding_t).
VALID_PROJECT_CODE_PAGES: frozenset[str] = frozenset(
    {"us-ascii", "utf-8"} | {c for c in PROJECT_CODE_PAGES if c is not None}
)

_DEFAULT_ANSI = "cp1252"
# Windows ANSI code page by language (ISO 639-1), where it is not cp1252.
_ANSI_BY_LANGUAGE: dict[str, str] = {
    **dict.fromkeys(("cs", "hu", "pl", "sk", "sl", "hr", "ro", "sq", "bs"), "cp1250"),
    **dict.fromkeys(
        ("ru", "uk", "bg", "be", "mk", "sr", "kk", "ky", "tt", "mn"), "cp1251"
    ),
    "el": "cp1253",
    **dict.fromkeys(("tr", "az", "uz"), "cp1254"),
    "he": "cp1255",
    **dict.fromkeys(("ar", "fa", "ur"), "cp1256"),
    **dict.fromkeys(("et", "lv", "lt"), "cp1257"),
    "vi": "cp1258",
    "th": "cp874",
    "ja": "cp932",
    "ko": "cp949",
}
# Traditional Chinese regions; other Chinese locales use cp936 (Simplified).
_TRADITIONAL_CHINESE = frozenset({"TW", "HK", "MO"})


def ansi_code_page(language_tag: str) -> str:
    """The Windows ANSI code page of a language tag such as ``de_DE``, ``ru-RU`` or ``zh_TW``."""
    parts = language_tag.split(".")[0].replace("-", "_").split("_")
    language = parts[0].lower()
    if language == "zh":
        region = parts[1].upper() if len(parts) > 1 else ""
        return "cp950" if region in _TRADITIONAL_CHINESE else "cp936"
    return _ANSI_BY_LANGUAGE.get(language, _DEFAULT_ANSI)


def system_code_page() -> str:
    """The code page of the system language (see the module docstring)."""
    try:
        name = locale.getlocale(locale.LC_CTYPE)[0] or locale.getlocale()[0]
    except (ValueError, TypeError):
        name = None
    if not name:
        return _DEFAULT_ANSI
    return ansi_code_page(locale.normalize(name))


def project_text_encoding(code_page: str | None) -> str:
    """The codec name of a ``knx:TextEncoding_t`` value such as a project ``CodePage``; ``None`` (or
    an unknown value) is the system language."""
    if code_page is None or code_page.lower() not in VALID_PROJECT_CODE_PAGES:
        return system_code_page()
    return "ascii" if code_page.lower() == "us-ascii" else code_page.lower()


def text_parameter_encoding(
    app: ApplicationProgram, project_code_page: str | None = None
) -> str:
    """The codec name the application's ``Text`` parameters are encoded with, in a project with the
    ``CodePage`` ``project_code_page`` (``None``: the system language)."""
    options = app.static.options
    if options is None:
        return system_code_page()
    selector = options.text_parameter_encoding_selector
    if selector is TextEncodingSelector.USE_WINDOWS_ANSI_CODE_PAGE:
        return system_code_page()
    if selector is TextEncodingSelector.USE_PROJECT_CODE_PAGE:
        return project_text_encoding(project_code_page)
    if options.text_parameter_encoding is None:
        return system_code_page()
    return project_text_encoding(options.text_parameter_encoding.value)
