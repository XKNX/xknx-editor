"""Whole-image originals and validation of shared family configurations."""

import json
from dataclasses import replace

import pytest

from packages.vendor.tests.powertool_fixtures import archive_cases, open_archive
from xknxeditor.vendor.powertool.family import FamilyEvaluation, edit, from_saved
from xknxeditor.vendor.powertool.family_codec import compile_family
from xknxeditor.vendor.powertool.native import dump, load

CASES = [r["case"] for r in archive_cases("family-images.zip")]
SEQUENCES = [r["case"] for r in archive_cases("family-sequences.zip")]


@pytest.mark.parametrize("case", CASES)
def test_family_original_pid(case):
    with open_archive("family-images.zip") as archive:
        config = load(archive.read(case + "/config.json"))
        assert compile_family(config).pid == archive.read(case + "/image.pid")
        assert load(dump(config)) == config


@pytest.mark.parametrize("case", SEQUENCES)
def test_original_family_sequence(case):
    with open_archive("family-sequences.zip") as archive:
        config = load(archive.read(case + "/config.json"))
        assert compile_family(config).pid == archive.read(case + "/image.pid")


def saved_config(case="DI-12"):
    with open_archive("host-projects.zip") as archive:
        return from_saved(archive.read(case + ".blob"))


@pytest.mark.parametrize(
    "change",
    [
        {"values": {"missing": 1}},
        {"options": {"missing": 0}},
        {"family_profile": "unknown"},
        {"values": []},
        {"bindings": {"one": "same", "two": "same"}},
        {"unexpected": 1},
    ],
)
def test_invalid_family_import(change):
    data = json.loads(dump(saved_config()))
    data.update(change)
    with pytest.raises(ValueError):
        load(json.dumps(data).encode())


def test_hidden_fields_and_unknown_flags_are_rejected():
    config = saved_config()
    state = FamilyEvaluation(config)
    hidden = next(
        n
        for n in state.nodes.values()
        if n.spec["kind"] == "parameter" and not state.property(n, "Visible")
    )
    with pytest.raises(ValueError, match=r"editable|unavailable"):
        edit(config, hidden.path, "1")
    with pytest.raises(ValueError, match="absent object"):
        compile_family(config, flags={"missing": 4})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1e100, "wrong"])
def test_invalid_numeric_values(value):
    config = saved_config("DI-3")
    state = FamilyEvaluation(config)
    node = next(
        n for n in state.nodes.values() if n.spec.get("value_kind") == "V_FLOAT"
    )
    with pytest.raises(ValueError):
        load(dump(replace(config, values={**config.values, node.path: value})))


def test_noop_function_keeps_values():
    config = saved_config()
    state = FamilyEvaluation(config)
    channel = next(c for c in state.channels if state.active(c))
    assert edit(config, channel.path, str(config.options[channel.path])) == config
