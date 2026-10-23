"""Regression tests for the vendor-mapped plugin-parameter viewer.

Covers mapping-file validity (every JSON parses, every regex compiles), module-instance refs
resolving via the template fallback, and the ``_K_``/``_Allg_`` Insta namespace landing in named
sections rather than the Other bucket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from editor_gui.plugin_params import (
    _MAPPINGS_DIR,
    _load_mapping,
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
