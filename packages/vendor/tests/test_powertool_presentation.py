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
        ("de-DE", "Schalten mit Wippe"),
        ("en-US", "Rocker Switching"),
    ],
)
def test_own_function_label(language, label):
    presentation = Presentation(3, language)
    assert presentation.text("App_SchaltenWippeTBA", "fallback") == label
    assert presentation.text("missing", "fallback") == "fallback"
    assert (
        presentation.text("App_SchaltenWippeTBA", "fallback", option=100) == "fallback"
    )
    assert (
        Presentation(3, "xx").text("App_SchaltenWippeTBA", "fallback")
        == "Rocker Switching"
    )


def test_localized_saved_values_keep_image_inputs():
    config = default("01")
    original = write_project_blob(None, config)
    german = read_project_blob(original, "de-DE")
    english = read_project_blob(original)
    assert german.channels[0].application == "Schalten mit Wippe"
    assert german.channels[0].parameters[1].options == (
        "Oben ein, unten aus",
        "Oben aus, unten ein",
        "Umschalten",
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
                if key not in ("label", "options", "subsystem_labels", "default_text"):
                    assert localized.spec[key] == value
            if "options" in node.spec:
                assert [o["value"] for o in node.spec["options"]] == [
                    o["value"] for o in localized.spec["options"]
                ]
        assert english.schema is not schema
        assert german.schema is not schema


def test_alternate_object_types_keep_distinct_labels():
    presentation = Presentation(3, "de-DE")
    whole = presentation.native_node("hvac_button", object_key="object_1")
    comfort = presentation.native_node("hvac_button", object_key="object_2")
    assert whole != comfort
    assert (
        presentation.text(whole, "fallback")
        == "Betriebsartvorgabe der Raumtemperaturregelung"
    )
    assert presentation.text(comfort, "fallback") == "Komfortvorgabe"


def test_shipped_presentation_covers_all_metadata_and_native_keys():
    import json
    from importlib.resources import files

    from xknxeditor.vendor.powertool.metadata import native_nodes
    from xknxeditor.vendor.powertool.presentation import _text_map

    root = files("xknxeditor.vendor.powertool")
    assert not root.joinpath("catalog.json.gz").is_file()
    assert not root.joinpath("presentation.json.gz").is_file()
    assert not root.joinpath("family-schema.json.gz").is_file()
    mapping = _text_map()
    for schema in schemas():
        descriptions = mapping[str(schema["code"])]
        for key, node in schema["nodes"].items():
            assert "label" not in node and "subsystem_labels" not in node
            description = descriptions[key]
            assert (
                description["label"].strip()
                or (node["kind"] == "parameter" and not node["visible"])
                or (
                    node["kind"] == "application"
                    and not node["parameters"]
                    and not node["objects"]
                )
            )
            for kind in ("options", "subsystems"):
                assert len(description.get(kind, [])) == len(node.get(kind, []))
                assert all(text.strip() for text in description.get(kind, []))
            if (
                node.get("value_kind") in ("V_STRING", "V_STRINGPTR")
                and node["extension"] != "DatapointConfig"
                and node.get("default")
                and any(c.isalpha() for c in node["default"])
            ):
                assert description["default_text"] == node["default"]
            else:
                assert "default_text" not in description
    for spec in native_nodes().values():
        keys = [spec["node"], *spec["fields"].values(), *spec["objects"].values()]
        assert all(
            key.removeprefix("BJE.toolB.ProductDatabase.") in mapping["3"]
            for key in keys
        )
    native = json.loads(root.joinpath("schema.json").read_text(encoding="utf-8"))
    for function in native.values():
        assert "label" not in function
        assert all("label" not in field for field in function["fields"])
        assert all("label" not in obj for obj in function["objects"])


def test_text_map_has_sorted_nodes_on_separate_lines():
    import json
    from importlib.resources import files

    text = (
        files("xknxeditor.vendor.powertool")
        .joinpath("text-map.json")
        .read_text(encoding="utf-8")
    )
    mapping = json.loads(text)
    assert list(mapping) == sorted(mapping)
    lines = [line.strip().removesuffix(",") for line in text.splitlines()]
    node_lines = [line for line in lines if ": {" in line and line.endswith("}")]
    assert len(node_lines) == sum(len(nodes) for nodes in mapping.values())
    for line in node_lines:
        node = json.loads("{" + line + "}")
        spec = next(iter(node.values()))
        assert list(spec) == sorted(spec)
    for nodes in mapping.values():
        assert list(nodes) == sorted(nodes)


@pytest.mark.parametrize("value", [None, "Homepage", "My menu", ""])
def test_translated_text_default_keeps_saved_value_and_image(value):
    from dataclasses import replace

    from xknxeditor.vendor.powertool.family import from_saved

    config = FamilyConfig(
        "SmartTouch", database_code=20, program_ids=("0007610840", "0007610850")
    )
    state = FamilyEvaluation(config)
    home = next(
        n for n in state.nodes.values() if n.key == "App_HomePage1+Par_ControlName"
    )
    config = replace(config, values={home.path: value})
    image = compile_native(config).pid
    blob = write_project_blob(None, config)
    for language, expected in (("en", "Homepage"), ("de-DE", "Startseite")):
        localized = FamilyEvaluation(config, language=language)
        node = localized.nodes[home.path]
        assert node.spec["default"] == "Homepage"
        assert node.spec["default_text"] == expected
        assert localized.display(node) == ("Homepage" if value is None else value)
        saved = read_project_blob(blob, language)
        parameter = next(
            p for c in saved.channels for p in c.parameters if p.path == home.path
        )
        assert parameter.default_value == expected
        assert parameter.value == localized.stored(node)
    assert compile_native(config).pid == image
    restored = replace(from_saved(blob, qualified=True), program_ids=config.program_ids)
    assert compile_native(restored).pid == image


def test_hidden_text_default_is_translated_without_changing_encoding_metadata():
    schema = next(s for s in schemas() if s["code"] == 1)
    key = "GUI_ListeKreismenue+Par_OffsetListtext"
    translated = Presentation(1, "de-DE").schema(schema)
    assert translated["nodes"][key]["default_text"] == "Hauptmenü"
    assert translated["nodes"][key]["default"] == "Main menu"
    assert "default_text" not in schema["nodes"][key]


def test_free_timer_labels_cover_all_product_languages():
    from xknxeditor.vendor.powertool.presentation import free_timer_label

    for schema in schemas():
        presentation = Presentation(schema["code"])
        for key, node in schema["nodes"].items():
            if node.get("value_kind") == "V_FREETIMER":
                label = presentation.text(key, "")
                assert label.count("(ms)") == 1
                assert not any(
                    unit in label for unit in ("(s)", "(mm:ss)", "[mm:ss]", "(min)")
                )
                assert free_timer_label(label) == label


def test_saved_timer_display_keeps_raw_code_and_writeback():
    from xknxeditor.vendor.powertool.native import edit_native

    config = edit_native(default("02"), "powertool:0:function", "dim_rocker")
    blob = write_project_blob(None, config)
    for language in ("en", "de-DE"):
        saved = read_project_blob(blob, language)
        timer = next(p for p in saved.channels[0].parameters if p.kind == "V_FREETIMER")
        assert timer.value == 0x50
        assert timer.display_value == timer.default_value == 400
    assert compile_native(from_original(blob)).pid == compile_native(config).pid


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, 0),
        (127, 635),
        (0x177F, 5_326_766_080),
        (128, None),
        (0x1801, None),
        (-1, None),
    ],
)
def test_stored_timer_display_boundaries(code, expected):
    from xknxeditor.vendor.powertool.project_blob import _display_value

    assert _display_value(code, {"value_kind": "V_FREETIMER"}) == expected


def test_invalid_timer_keeps_raw_inspection_and_native_refusal():
    from xknxeditor.vendor.powertool.native import edit_native
    from xknxeditor.vendor.powertool.nrbf import Record, read_document, write_document

    config = edit_native(default("02"), "powertool:0:function", "dim_rocker")
    document = read_document(write_project_blob(None, config))
    timer = next(
        r
        for r in document.objects.values()
        if isinstance(r, Record) and r.type_name.endswith(".FreeTimerParameter")
    )
    timer.members["ParameterValue"] = 0x1801
    data = write_document(document)
    saved = read_project_blob(data)
    parameter = next(p for p in saved.channels[0].parameters if p.kind == "V_FREETIMER")
    assert parameter.value == 0x1801 and parameter.display_value is None
    with pytest.raises(ValueError, match="timer code"):
        from_original(data)
