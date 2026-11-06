"""A new interface language asks whether the product texts change with it, only where they would,
and the devices are built again only when their texts change."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui import languages
from editor_gui.main import KnxGuiApp
from editor_gui.strings import S, get_locale, set_locale


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    before, product = get_locale(), languages.chosen_product_language()
    set_locale("en")
    languages.set_product_language(None)
    yield
    set_locale(before)
    languages.set_product_language(product)


def _app() -> Any:
    app: Any = SimpleNamespace(_language_question=None, switched=[], rebuilt=[])
    app._switch_language = lambda code, product: app.switched.append((code, product))
    return app


def test_the_same_language_changes_nothing() -> None:
    app = _app()
    KnxGuiApp._choose_language(app, "en")  # pyright: ignore[reportPrivateUsage]
    assert app.switched == [] and app._language_question is None


def test_a_language_with_its_own_product_texts_asks() -> None:
    app = _app()
    KnxGuiApp._choose_language(app, "de")  # pyright: ignore[reportPrivateUsage]
    assert app._language_question == "de" and app.switched == []


def test_through_mcp_the_product_language_stays() -> None:
    app = _app()
    KnxGuiApp._choose_language(app, "de", ask=False)  # pyright: ignore[reportPrivateUsage]
    assert app.switched == [("de", "en-US")]


def test_nothing_to_ask_when_the_product_texts_are_already_in_it() -> None:
    languages.set_product_language("de-DE")
    app = _app()
    KnxGuiApp._choose_language(app, "de")  # pyright: ignore[reportPrivateUsage]
    assert app.switched == [("de", "de-DE")]


def _switcher() -> Any:
    app: Any = SimpleNamespace(rebuilt=[])
    app._apply_language = lambda code, product: (
        set_locale(code),
        languages.set_product_language(product),
    )
    app._rebuild_in_language = lambda undo: app.rebuilt.append(undo)
    return app


def test_the_devices_are_built_again_only_when_their_texts_change() -> None:
    languages.set_product_language("de-DE")
    set_locale("de")
    app = _switcher()
    # German product texts in a German interface stay German: nothing to build again.
    KnxGuiApp._switch_language(app, "de", "de-DE")  # pyright: ignore[reportPrivateUsage]
    assert app.rebuilt == []
    # A new product language changes them.
    KnxGuiApp._switch_language(app, "de", None)  # pyright: ignore[reportPrivateUsage]
    assert app.rebuilt == []
    KnxGuiApp._switch_language(app, "fr", None)  # pyright: ignore[reportPrivateUsage]
    assert len(app.rebuilt) == 1
    assert languages.product_language() == "fr-FR"


def test_the_palette_offers_the_other_languages() -> None:
    chosen: list[str] = []
    products: list[str] = []
    app: Any = SimpleNamespace(
        _choose_language=chosen.append, _switch_product_language=products.append
    )
    items = KnxGuiApp._palette_languages(app)  # pyright: ignore[reportPrivateUsage]
    ui = [i for i in items if i.where == S.SETTING_LANGUAGE]
    product = [i for i in items if i.where == S.SETTING_PRODUCT_LANGUAGE]
    # Every language but the current one, of both kinds.
    assert len(ui) == len(languages.LANGUAGES) - 1
    assert len(product) == len(languages.PRODUCT_LANGUAGES) - 1
    german = next(i for i in ui if "Deutsch" in i.label)
    german.action()
    assert chosen == ["de"]
    next(i for i in product if "Deutsch" in i.label).action()
    assert products == ["de-DE"]
