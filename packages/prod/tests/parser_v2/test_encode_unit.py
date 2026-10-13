"""Unit tests for collect_writes / encode_to_memory / encode_to_properties.

All tests build minimal ApplicationProgram IR structures in-process — no fixture files.
"""

from __future__ import annotations

from xknxeditor.namespaces.intermediate import ApplicationProgram
from xknxeditor.namespaces.intermediate.application_program_static_t import (
    ApplicationProgramStatic,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_code import (
    ApplicationProgramStaticCode,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_code_absolute_segment import (
    ApplicationProgramStaticCodeAbsoluteSegment,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_parameter_types import (
    ApplicationProgramStaticParameterTypes,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_parameters import (
    ApplicationProgramStaticParameters,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_parameter import (
    ApplicationProgramStaticParametersParameter,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_union import (
    ApplicationProgramStaticParametersUnion,
)
from xknxeditor.namespaces.intermediate.application_program_type_t import (
    ApplicationProgramType,
)
from xknxeditor.namespaces.intermediate.io_tpoint_parameter_t import IoPointParameter
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.memory_parameter_t import MemoryParameter
from xknxeditor.namespaces.intermediate.memory_union_t import MemoryUnion
from xknxeditor.namespaces.intermediate.parameter_type_t import ParameterType
from xknxeditor.namespaces.intermediate.parameter_type_t_type_number import (
    ParameterTypeTypeNumber,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_number_type import (
    ParameterTypeTypeNumberType,
)
from xknxeditor.namespaces.intermediate.property_parameter_t import PropertyParameter
from xknxeditor.namespaces.intermediate.property_union_t import PropertyUnion
from xknxeditor.namespaces.intermediate.union_parameter_t import UnionParameter
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.prod.parser_v2.encode import (
    Writes,
    collect_writes,
    encode_to_memory,
    encode_to_properties,
    encode_to_typed_properties,
    written_bit_mask,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PT_ID = "PT1"
_SEG_ID = "SEG1"


def _num_type(size_in_bit: int = 8) -> ParameterTypeTypeNumber:
    return ParameterTypeTypeNumber(
        size_in_bit=size_in_bit,
        type_value=ParameterTypeTypeNumberType.UNSIGNED_INT,
        min_inclusive=0,
        max_inclusive=(1 << size_in_bit) - 1,
    )


def _param_type(size_in_bit: int = 8) -> ParameterType:
    return ParameterType(id=_PT_ID, name="T", choice=_num_type(size_in_bit))


def _segment(
    size: int = 8, data: bytes | None = None
) -> ApplicationProgramStaticCodeAbsoluteSegment:
    return ApplicationProgramStaticCodeAbsoluteSegment(
        id=_SEG_ID, size=size, address=0, data=data
    )


def _param(
    param_id: str,
    choice: MemoryParameter | PropertyParameter | IoPointParameter | None,
    value: str = "0",
) -> ApplicationProgramStaticParametersParameter:
    return ApplicationProgramStaticParametersParameter(
        id=param_id, name="", text="", parameter_type=_PT_ID, value=value, choice=choice
    )


def _union_param(
    param_id: str, offset: int, value: str, default: bool = False
) -> UnionParameter:
    return UnionParameter(
        id=param_id,
        name="",
        text="",
        parameter_type=_PT_ID,
        value=value,
        offset=offset,
        bit_offset=0,
        default_union_parameter=default,
    )


def _app(
    params: list[
        ApplicationProgramStaticParametersParameter
        | ApplicationProgramStaticParametersUnion
    ],
    seg_size: int = 8,
    seg_data: bytes | None = None,
    pt_size: int = 8,
) -> tuple[ApplicationProgram, ApplicationIndexer]:
    app = ApplicationProgram(
        id="APP",
        name="",
        application_number=1,
        application_version=1,
        program_type=ApplicationProgramType.APPLICATION_PROGRAM,
        mask_version="BV20",
        load_procedure_style=LoadProcedureStyle.DEFAULT_PROCEDURE,
        pei_type=0,
        default_language="en",
        dynamic_table_management=False,
        linkable=False,
        static=ApplicationProgramStatic(
            code=ApplicationProgramStaticCode(
                absolute_segment=[_segment(seg_size, seg_data)]
            ),
            parameter_types=ApplicationProgramStaticParameterTypes(
                parameter_type=[_param_type(pt_size)]
            ),
            parameters=ApplicationProgramStaticParameters(choice=params),
        ),
    )
    return app, ApplicationIndexer(app)


# ---------------------------------------------------------------------------
# collect_writes — memory parameter
# ---------------------------------------------------------------------------


def test_collect_mem_param() -> None:
    p = _param(
        "P1", MemoryParameter(code_segment=_SEG_ID, offset=2, bit_offset=0), value="42"
    )
    app, idx = _app([p])
    w = collect_writes(app, idx, {})
    assert len(w.mem) == 1
    assert len(w.prop) == 0
    assert w.mem[0].seg_id == _SEG_ID
    assert w.mem[0].offset == 2
    assert w.mem[0].value == "42"
    assert w.mem[0].param_id == "P1"


def test_collect_mem_param_override() -> None:
    p = _param("P1", MemoryParameter(code_segment=_SEG_ID, offset=0, bit_offset=0))
    app, idx = _app([p])
    w = collect_writes(app, idx, {"P1": "7"})
    assert w.mem[0].value == "7"


# ---------------------------------------------------------------------------
# collect_writes — property parameter
# ---------------------------------------------------------------------------


def test_collect_prop_param() -> None:
    p = _param(
        "P1",
        PropertyParameter(
            object_index=1, property_id=53, occurrence=0, offset=0, bit_offset=0
        ),
        value="5",
    )
    app, idx = _app([p])
    w = collect_writes(app, idx, {})
    assert len(w.mem) == 0
    assert len(w.prop) == 1
    pw = w.prop[0]
    assert pw.object_index == 1
    assert pw.property_id == 53
    assert pw.occurrence == 0
    assert pw.value == "5"


# ---------------------------------------------------------------------------
# collect_writes — memory union
# ---------------------------------------------------------------------------


def test_collect_mem_union_default() -> None:
    union = ApplicationProgramStaticParametersUnion(
        choice=MemoryUnion(code_segment=_SEG_ID, offset=0, bit_offset=0),
        size_in_bit=8,
        parameter=[
            _union_param("U1", 0, "10", default=True),
            _union_param("U2", 0, "20"),
        ],
    )
    app, idx = _app([union])
    w = collect_writes(app, idx, {})
    assert len(w.mem) == 1
    assert w.mem[0].param_id == "U1"
    assert w.mem[0].value == "10"


def test_collect_mem_union_active_override() -> None:
    union = ApplicationProgramStaticParametersUnion(
        choice=MemoryUnion(code_segment=_SEG_ID, offset=0, bit_offset=0),
        size_in_bit=8,
        parameter=[
            _union_param("U1", 0, "10", default=True),
            _union_param("U2", 0, "20"),
        ],
    )
    app, idx = _app([union])
    w = collect_writes(app, idx, {"U2": "99"})
    assert w.mem[0].param_id == "U2"
    assert w.mem[0].value == "99"


# ---------------------------------------------------------------------------
# collect_writes — property union
# ---------------------------------------------------------------------------


def test_collect_prop_union_default() -> None:
    union = ApplicationProgramStaticParametersUnion(
        choice=PropertyUnion(
            object_index=0, property_id=10, occurrence=0, offset=0, bit_offset=0
        ),
        size_in_bit=8,
        parameter=[
            _union_param("U1", 0, "3", default=True),
            _union_param("U2", 0, "4"),
        ],
    )
    app, idx = _app([union])
    w = collect_writes(app, idx, {})
    assert len(w.prop) == 1
    assert w.prop[0].param_id == "U1"
    assert w.prop[0].property_id == 10


# ---------------------------------------------------------------------------
# encode_to_memory
# ---------------------------------------------------------------------------


def test_encode_to_memory_writes_value() -> None:
    app, idx = _app(
        [_param("P1", MemoryParameter(code_segment=_SEG_ID, offset=3, bit_offset=0))]
    )
    mem = encode_to_memory(app, idx, {"P1": "255"})
    assert mem[_SEG_ID][3] == 255


def test_encode_to_memory_default_value() -> None:
    app, idx = _app(
        [
            _param(
                "P1",
                MemoryParameter(code_segment=_SEG_ID, offset=0, bit_offset=0),
                value="42",
            )
        ]
    )
    mem = encode_to_memory(app, idx, {})
    assert mem[_SEG_ID][0] == 42


def test_encode_to_memory_seeded_from_seg_data() -> None:
    p = _param("P1", MemoryParameter(code_segment=_SEG_ID, offset=1, bit_offset=0))
    app, idx = _app([p], seg_data=b"\xff\x00\xff\xff")
    mem = encode_to_memory(app, idx, {"P1": "7"})
    assert mem[_SEG_ID][0] == 0xFF  # seeded from data
    assert mem[_SEG_ID][1] == 7  # overwritten by param
    assert mem[_SEG_ID][2] == 0xFF  # seeded from data


def test_encode_to_memory_sub_byte() -> None:
    # 4-bit param at bit_offset=4 of byte 0 — value 3 should land in lower nibble
    app, idx = _app(
        [_param("P1", MemoryParameter(code_segment=_SEG_ID, offset=0, bit_offset=4))],
        pt_size=4,
    )
    mem = encode_to_memory(app, idx, {"P1": "3"})
    assert mem[_SEG_ID][0] == 0x03  # bits 4-7 = 0b0011


# ---------------------------------------------------------------------------
# encode_to_properties
# ---------------------------------------------------------------------------


def test_encode_to_properties_basic() -> None:
    p = _param(
        "P1",
        PropertyParameter(
            object_index=2, property_id=57, occurrence=0, offset=0, bit_offset=0
        ),
    )
    app, idx = _app([p])
    props = encode_to_properties(app, idx, {"P1": "200"})
    assert props[(2, 57, 0)][0] == 200


def test_encode_to_properties_default_value() -> None:
    p = _param(
        "P1",
        PropertyParameter(
            object_index=0, property_id=10, occurrence=0, offset=0, bit_offset=0
        ),
        value="99",
    )
    app, idx = _app([p])
    props = encode_to_properties(app, idx, {})
    assert props[(0, 10, 0)][0] == 99


def test_encode_to_typed_properties_keeps_object_types_apart() -> None:
    app, idx = _app(
        [
            _param(
                "P1",
                PropertyParameter(
                    object_type=11,
                    property_id=205,
                    occurrence=0,
                    offset=0,
                    bit_offset=0,
                ),
            ),
            _param(
                "P2",
                PropertyParameter(
                    object_type=50001,
                    property_id=205,
                    occurrence=0,
                    offset=0,
                    bit_offset=0,
                ),
            ),
        ]
    )
    props = encode_to_typed_properties(app, idx, {"P1": "7", "P2": "9"})
    assert props == {(None, 11, 205, 0): b"", (None, 50001, 205, 0): b"	"}


def test_encode_to_properties_empty_when_no_prop_params() -> None:
    app, idx = _app(
        [
            _param(
                "P1",
                MemoryParameter(code_segment=_SEG_ID, offset=0, bit_offset=0),
                value="1",
            )
        ]
    )
    assert encode_to_properties(app, idx, {}) == {}


def test_encode_to_memory_empty_when_no_mem_params() -> None:
    p = _param(
        "P1",
        PropertyParameter(
            object_index=0, property_id=10, occurrence=0, offset=0, bit_offset=0
        ),
        value="1",
    )
    app, idx = _app([p])
    mem = encode_to_memory(app, idx, {})
    assert all(b == 0 for b in mem[_SEG_ID])


def test_encode_to_properties_multiple_params_same_property() -> None:
    # Two params writing to the same property at different offsets
    p1 = _param(
        "P1",
        PropertyParameter(
            object_index=0, property_id=5, occurrence=0, offset=0, bit_offset=0
        ),
    )
    p2 = _param(
        "P2",
        PropertyParameter(
            object_index=0, property_id=5, occurrence=0, offset=1, bit_offset=0
        ),
    )
    app, idx = _app([p1, p2])
    props = encode_to_properties(app, idx, {"P1": "11", "P2": "22"})
    key = (0, 5, 0)
    assert props[key][0] == 11
    assert props[key][1] == 22


# ---------------------------------------------------------------------------
# Writes container
# ---------------------------------------------------------------------------


def test_writes_starts_empty() -> None:
    w = Writes()
    assert w.mem == []
    assert w.prop == []


# ---------------------------------------------------------------------------
# written_bit_mask — bit-granular parameter-driven mask
# ---------------------------------------------------------------------------


def test_written_bit_mask_marks_only_driven_bits() -> None:
    # Two 1-bit parameters share byte 0 at bit_offset 1 and 3 (MSB-first). The mask
    # must mark exactly those bits (0x40 | 0x10 = 0x50) and leave the rest untouched,
    # so a pre-flight can tell a driven bit apart from a neighbouring seed bit.
    p1 = _param("P1", MemoryParameter(code_segment=_SEG_ID, offset=0, bit_offset=1))
    p2 = _param("P2", MemoryParameter(code_segment=_SEG_ID, offset=0, bit_offset=3))
    app, idx = _app([p1, p2], pt_size=1)
    mask = written_bit_mask(app, idx, {})
    assert mask[_SEG_ID][0] == 0x50
    assert all(b == 0 for b in mask[_SEG_ID][1:])


def test_written_bit_mask_multi_byte_field() -> None:
    # A 16-bit parameter at offset 2 drives both of its bytes fully.
    p = _param("P1", MemoryParameter(code_segment=_SEG_ID, offset=2, bit_offset=0))
    app, idx = _app([p], seg_size=8, pt_size=16)
    mask = written_bit_mask(app, idx, {})
    assert mask[_SEG_ID][2] == 0xFF
    assert mask[_SEG_ID][3] == 0xFF
    assert mask[_SEG_ID][0] == mask[_SEG_ID][1] == 0x00
