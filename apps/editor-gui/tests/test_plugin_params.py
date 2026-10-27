"""Regression tests for the vendor-mapped plugin-parameter viewer.

Covers mapping-file validity (every JSON parses, every regex compiles), module-instance refs
resolving via the template fallback, and the ``_K_``/``_Allg_`` Insta namespace landing in named
sections rather than the Other bucket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from editor_gui.plugin_params import (
    _MAPPINGS_DIR,
    _enum_choices,
    _load_mapping,
    _module_text_args,
    _resolve_placeholders,
    collect_from_device,
    mapping_for,
)


def test_every_mapping_file_loads() -> None:
    _load_mapping.cache_clear()
    assert _MAPPINGS_DIR.is_dir()
    files = sorted(_MAPPINGS_DIR.glob("*.json"))
    assert files, "no mapping files"
    for path in files:
        mapping = _load_mapping(str(path))
        assert mapping.app_id_prefixes
        assert mapping.title
        assert mapping.patterns, f"{path.name} has no patterns"
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
class _StubChoiceContainer:
    choice: list[Any] = field(default_factory=list)


@dataclass
class _StubParamRefsContainer:
    parameter_ref: list[Any] = field(default_factory=list)


@dataclass
class _StubStatic:
    parameters: Any = None
    parameter_refs: Any = None


@dataclass
class _StubModuleDefs:
    module_def: list[Any] = field(default_factory=list)


@dataclass
class _StubModuleDef:
    static: Any = None


@dataclass
class _StubProgram:
    static: Any = None
    module_defs: Any = None


@dataclass
class _StubApp:
    program: Any = None
    id: str = ""


@dataclass
class _StubPIR:
    ref_id: str
    value: str


@dataclass
class _StubDevice:
    app: Any = None
    parameter_instance_refs: list[Any] = field(default_factory=list)
    individual_address: str = "1.1.1"


def test_module_instance_ref_resolves_via_template() -> None:
    _load_mapping.cache_clear()
    template = _StubParam(
        id="M-0083_A-0153-21-8829-O00EF_MD-1_P-7",
        name="ECG1-ModeON",
        value="0",
    )
    device = _StubDevice(
        app=_StubApp(
            id="M-0083_A-0153-21-8829-O00EF",
            program=_StubProgram(
                static=_StubStatic(
                    parameters=_StubChoiceContainer(choice=[]),
                    parameter_refs=_StubParamRefsContainer(parameter_ref=[]),
                ),
                module_defs=_StubModuleDefs(
                    module_def=[
                        _StubModuleDef(
                            static=_StubStatic(
                                parameters=_StubChoiceContainer(choice=[template]),
                            )
                        )
                    ]
                ),
            ),
        ),
        parameter_instance_refs=[
            _StubPIR(
                ref_id="M-0083_A-0153-21-8829-O00EF_MD-1_M-2_MI-5_P-7_R-11", value="1"
            ),
            _StubPIR(
                ref_id="M-0083_A-0153-21-8829-O00EF_MD-1_M-2_MI-6_P-7_R-11", value="2"
            ),
        ],
    )

    mapping = mapping_for("M-0083_A-0153-21-8829-O00EF")
    assert mapping is not None
    sections = collect_from_device(device, mapping)
    rows: list[Any] = []
    for s in sections:
        rows.extend(s.parameters)
    assert len(rows) == 2
    # The MI index is reflected in the display label so distinct instances are visible.
    labels = [r.label for r in rows]
    assert any("[5]" in lbl for lbl in labels), labels
    assert any("[6]" in lbl for lbl in labels), labels
    # Ref_id must still be the project's full instance ref so set_param writes the right slot.
    ref_ids = [r.ref_id for r in rows]
    assert all("_MI-" in r for r in ref_ids)


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
