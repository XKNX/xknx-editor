"""Product and interface language apply independently to plugin parameters."""

from editor_gui import languages, strings
from editor_gui.plugin_blob_readers.powertool import read, read_family
from editor_gui.plugin_providers.powertool import PROVIDER
from xknxeditor.vendor.powertool.family import FamilyConfig
from xknxeditor.vendor.powertool.native import default, dump

from .test_powertool import current, native_project


def test_product_language_and_interface_language(monkeypatch):
    monkeypatch.setattr(languages, "_product_language", "de-DE")
    monkeypatch.setattr(strings, "_current_locale", "de")
    config = dump(default("02"))
    german = read(config)
    assert german[0].section == "1: Oben / Wippe"
    assert german[0].parameters[0].label == "Funktion"
    assert german[0].parameters[0].choices[0][1] == "Schalten Wippe gesamt"
    assert german[0].parameters[1].label == "Arbeitsweise der Wippe für Schalten"
    monkeypatch.setattr(strings, "_current_locale", "en")
    mixed = read(config)
    assert mixed[0].section == "1: Upper / Rocker"
    assert mixed[0].parameters[0].choices == german[0].parameters[0].choices
    monkeypatch.setattr(languages, "_product_language", "en-US")
    english = read(config)
    assert english[0].parameters[0].choices[0][1] == "Switching rocker switch total"
    for a, b in zip(german, english, strict=True):
        assert [(p.ref_id, p.value) for p in a.parameters] == [
            (p.ref_id, p.value) for p in b.parameters
        ]


def test_family_uses_product_language(monkeypatch):
    config = FamilyConfig("ObjektRTR")
    monkeypatch.setattr(languages, "_product_language", "de-DE")
    german = read_family(config)
    monkeypatch.setattr(languages, "_product_language", "en-US")
    english = read_family(config)
    assert [(s.section, [p.label for p in s.parameters]) for s in german] != [
        (s.section, [p.label for p in s.parameters]) for s in english
    ]
    assert [(p.ref_id, p.value) for s in german for p in s.parameters] == [
        (p.ref_id, p.value) for s in english for p in s.parameters
    ]


def test_language_change_invalidates_plugin_cache_and_localizes_objects(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(languages, "_product_language", "de-DE")
    monkeypatch.setattr(strings, "_current_locale", "de")
    project, nid = native_project(tmp_path, monkeypatch)
    project.setup_plugin(current(project, nid))
    device = current(project, nid)
    german = project._plugin_read("test", device, lambda: PROVIDER.sections(device))
    assert german[0].section == "1: Oben / Wippe"
    assert device.get_visible_com_objects()[0].name == "Schalten"
    before = project.cursor
    monkeypatch.setattr(strings, "_current_locale", "en")
    mixed = project._plugin_read("test", device, lambda: PROVIDER.sections(device))
    assert mixed[0].section == "1: Upper / Rocker"
    assert mixed[0].parameters[1].label == german[0].parameters[1].label
    assert project.cursor == before
    project.close()
