"""Product and interface language apply independently to plugin parameters."""

import pytest

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


@pytest.mark.parametrize("language", ["en-US", "de-DE"])
def test_free_timer_values_and_labels_share_milliseconds(monkeypatch, language):
    from editor_gui.plugin_blob_readers.powertool import read_original
    from xknxeditor.vendor.powertool.family import FamilyEvaluation
    from xknxeditor.vendor.powertool.model import (
        ChannelConfig,
        PowerToolConfig,
        functions,
    )
    from xknxeditor.vendor.powertool.project_writer import write_project_blob

    monkeypatch.setattr(languages, "_product_language", language)
    for function in functions().values():
        if not any(f.kind == "freetimer" for f in function.fields):
            continue
        config = PowerToolConfig(
            "02",
            (ChannelConfig(function.key),) + (ChannelConfig("inactive"),) * 3,
        )
        fields = {f.key: f for f in function.fields if f.kind == "freetimer"}
        rows = {p.name: p for s in read(dump(config)) for p in s.parameters}
        for key, field in fields.items():
            if key in rows:
                assert rows[key].label.endswith("(ms)")
                assert rows[key].value == rows[key].default_value == str(field.default)
        original = write_project_blob(None, config)
        for section in read_original(original):
            for parameter in section.parameters:
                if "(ms)" in parameter.label:
                    code = int(parameter.raw_value)
                    assert int(parameter.value) == (code & 255) * (5 << (code >> 8))
    config = FamilyConfig("ObjektRTR")
    evaluated = FamilyEvaluation(config)
    rows = {p.name: p for s in read_family(config) for p in s.parameters}
    timers = [
        node
        for node in evaluated.nodes.values()
        if node.spec.get("value_kind") == "V_FREETIMER" and node.path in rows
    ]
    assert timers
    for node in timers:
        assert "(ms)" in rows[node.path].label
        assert rows[node.path].value == str(evaluated.display(node))


def test_repetition_timer_is_in_milliseconds(monkeypatch):
    from xknxeditor.vendor.powertool.native import edit_native

    monkeypatch.setattr(languages, "_product_language", "en-US")
    config = default("02")
    for field, value in (
        ("function", "dim_rocker"),
        ("manner_of_dimming", "1"),
        ("dimming_functionality", "1"),
        ("cyclical_sending_of_the_dimming_telegrams", "0"),
    ):
        config = edit_native(config, f"powertool:0:{field}", value)
    timer = next(
        p
        for s in read(dump(config))
        for p in s.parameters
        if p.name == "duration_of_the_telegram_repetition_s"
    )
    assert timer.label == "Duration of the telegram repetition (ms)"
    assert timer.value == "1000"
