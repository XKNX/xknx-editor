"""Tests for the KNX language-translation overlay (:mod:`xknxeditor.prod.translate`)."""

from pathlib import Path

from xknxeditor.namespaces import detect_version
from xknxeditor.namespaces.intermediate.language_data_t import LanguageData
from xknxeditor.prod.archive import Archive
from xknxeditor.prod.data import to_ir
from xknxeditor.prod.translate import (
    _index_by_id,
    _pick_language,
    apply_translations,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "gira_2gang_button_interface.knxprod"


def _lang(identifier: str) -> LanguageData:
    return LanguageData(identifier=identifier, translation_unit=[])


def test_pick_language_exact_and_prefix() -> None:
    langs = [_lang("en-US"), _lang("de-DE"), _lang("fr-FR")]
    # Exact identifier wins.
    assert _pick_language(langs, "de-DE") is langs[1]
    # A bare language code matches its region variant.
    assert _pick_language(langs, "de") is langs[1]
    assert _pick_language(langs, "en") is langs[0]
    # No match -> None (caller leaves the inline default text).
    assert _pick_language(langs, "it") is None


def test_apply_translations_overlays_texts() -> None:
    with Archive(str(_FIXTURE)) as archive:
        mid = sorted(archive.manufacturer_ids)[0]
        _app_id, xml = next(iter(archive.get_application_xmls(mid).items()))

    version = detect_version(xml)
    knx = to_ir(xml, version)
    manufacturer = knx.manufacturer_data.manufacturer[0]  # type: ignore[union-attr]

    # The fixture ships en-US; overlaying it must run cleanly and keep element texts populated.
    before = _index_by_id(manufacturer)
    apply_translations(knx, "en")
    after = _index_by_id(manufacturer)

    assert before.keys() == after.keys()
    assert any(getattr(obj, "text", None) for obj in after.values())


def test_apply_translations_unknown_language_is_noop() -> None:
    with Archive(str(_FIXTURE)) as archive:
        mid = sorted(archive.manufacturer_ids)[0]
        _app_id, xml = next(iter(archive.get_application_xmls(mid).items()))

    version = detect_version(xml)
    knx = to_ir(xml, version)
    texts_before = {
        k: getattr(v, "text", None)
        for k, v in _index_by_id(knx.manufacturer_data.manufacturer[0]).items()  # type: ignore[union-attr]
    }
    apply_translations(knx, "zz")  # no such language
    texts_after = {
        k: getattr(v, "text", None)
        for k, v in _index_by_id(knx.manufacturer_data.manufacturer[0]).items()  # type: ignore[union-attr]
    }
    assert texts_before == texts_after


def test_pick_language_follows_a_preference_list() -> None:
    langs = [_lang("en-US"), _lang("cs-CZ"), _lang("de")]
    # Slovak preferred, Czech next: the first one the product has.
    assert _pick_language(langs, "sk-SK,cs-CZ") is langs[1]
    assert _pick_language(langs, "sk-SK,it-IT") is None
    # A full identifier also finds the language without a region, or in another one.
    assert _pick_language(langs, "de-DE") is langs[2]
    assert _pick_language([_lang("es-MX")], "es-ES") is not None


def test_a_preferred_language_the_product_is_written_in_keeps_its_own_texts() -> None:
    langs = [_lang("de-DE"), _lang("fr-FR")]
    # Written in English, translated to German: English first means its own texts.
    assert _pick_language(langs, "en-US,de-DE", "en-US") is None
    # Slovak missing, English is the product's own language: its own texts again.
    assert _pick_language(langs, "sk-SK,en-US", "en-US") is None
    assert _pick_language(langs, "sk-SK,fr-FR", "en-US") is langs[1]
