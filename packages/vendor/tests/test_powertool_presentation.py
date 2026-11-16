"""Presentation follows product language without changing stored configuration."""

import pytest

from xknxeditor.vendor.powertool.family import FamilyConfig, FamilyEvaluation, schemas
from xknxeditor.vendor.powertool.native import compile_native, default, from_original
from xknxeditor.vendor.powertool.presentation import Presentation
from xknxeditor.vendor.powertool.project_blob import read_project_blob
from xknxeditor.vendor.powertool.project_writer import write_project_blob


@pytest.mark.parametrize(
    ("language", "label"),
    [
        ("de-DE", "Schalten Wippe gesamt"),
        ("fr-FR", "Commutation du bouton-poussoir général"),
    ],
)
def test_original_resource_function_label(language, label):
    presentation = Presentation(3, language)
    assert presentation.text("App_SchaltenWippeTBA", "fallback") == label
    assert presentation.text("missing", "fallback") == "fallback"
    assert (
        presentation.text("App_SchaltenWippeTBA", "fallback", option=100) == "fallback"
    )
    assert (
        Presentation(3, "ja-JP").text("App_SchaltenWippeTBA", "fallback") == "fallback"
    )


def test_localized_saved_values_keep_image_inputs():
    config = default("01")
    original = write_project_blob(None, config)
    german = read_project_blob(original, "de-DE")
    english = read_project_blob(original)
    assert german.channels[0].application == "Schalten Wippe gesamt"
    assert german.channels[0].parameters[1].options == (
        "oben ein, unten aus",
        "oben aus, unten ein",
        "abwechselnd ein/aus",
    )
    assert (
        german.channels[0].parameters[1].value
        == english.channels[0].parameters[1].value
    )
    assert compile_native(from_original(original)).pid == compile_native(config).pid
    assert read_project_blob(original) is english


def test_family_localization_preserves_schema_and_values():
    for schema in schemas():
        profile = next(iter(schema["products"]))
        config = FamilyConfig(profile, database_code=schema["code"])
        english = FamilyEvaluation(config)
        german = FamilyEvaluation(config, language="de-DE")
        assert english.nodes.keys() == german.nodes.keys()
        for path, node in english.nodes.items():
            localized = german.nodes[path]
            if node.spec["kind"] == "parameter":
                assert english.stored(node) == german.stored(localized)
                assert english.display(node) == german.display(localized)
            for key, value in node.spec.items():
                if key not in ("label", "options", "subsystem_labels"):
                    assert localized.spec[key] == value
            if "options" in node.spec:
                assert [o["value"] for o in node.spec["options"]] == [
                    o["value"] for o in localized.spec["options"]
                ]
        assert english.schema is schema
        assert german.schema is not schema


def test_alternate_object_types_keep_distinct_labels():
    presentation = Presentation(3, "de-DE")
    whole = presentation.native_node("hvac_button", object_key="object_1")
    comfort = presentation.native_node("hvac_button", object_key="object_2")
    assert whole != comfort
    assert presentation.text(whole, "fallback") == "Betriebsmodus"
    assert presentation.text(comfort, "fallback") == "Betriebsmodus Komfort"


def test_shipped_presentation_covers_all_metadata_and_native_keys():
    import gzip
    import json
    from importlib.resources import files

    from xknxeditor.vendor.powertool.model import channel_functions, functions

    root = files("xknxeditor.vendor.powertool")
    presentation = json.loads(
        gzip.decompress(root.joinpath("presentation.json.gz").read_bytes())
    )
    for name in ("catalog.json.gz", "family-schema.json.gz"):
        for schema in json.loads(gzip.decompress(root.joinpath(name).read_bytes())):
            keys = presentation["databases"][str(schema["code"])]
            for key, node in schema["nodes"].items():
                assert isinstance(node["label"], str)
                description = presentation["descriptions"][
                    keys[key.removeprefix("BJE.toolB.ProductDatabase.")]
                ]
                if "options" in node:
                    assert len(description[1]) == len(node["options"])
                if "subsystem_labels" in node:
                    assert len(description[2]) == len(node["subsystem_labels"])
    native = Presentation(3, "de")
    keys = presentation["databases"]["3"]
    for function in functions().values():
        mapped = [native.native_node(function.key)]
        mapped.extend(native.native_node(function.key, f.key) for f in function.fields)
        mapped.extend(
            native.native_node(function.key, object_key=o.key) for o in function.objects
        )
        assert all(
            key.removeprefix("BJE.toolB.ProductDatabase.") in keys for key in mapped
        )
    for variant, count in (("01", 3), ("02", 4)):
        for channel in range(count):
            for function in channel_functions(variant, channel):
                assert native.native_node(function) in keys
