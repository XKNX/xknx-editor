"""The project code pages offered for selection, and their names."""

from __future__ import annotations

from editor_gui.plugins.project.strings import S
from xknxeditor.prod.parser_v2.text_encoding import PROJECT_CODE_PAGES

__all__ = ["PROJECT_CODE_PAGES", "code_page_text"]


def code_page_text(code_page: str | None) -> str:
    """The name of a project ``CodePage``; ``None`` is the system language. A value that is valid
    in a project file but not offered for selection (``us-ascii``, ``utf-8``) shows as it is."""
    names = {
        None: S.CODE_PAGE_SYSTEM,
        "iso-8859-1": S.CODE_PAGE_ISO_8859_1,
        "iso-8859-2": S.CODE_PAGE_ISO_8859_2,
        "iso-8859-3": S.CODE_PAGE_ISO_8859_3,
        "iso-8859-4": S.CODE_PAGE_ISO_8859_4,
        "iso-8859-5": S.CODE_PAGE_ISO_8859_5,
        "iso-8859-6": S.CODE_PAGE_ISO_8859_6,
        "iso-8859-7": S.CODE_PAGE_ISO_8859_7,
        "iso-8859-8": S.CODE_PAGE_ISO_8859_8,
        "iso-8859-9": S.CODE_PAGE_ISO_8859_9,
        "iso-8859-10": S.CODE_PAGE_ISO_8859_10,
        "iso-8859-13": S.CODE_PAGE_ISO_8859_13,
        "iso-8859-15": S.CODE_PAGE_ISO_8859_15,
    }
    return names.get(code_page) or (code_page or "").upper()
