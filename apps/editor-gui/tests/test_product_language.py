"""The preferred product language: chosen separately, else the UI language's; product texts fall
back to the UI language, and products download in the preferred one first."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from editor_gui.languages import (
    PRODUCT_LANGUAGES,
    chosen_product_language,
    product_language,
    product_text_languages,
    set_product_language,
)
from editor_gui.strings import get_locale, set_locale


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    locale, product = get_locale(), chosen_product_language()
    yield
    set_locale(locale)
    set_product_language(product)


def test_the_product_language_follows_the_ui_language_until_one_is_chosen() -> None:
    set_locale("cs")
    set_product_language(None)
    assert product_language() == "cs-CZ"
    assert product_text_languages() == "cs-CZ"
    set_product_language("sk-SK")
    assert product_language() == "sk-SK"
    # Products without Slovak show Czech, the UI language, before their own default.
    assert product_text_languages() == "sk-SK,cs-CZ"


def test_an_unknown_product_language_follows_the_ui_language() -> None:
    set_locale("de")
    set_product_language("xx-XX")
    assert chosen_product_language() is None
    assert product_language() == "de-DE"


def test_the_ui_languages_and_five_more_are_product_languages() -> None:
    cultures = [lang.culture for lang in PRODUCT_LANGUAGES]
    assert len(cultures) == len(set(cultures)) == 25
    assert {"hu-HU", "ro-RO", "sk-SK", "sl-SI", "is-IS"} <= set(cultures)


def test_the_product_language_menu_reads_alphabetically() -> None:
    from editor_gui.main import _product_language_items

    set_locale("de")
    labels = [label for _code, label in _product_language_items()]
    assert len(labels) == 25
    assert labels[:4] == [
        "Albanisch (shqip)",
        "Chinesisch, vereinfacht (中文(中国))",
        "Dänisch (dansk)",
        "Deutsch",
    ]
    assert "Slowakisch (slovenčina)" in labels


def test_downloads_ask_for_the_preferred_product_language_first() -> None:
    from typing import Any

    from editor_gui.plugins.catalog.service import CatalogService

    service: Any = CatalogService.__new__(CatalogService)
    service._language = None
    set_locale("cs")
    set_product_language("sk-SK")
    languages = service.online_languages
    assert languages[:2] == ["sk-SK", "cs-CZ"]
    assert len(languages) == len(set(languages))
