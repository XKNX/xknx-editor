"""Regression tests for the vendor-mapped plugin-parameter viewer.

Covers mapping-file validity (every JSON parses, every regex compiles), module-instance refs
resolving via the template fallback, and the ``_K_``/``_Allg_`` Insta namespace landing in named
sections rather than the Other bucket.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest
from editor_gui.device import Device
from editor_gui.plugin_params import (
    _MAPPINGS_DIR,
    _enum_choices,
    _load_mapping,
    _module_text_args,
    _resolve_placeholders,
    collect_from_device,
    mapping_for,
)

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod import parse_application_xml
from xknxeditor.prod.application import Application


def test_every_mapping_file_loads() -> None:
    _load_mapping.cache_clear()
    assert _MAPPINGS_DIR.is_dir()
    files = sorted(_MAPPINGS_DIR.glob("*.json"))
    assert files, "no mapping files"
    for path in files:
        mapping = _load_mapping(str(path))
        assert mapping.app_id_prefixes or mapping.plugin_ids
        assert mapping.title
        # A mapping either groups the device's parameters (patterns) or only surfaces a vendor
        # blob (blob_reader); at least one must be present to be useful.
        assert mapping.patterns or mapping.blob_reader or mapping.provider, (
            f"{path.name} has neither section patterns nor a blob reader"
        )
        for pattern in mapping.patterns:
            # Ensure the label placeholders stay in sync with the capture groups.
            label = pattern.label
            captured_slots = {f"{{{i}}}" for i in range(1, 10)}
            for slot in captured_slots:
                if slot in label:
                    group_num = int(slot.strip("{}"))
                    assert group_num <= pattern.regex.groups, (
                        f"{path.name} pattern {pattern.regex.pattern!r} references {slot} "
                        f"but has only {pattern.regex.groups} groups"
                    )


def test_mapping_for_resolves_oem_sibling() -> None:
    _load_mapping.cache_clear()
    # The Insta platform mapping covers the Gira, Hager, STEINEL and Insta chassis under one JSON.
    m_gira = mapping_for("M-0008_A-C030-21-AA50-O000A")
    m_hager = mapping_for("M-0009_A-E80A-22-7594-O000A")
    assert m_gira is not None and m_hager is not None
    assert m_gira is m_hager


@dataclass
class _StubParam:
    id: str
    name: str
    value: str = ""
    text: str = ""
    access: Any = None
    parameter: Any = None


@dataclass
class _StubModuleDefs:
    module_def: list[Any] = field(default_factory=list)


MODULE_FIXTURE = Path(__file__).parent / "fixtures/dali_module_parameters.xml"
APP = "M-0083_A-0153-21-0000"


def module_device() -> Device:
    app = parse_application_xml(MODULE_FIXTURE.read_bytes(), "M-0083")[0]
    return Device(node_id=1, name="DALI", app=app, individual_address="1.1.1")


def test_all_module_defaults_and_nested_instances_are_listed() -> None:
    device = module_device()
    mapping = mapping_for(APP)
    assert mapping is not None and device.dynamic_ui is not None
    rows = {
        p.ref_id: p for s in collect_from_device(device, mapping) for p in s.parameters
    }
    assert set(rows) == set(device.dynamic_ui.parameter_ref_ids())
    assert len(rows) == 11
    for n in (1, 2):
        prefix = f"{APP}_MD-1_M-1_MI-{n}"
        mode = rows[f"{prefix}_P-1_R-1"]
        assert mode.value == mode.default_value == "8"
        assert mode.name == "DCA1-Mode"
        assert mode.label == f"[{n}] Group 3 Mode"
        assert mode.section == "DCA Channel 1"
        assert mode.writable
        assert not rows[f"{prefix}_P-1_R-2"].writable
        assert rows[f"{prefix}_P-1_R-2"].value == "12"
        assert not rows[f"{prefix}_P-3_R-4"].writable
        assert rows[f"{prefix}_P-2_R-3"].value == ""
        nested = [p for r, p in rows.items() if r.startswith(prefix + "_SM-")]
        assert len(nested) == 1 and nested[0].value == "2"
        assert nested[0].label.endswith("Input 7 Level")
    assert rows[f"{APP}_P-1_R-1"].value == ""
    assert device.parameter_instance_refs == []


def test_module_values_follow_live_edits_without_overwriting_other_instances() -> None:
    device = module_device()
    first = f"{APP}_MD-1_M-1_MI-1_P-1_R-1"
    second = f"{APP}_MD-1_M-1_MI-2_P-1_R-1"
    device.parameter_instance_refs = [ParameterInstanceRef(ref_id=first, value="9")]
    device.release_dynamic_ui()
    mapping = mapping_for(APP)
    assert mapping is not None
    rows = {
        p.ref_id: p for s in collect_from_device(device, mapping) for p in s.parameters
    }
    assert rows[first].value == "9"
    device.set_param_value(first, "10")
    rows = {
        p.ref_id: p for s in collect_from_device(device, mapping) for p in s.parameters
    }
    assert rows[first].value == "10"
    assert rows[first].default_value == "8"
    assert rows[second].value == "8"


def test_static_parameters_without_dynamic_tree_remain_available() -> None:
    device = module_device()
    assert device.app is not None
    app = device.app
    device = replace(
        device,
        app=Application(replace(app.program, dynamic=None), app.version, "M-0083"),
    )
    mapping = mapping_for(APP)
    assert mapping is not None
    rows = [p for s in collect_from_device(device, mapping) for p in s.parameters]
    assert len(rows) == 1
    assert rows[0].ref_id == f"{APP}_P-1_R-1"
    assert rows[0].default_value == rows[0].value == ""


def test_insta_k_prefix_lands_in_channel_section() -> None:
    _load_mapping.cache_clear()
    mapping = mapping_for("M-000A_A-C02F-22-BB9A")
    assert mapping is not None
    hit = mapping.match("_K_A_Bezeichnung")
    assert hit is not None
    label, _ = hit
    assert label == "Channel A"


def test_mapping_directory_lives_next_to_module() -> None:
    # Guard against accidental moves of the mapping bundle, which would silently disable every
    # Plugin Config tab (mapping_for() would return None for every device).
    assert _MAPPINGS_DIR.name == "plugin_mappings"
    assert _MAPPINGS_DIR.parent.name == "editor_gui"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])


@dataclass
class _StubEnumEntry:
    value: int
    text: str
    display_order: int | None = None


@dataclass
class _StubRestriction:
    enumeration: list[Any] = field(default_factory=list)


@dataclass
class _StubParameterType:
    id: str
    choice: Any = None


def test_enum_choices_resolves_label_pairs() -> None:
    pt = _StubParameterType(
        id="PT-ColourControlType",
        choice=_StubRestriction(
            enumeration=[
                _StubEnumEntry(value=0, text="none"),
                _StubEnumEntry(value=3, text="RGBW Colour"),
                _StubEnumEntry(value=1, text="Colour Temperature", display_order=1),
            ]
        ),
    )
    param = _StubParam(id="P-1", name="DCA1-CC-ColourControlType")
    param.parameter_type = pt.id  # type: ignore[attr-defined]
    choices = _enum_choices(param, {pt.id: pt})
    # value 3 maps to its label; the panel renders a dropdown seeded by the stored "3".
    assert ("3", "RGBW Colour") in choices
    assert ("0", "none") in choices


def test_enum_choices_empty_for_non_enum() -> None:
    pt = _StubParameterType(id="PT-Number", choice=_StubRestriction(enumeration=[]))
    param = _StubParam(id="P-2", name="DEV-Foo")
    param.parameter_type = pt.id  # type: ignore[attr-defined]
    assert _enum_choices(param, {pt.id: pt}) == ()
    # Unknown type id -> no choices, no crash.
    assert _enum_choices(param, {}) == ()


def test_resolve_placeholders_substitutes_and_strips() -> None:
    assert (
        _resolve_placeholders("Group {{GRP_NO}}, Description", {"GRP_NO": "3"})
        == "Group 3, Description"
    )
    # A placeholder with no argument is dropped rather than shown raw.
    assert "{{" not in _resolve_placeholders("Group {{GRP_NO}}, X", {})
    # No placeholder -> unchanged.
    assert _resolve_placeholders("Plain label", {}) == "Plain label"


@dataclass
class _StubArg:
    id: str
    name: str


@dataclass
class _StubArgs:
    argument: list[Any] = field(default_factory=list)


@dataclass
class _StubMdefWithArgs:
    arguments: Any = None


@dataclass
class _StubProgramArgs:
    module_defs: Any = None


class _StubDeviceArgs:
    def __init__(self, args: dict[str, list[tuple[str, str]]]) -> None:
        self._args = args

    def get_module_instance_arguments(self) -> dict[str, list[tuple[str, str]]]:
        return self._args


def test_module_text_args_maps_allocator_name_to_value() -> None:
    # The module def declares GRP_NO with its full app-prefixed id; the resolved instance args
    # come back keyed relative to the module ("MD-1_A-3"). _module_text_args must bridge both.
    program = _StubProgramArgs(
        module_defs=_StubModuleDefs(
            module_def=[
                _StubMdefWithArgs(
                    arguments=_StubArgs(
                        argument=[_StubArg(id="M-0083_A-0154_MD-1_A-3", name="GRP_NO")]
                    )
                )
            ]
        )
    )
    device = _StubDeviceArgs({"MD-1_M-2_MI-3": [("MD-1_A-3", "3")]})
    result = _module_text_args(cast(Any, device), program)
    assert result == {"MD-1_M-2_MI-3": {"GRP_NO": "3"}}


def test_module_text_args_empty_when_no_instances() -> None:
    device = _StubDeviceArgs({})
    assert _module_text_args(cast(Any, device), _StubProgramArgs()) == {}


@pytest.mark.parametrize(
    "app_id",
    [
        "M-0007_A-6323-01-0001",
        "M-0007_A-6325-01-0001",
        "M-0002_A-6322-01-0001",
        "M-0002_A-6323-01-0001",
        "M-0002_A-6325-01-0001",
    ],
)
def test_triton_mapping_matches_only_lcd_apps(app_id: str) -> None:
    from editor_gui.plugin_params import mapping_for

    from xknxeditor.vendor.triton import device_type_from_app_id

    mapping = mapping_for(app_id)
    assert mapping is not None and mapping.blob_reader == "triton_lcd"
    assert not mapping.patterns
    assert device_type_from_app_id(app_id) is not None


@pytest.mark.parametrize(
    "app_id",
    [
        "M-0007_A-6322-01-0001",
        "M-0007_A-63250-01-0001",
        "M-0007_A-6136-01-0001",
        "M-FFFF_A-6325-01-0001",
    ],
)
def test_triton_mapping_does_not_claim_other_apps(app_id: str) -> None:
    from editor_gui.plugin_params import mapping_for

    from xknxeditor.vendor.triton import device_type_from_app_id

    mapping = mapping_for(app_id)
    assert mapping is None or mapping.blob_reader != "triton_lcd"
    assert device_type_from_app_id(app_id) is None


def test_module_only_application_and_raw_mapping_names() -> None:
    source = MODULE_FIXTURE.read_text()
    source = source.replace('Name="DCA1-Mode"', 'Name="ch[{{ChannelNum}}].ecg[3].mode"')
    app = parse_application_xml(source.encode(), "M-0083")[0]
    assert app.program.static is not None
    program = replace(
        app.program,
        static=replace(app.program.static, parameters=None, parameter_refs=None),
    )
    device = Device(
        node_id=1,
        name="DALI",
        app=Application(program, app.version, "M-0083"),
        individual_address="1.1.1",
    )
    mapping = mapping_for("M-0071_A-3391-30-0000")
    assert mapping is not None
    rows = [p for s in collect_from_device(device, mapping) for p in s.parameters]
    assert len(rows) == 10
    mode = next(p for p in rows if p.ref_id.endswith("MI-1_P-1_R-1"))
    assert mode.section == "Channel - ECG 3"
    assert mode.label == "[1] Group 3 Mode"
    assert device.app is not None
    ref = device.app.tree_builder().idx.parameter_refs[f"{APP}_MD-1_P-1_R-1"]
    assert ref.text == "Group {{GRP_NO}} Mode"
