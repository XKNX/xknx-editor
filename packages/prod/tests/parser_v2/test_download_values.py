"""Which parameter values a download writes (DownloadInvisibleParameters)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from xknxeditor.namespaces.intermediate.application_program_static_t_options import (
    ApplicationProgramStaticOptions,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_parameter_refs import (
    ApplicationProgramStaticParameterRefs,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_union import (
    ApplicationProgramStaticParametersUnion,
)
from xknxeditor.namespaces.intermediate.download_behavior_t import DownloadBehavior
from xknxeditor.namespaces.intermediate.memory_parameter_t import MemoryParameter
from xknxeditor.namespaces.intermediate.memory_union_t import MemoryUnion
from xknxeditor.namespaces.intermediate.parameter_ref_t import ParameterRef
from xknxeditor.namespaces.intermediate.property_parameter_t import PropertyParameter
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI
from xknxeditor.prod.parser_v2.encode import (
    MaskedProperty,
    download_behavior,
    download_param_values,
    encode_to_memory,
    encode_to_typed_properties,
    encode_to_typed_properties_masked,
)
from xknxeditor.prod.parser_v2.state import GlobalState

from .test_encode_unit import _SEG_ID, _app, _param, _union_param


def _program(
    min_ets: str | None,
    behavior: DownloadBehavior | None,
):
    params = [
        _param(
            "P1",
            PropertyParameter(
                object_type=11, property_id=204, occurrence=0, offset=0, bit_offset=0
            ),
            value="10",
        ),
        _param(
            "P2",
            PropertyParameter(
                object_type=11, property_id=205, occurrence=0, offset=0, bit_offset=0
            ),
            value="3",
        ),
        _param("P3", None, value="5"),
        ApplicationProgramStaticParametersUnion(
            choice=MemoryUnion(code_segment=_SEG_ID, offset=0, bit_offset=0),
            size_in_bit=8,
            parameter=[_union_param("U1", 0, "1"), _union_param("U2", 0, "2", True)],
        ),
    ]
    app, _ = _app(params)
    app.min_ets_version = cast(Any, min_ets)
    app.static.options = (
        ApplicationProgramStaticOptions(download_invisible_parameters=behavior)
        if behavior is not None
        else None
    )
    app.static.parameter_refs = ApplicationProgramStaticParameterRefs(
        parameter_ref=[
            ParameterRef(id=f"{pid}_R", ref_id=pid) for pid in ("P1", "P2", "U1", "U2")
        ]
    )
    return app, ApplicationIndexer(app)


def _state(**active: str) -> GlobalState:
    """A state with the given ParameterRefs active, set to their values."""
    state = GlobalState()
    for ref, value in active.items():
        state.mark_active_param(ref)
        state.set(ref, value)
    return state


@pytest.mark.parametrize(
    ("min_ets", "behavior", "expected"),
    [
        ("5.0", None, DownloadBehavior.BACKGROUND),
        (
            "5.0",
            DownloadBehavior.DEFAULT_VALUE,
            DownloadBehavior.DEFAULT_VALUE,
        ),
        (
            "5.0",
            DownloadBehavior.NONE,
            DownloadBehavior.NONE,
        ),
        (
            "4.0",
            DownloadBehavior.DEFAULT_VALUE,
            DownloadBehavior.BACKGROUND,
        ),
        (None, DownloadBehavior.DEFAULT_VALUE, DownloadBehavior.BACKGROUND),
    ],
)
def test_download_behavior(min_ets, behavior, expected) -> None:
    app, _ = _program(min_ets, behavior)
    assert download_behavior(app) is expected


def test_default_value_writes_inactive_parameters_with_their_default() -> None:
    app, idx = _program("5.0", DownloadBehavior.DEFAULT_VALUE)
    values = download_param_values(app, idx, _state(P2_R="7"))
    # P3 has no ParameterRef; the union writes its default alternative.
    assert values == {"P1": "10", "P2": "7", "U2": "2"}
    assert encode_to_typed_properties(app, idx, values) == {
        (None, 11, 204, 0): b"\x0a",
        (None, 11, 205, 0): b"\x07",
    }


def test_an_active_union_alternative_suppresses_the_default() -> None:
    app, idx = _program("5.0", DownloadBehavior.DEFAULT_VALUE)
    values = download_param_values(app, idx, _state(U1_R="1"))
    assert "U2" not in values
    assert values["U1"] == "1"


@pytest.mark.parametrize("behavior", [DownloadBehavior.NONE, None])
def test_other_behaviors_write_only_active_parameters(behavior) -> None:
    app, idx = _program("5.0", behavior)
    assert download_param_values(app, idx, _state(P2_R="3")) == {"P2": "3"}


def test_inactive_defaults_are_written_after_active_values() -> None:
    app, _ = _app(
        [
            _param(
                "P1", MemoryParameter(code_segment=_SEG_ID, offset=0, bit_offset=0), "1"
            ),
            _param(
                "P2", MemoryParameter(code_segment=_SEG_ID, offset=0, bit_offset=0), "2"
            ),
        ]
    )
    app.min_ets_version = cast(Any, "5.0")
    app.static.options = ApplicationProgramStaticOptions()
    app.static.parameter_refs = ApplicationProgramStaticParameterRefs(
        parameter_ref=[ParameterRef(id=f"{pid}_R", ref_id=pid) for pid in ("P1", "P2")]
    )
    idx = ApplicationIndexer(app)
    state = _state(P2_R="2")
    values = download_param_values(app, idx, state)
    assert values == {"P1": "1", "P2": "2"}
    assert encode_to_memory(app, idx, values, state)[_SEG_ID][0] == 1


_INVISIBLE = (
    Path(__file__).parents[1] / "fixtures" / "download_invisible_application.xml"
).read_text(encoding="utf-8")


def _invisible_ui(behavior: str) -> DynamicUI:
    xml = _INVISIBLE.replace(
        'DownloadInvisibleParameters="DefaultValue"',
        f'DownloadInvisibleParameters="{behavior}"',
    )
    app = parse_application_xml(xml.encode(), "M-00FA")[0]
    ui = DynamicUI(app.program, tree_builder=DynamicTreeBuilder(app.program))
    ui.ui()
    return ui


@pytest.mark.parametrize(
    ("behavior", "data", "mask", "properties"),
    [
        # Active values first, then the defaults of inactive parameters, in the
        # application and in the module instance (base offset 4).
        (
            "DefaultValue",
            "01 02 13 14 05 06 17 18",
            "ff ff 00 00 ff ff 00 00",
            {(None, 11, 204, 0): b"\x07"},
        ),
        # Inactive ranges are written with the bytes the segment holds.
        (
            "Background",
            "01 12 13 14 05 16 17 18",
            "ff ff 00 00 ff ff 00 00",
            {(None, 11, 204, 0): b"\x00"},
        ),
        # Inactive ranges are not written.
        ("None", "01 12 13 14 05 16 17 18", "ff 00 00 00 ff 00 00 00", {}),
    ],
)
def test_inactive_parameters_follow_the_download_behavior(
    behavior: str, data: str, mask: str, properties: dict[Any, bytes]
) -> None:
    ui = _invisible_ui(behavior)
    ((encoded, written),) = ui.encode_to_memory_masked().values()
    assert encoded.hex(" ") == data
    assert written.hex(" ") == mask
    assert ui.encode_to_typed_properties() == properties


def test_masked_properties_cover_inactive_parameters_without_masking_them() -> None:
    app, idx = _program("5.0", None)
    state = _state(P2_R="7")
    values = download_param_values(app, idx, state)
    assert encode_to_typed_properties_masked(app, idx, values, state) == {
        # Inactive under Background: written with the property's base.
        (None, 11, 204, 0): MaskedProperty(b"\x00", b"\x00", b"\xff"),
        (None, 11, 205, 0): MaskedProperty(b"\x07", b"\xff", b"\xff"),
    }


@pytest.mark.parametrize(
    ("app_id", "bitwise"),
    [("M-009E_A-1000-01-0000", True), ("M-0083_A-0001-01-0000", False)],
)
def test_parameters_cover_whole_bytes_except_for_listed_applications(
    app_id: str, bitwise: bool
) -> None:
    app, idx = _app(
        [
            _param(
                "P1",
                PropertyParameter(
                    object_type=11,
                    property_id=204,
                    occurrence=0,
                    offset=0,
                    bit_offset=2,
                ),
                value="5",
            )
        ],
        pt_size=4,
    )
    app.id = app_id
    ((data, mask, covered),) = encode_to_typed_properties_masked(
        app, idx, {"P1": "5"}
    ).values()
    assert bin(mask[0]).count("1") == 4
    assert data[0] & ~mask[0] == 0
    assert covered == (mask if bitwise else b"\xff")
