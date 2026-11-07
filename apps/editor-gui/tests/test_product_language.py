"""The preferred product language: chosen separately, else the UI language's; product texts are
in it alone (a product without it shows its own default), and products download in it first."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from editor_gui.languages import (
    PRODUCT_LANGUAGES,
    chosen_product_language,
    default_locale,
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
    # Products without Slovak show their own default language, not the UI language: a new UI
    # language alone leaves the product texts as they are.
    assert product_text_languages() == "sk-SK"
    set_locale("de")
    assert product_text_languages() == "sk-SK"


def test_an_unknown_product_language_follows_the_ui_language() -> None:
    set_locale("de")
    set_product_language("xx-XX")
    assert chosen_product_language() is None
    assert product_language() == "de-DE"


def test_the_product_languages_are_those_of_the_knx_master_data() -> None:
    cultures = [lang.culture for lang in PRODUCT_LANGUAGES]
    assert len(cultures) == len(set(cultures)) == 24
    assert {"hu-HU", "ro-RO", "sk-SK", "sl-SI", "is-IS"} <= set(cultures)
    # Albanian is a UI language, but no product has texts in it.
    assert "sq-AL" not in cultures


def test_a_ui_language_without_a_knx_language_shows_products_in_english() -> None:
    set_locale("sq")
    set_product_language(None)
    assert product_language() == "en-US"
    assert product_text_languages() == "en-US"
    set_product_language("sk-SK")
    assert product_text_languages() == "sk-SK"


def test_the_product_language_menu_reads_alphabetically() -> None:
    from editor_gui.main import _product_language_items

    set_locale("de")
    labels = [label for _code, label in _product_language_items()]
    assert len(labels) == 24
    assert "Albanisch (shqip)" not in labels
    assert labels[:3] == [
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
    set_locale("sq")
    set_product_language(None)
    languages = service.online_languages
    assert languages[0] == "en-US"
    assert "sq-AL" not in languages
    assert len(languages) == len(set(languages))


@pytest.mark.parametrize(
    ("host_language", "expected", "product"),
    [
        # Home Assistant's language, with the product language that goes with it.
        ("de", "de", "de-DE"),
        ("pt-BR", "pt", "pt-PT"),
        ("zh-Hans", "zh", "zh-CN"),
        ("nb", "nb", "nb-NO"),
        # One the editor lacks: English, not the system's.
        ("ko", "en", "en-US"),
        # Started without one: the system's.
        (None, "cs", "cs-CZ"),
    ],
)
def test_the_language_of_the_program_that_started_the_editor(
    host_language: str | None, expected: str, product: str
) -> None:
    locale = default_locale(host_language, "cs")
    assert locale == expected
    set_locale(locale)
    set_product_language(None)
    assert product_language() == product
