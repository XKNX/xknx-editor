"""The application's identity in the image where the mask keeps it in memory, and code
segments in the device's user memory."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import cast

import pytest

from xknxeditor.download.image import DownloadImage, build_image
from xknxeditor.namespaces.intermediate.application_program_static_t_code_absolute_segment import (
    ApplicationProgramStaticCodeAbsoluteSegment,
)
from xknxeditor.prod import Application, load
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .test_wire_audit import _FIXTURE


def _image(
    mask_version: str,
    segments: list[tuple[int, int, bool]],
    *,
    manufacturer: str = "M-00FA",
    parameter: tuple[int, int] | None = None,
) -> tuple[DownloadImage, Application]:
    """The image of ``segments`` (address, size, user memory), zero data, a parameter at
    ``(segment index, offset)``."""
    original = next(iter(load(_FIXTURE).applications.values()))
    program = deepcopy(original.program)
    assert program.static.code is not None
    program.static.code.absolute_segment = [
        ApplicationProgramStaticCodeAbsoluteSegment(
            id=f"s{n}", size=size, address=address, user_memory=user
        )
        for n, (address, size, user) in enumerate(segments)
    ]
    program.static.code.relative_segment = []
    program.mask_version = mask_version
    program.application_number = 0xA00E
    program.application_version = 0x01
    program.pei_type = 0x14

    def encoded() -> dict[str, tuple[bytes, bytes]]:
        out: dict[str, tuple[bytes, bytes]] = {}
        for n, (_address, size, _user) in enumerate(segments):
            data, mask = bytearray(size), bytearray(size)
            if parameter is not None and parameter[0] == n:
                data[parameter[1]], mask[parameter[1]] = 0x3B, 0xFF
            out[f"s{n}"] = (bytes(data), bytes(mask))
        return out

    app = cast(
        Application,
        SimpleNamespace(
            program=program, manufacturer_id=manufacturer, load_procedures=None
        ),
    )
    ui = cast(
        DynamicUI,
        SimpleNamespace(
            segment_base_addrs=lambda: {
                f"s{n}": address for n, (address, _s, _u) in enumerate(segments)
            },
            encode_to_memory_masked=encoded,
            encode_to_typed_properties_masked=lambda: {},
        ),
    )
    return build_image(app, ui=ui), app


@pytest.mark.parametrize("mask_version", ["MV-0010", "MV-0011", "MV-0012", "MV-1013"])
def test_bcu1_header_carries_the_application_id_and_pei_type(mask_version: str) -> None:
    image, _ = _image(mask_version, [(0x0100, 0x20, False)], manufacturer="M-0004")
    (segment,) = image.segments
    # 0x0104: one-octet manufacturer, application number, version; 0x0109: PEI type.
    assert segment.data[4:8] == bytes([0x04, 0xA0, 0x0E, 0x01])
    assert segment.data[9] == 0x14
    # Written by a full download (the seed octets), not by a partial one.
    assert segment.initialized_mask is not None and segment.mask is not None
    assert segment.initialized_mask[4:8] == b"\xff" * 4
    assert segment.initialized_mask[9] == 0xFF
    assert not any(segment.mask)


def test_bcu2_keeps_only_the_pei_type_in_memory() -> None:
    # MV-0021 has the application id as a property: only the PEI type is in the header.
    image, _ = _image("MV-0021", [(0x0100, 0x20, False)])
    (segment,) = image.segments
    assert segment.data[4:8] == bytes(4)
    assert segment.data[9] == 0x14


def test_every_segment_covering_the_header_gets_the_identity() -> None:
    # A code segment and a table segment over the same memory: the download writes both.
    image, _ = _image(
        "MV-0012", [(0x0100, 0x20, False), (0x0100, 0x10, False)], manufacturer="M-0004"
    )
    assert [s.data[4:8] for s in image.segments] == [bytes([4, 0xA0, 0x0E, 1])] * 2


def test_a_parameter_over_the_header_makes_a_partial_download_write_it() -> None:
    image, _ = _image("MV-0012", [(0x0100, 0x20, False)], parameter=(0, 5))
    (segment,) = image.segments
    assert segment.mask is not None
    assert segment.mask[4:8] == b"\xff" * 4
    assert segment.data[4:8] == bytes([0xFA, 0xA0, 0x0E, 0x01])


def test_no_identity_without_a_segment_at_its_location() -> None:
    image, _ = _image("MV-0012", [(0x0200, 0x20, False)])
    (segment,) = image.segments
    assert segment.data == bytes(0x20)
    # M112 keeps its application id as a property.
    image, _ = _image("MV-0701", [(0x0100, 0x20, False)])
    assert image.segments[0].data == bytes(0x20)


def test_user_memory_segments_stay_out_of_standard_memory() -> None:
    # A one-octet user memory segment at an address inside a standard memory segment.
    image, _ = _image(
        "MV-0705",
        [(0x476A, 0x100, False), (0x47DD, 1, True)],
        parameter=(0, 0x47DD - 0x476A),
    )
    assert [s.address for s in image.segments] == [0x476A]
    assert [s.address for s in image.user_segments] == [0x47DD]
    # A write of the standard memory range takes the parameter, not the user memory octet.
    runs = image.masked_writes(0x476A, 0x100)
    assert runs == [(0x47DD, b"\x3b")]
