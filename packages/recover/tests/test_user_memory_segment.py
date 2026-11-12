"""A code segment in the device's user memory is read with the user memory services
(A_UserMemory_Read): the user memory is an address space of its own, and at the same address the
standard memory holds another segment (an ABB A018 user-memory octet at 0x4676 lies inside the
segment at 0x4600). It is read where a step of the load procedure writes it in the user memory,
the only place the download writes it, and the parameters it holds are recovered from it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.apci import (
    APCI,
    MemoryRead,
    MemoryResponse,
    UserMemoryRead,
    UserMemoryResponse,
)

from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_mem_addr_space_t import (
    LdCtrlMemAddrSpace,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.read_config import read_parameter_memory

_SOURCE = IndividualAddress("1.1.1")


class _Device:
    """A device whose user memory is kept apart from its standard memory, answering the
    memory reads of each address space from its own memory."""

    def __init__(
        self,
        standard: dict[int, bytes] | None = None,
        user: dict[int, bytes] | None = None,
    ) -> None:
        self.standard = _flat(standard or {})
        self.user = _flat(user or {})
        self.reads: list[tuple[str, int, int]] = []

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        raise AssertionError(f"unexpected send_data: {payload}")

    async def request(
        self, payload: APCI, expected: type[APCI] | None = None
    ) -> Telegram:
        if isinstance(payload, UserMemoryRead):
            self.reads.append(("user", payload.address, payload.count))
            data = _read(self.user, payload.address, payload.count)
            response: APCI = UserMemoryResponse(address=payload.address, data=data)
        elif isinstance(payload, MemoryRead):
            self.reads.append(("standard", payload.address, payload.count))
            data = _read(self.standard, payload.address, payload.count)
            response = MemoryResponse(address=payload.address, data=data)
        else:
            raise AssertionError(f"unexpected request: {payload}")
        return Telegram(destination_address=_SOURCE, payload=response)


def _flat(blocks: dict[int, bytes]) -> dict[int, int]:
    return {
        address + index: value
        for address, data in blocks.items()
        for index, value in enumerate(data)
    }


def _read(memory: dict[int, int], address: int, count: int) -> bytes:
    return bytes(memory.get(address + index, 0) for index in range(count))


def _segment(address: int, size: int) -> LdCtrlAbsSegment:
    return LdCtrlAbsSegment(
        lsm_idx=4,
        seg_type=0,
        address=address,
        size=size,
        access=0,
        mem_type=3,
        seg_flags=0,
    )


def _user_write(address: int, size: int) -> LdCtrlWriteMem:
    return LdCtrlWriteMem(
        address=address,
        size=size,
        verify=True,
        address_space=LdCtrlMemAddrSpace.USER,
    )


def _a018(*controls: object) -> Any:
    """A memory-mapped application with a user memory octet at 0x4676, inside the standard
    memory segment at 0x4600, loaded by its own procedure with ``controls`` (none: without a
    procedure of its own)."""
    sizes = {"AS-4000": 0x10, "AS-4600": 160, "AS-U04676": 1}
    bases = {"AS-4000": 0x4000, "AS-4600": 0x4600, "AS-U04676": 0x4676}
    return cast(
        Any,
        SimpleNamespace(
            dynamic_ui=lambda: SimpleNamespace(
                segment_sizes=lambda: sizes, segment_base_addrs=lambda: bases
            ),
            load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE
            if controls
            else None,
            load_procedures=SimpleNamespace(
                load_procedure=[SimpleNamespace(choice=list(controls))]
            )
            if controls
            else None,
            program=SimpleNamespace(
                static=SimpleNamespace(
                    address_table=SimpleNamespace(code_segment="AS-4000"),
                    code=SimpleNamespace(
                        absolute_segment=[
                            SimpleNamespace(id="AS-4000", user_memory=False),
                            SimpleNamespace(id="AS-4600", user_memory=False),
                            SimpleNamespace(id="AS-U04676", user_memory=True),
                        ],
                        relative_segment=[],
                    ),
                )
            ),
        ),
    )


async def _read_segments(
    application: Any, device: _Device | None = None
) -> tuple[dict[str, bytes], list[tuple[str, int, int]]]:
    device = device or _Device()
    read = await read_parameter_memory(DeviceProgrammer(cast(Any, device)), application)
    return read, device.reads


async def test_a_user_memory_segment_no_step_writes_is_not_read() -> None:
    # Without a step in the user memory, the download writes nothing there: only the
    # standard memory is read, as for an application without user memory.
    read, reads = await _read_segments(_a018())
    assert set(read) == {"AS-4000", "AS-4600"}
    assert {space for space, _address, _count in reads} == {"standard"}
    read, reads = await _read_segments(
        _a018(_segment(0x4000, 0x10), _segment(0x4600, 160))
    )
    assert set(read) == {"AS-4000", "AS-4600"}
    assert {space for space, _address, _count in reads} == {"standard"}


async def test_a_user_memory_segment_a_step_writes_is_read_from_the_user_memory() -> (
    None
):
    device = _Device(standard={0x4676: b"\x11"}, user={0x4676: b"\x22"})
    read, reads = await _read_segments(
        _a018(_segment(0x4000, 0x10), _segment(0x4600, 160), _user_write(0x4676, 1)),
        device,
    )
    assert set(read) == {"AS-4000", "AS-4600", "AS-U04676"}
    assert read["AS-U04676"] == b"\x22"
    assert read["AS-4600"][0x76] == 0x11
    assert ("user", 0x4676, 1) in reads
    assert all(
        space == "standard" for space, address, _count in reads if address < 0x4676
    )


async def test_a_step_in_the_user_memory_does_not_write_the_standard_memory() -> None:
    # The step at 0x4676 of the user memory reaches nothing of the standard segment at the
    # same address: no step of the procedure writes that segment.
    read, reads = await _read_segments(
        _a018(_segment(0x4000, 0x10), _user_write(0x4676, 1))
    )
    assert set(read) == {"AS-4000", "AS-U04676"}
    assert ("user", 0x4676, 1) in reads
    assert not any(
        space == "standard" and address == 0x4600 for space, address, _ in reads
    )


_APP = "M-00FA_A-0008-01-0000"
_STANDARD = f"{_APP}_P-1_R-1"
_USER = f"{_APP}_P-2_R-2"
_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="8" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="User memory"
            LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-4000" Address="16384" Size="16"><Data>AAAAAAAAAAAAAAAAAAAAAA==</Data></AbsoluteSegment>
              <AbsoluteSegment Id="{_APP}_AS-0100" Address="256" Size="1"><Data>AA==</Data></AbsoluteSegment>
              <AbsoluteSegment Id="{_APP}_AS-U0100" Address="256" Size="1" UserMemory="true"><Data>AA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="Standard" ParameterType="{_APP}_PT-N" Text="Standard" Value="0">
                <Memory CodeSegment="{_APP}_AS-0100" Offset="0" BitOffset="0" />
              </Parameter>
              <Parameter Id="{_APP}_P-2" Name="User" ParameterType="{_APP}_PT-N" Text="User" Value="0">
                <Memory CodeSegment="{_APP}_AS-U0100" Offset="0" BitOffset="0" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_STANDARD}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_USER}" RefId="{_APP}_P-2" />
            </ParameterRefs>
            <AddressTable CodeSegment="{_APP}_AS-4000" Offset="0" MaxEntries="8" />
            <LoadProcedures>
              <LoadProcedure>
                <LdCtrlConnect />
                <LdCtrlAbsSegment LsmIdx="1" SegType="0" Address="16384" Size="16" Access="0" MemType="3" SegFlags="0" />
                <LdCtrlAbsSegment LsmIdx="4" SegType="0" Address="256" Size="1" Access="0" MemType="3" SegFlags="0" />
                <LdCtrlWriteMem Address="256" Size="1" Verify="true" AddressSpace="User" />
                <LdCtrlDisconnect />
              </LoadProcedure>
            </LoadProcedures>
          </Static>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_STANDARD}" />
                <ParameterRefRef RefId="{_USER}" />
              </ParameterBlock>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


async def test_a_parameter_in_the_user_memory_is_recovered(
    application: Application,
) -> None:
    # Both parameters sit at 0x0100, each in its own address space.
    device = _Device(standard={0x0100: b"\x0b"}, user={0x0100: b"\x16"})
    segments, _reads = await _read_segments(application, device)
    assert segments[f"{_APP}_AS-U0100"] == b"\x16"
    recovered = recover_parameters(application, segments)
    assert recovered.values[_STANDARD] == "11"
    assert recovered.values[_USER] == "22"
