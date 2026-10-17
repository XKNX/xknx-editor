"""Offline regressions from a wire capture of a System B device.

The JSON stores reconstructed memory and final MCB bytes; tests require no pcap,
network connection, or device. The fake models allocation fill and device CRCs.
"""

from __future__ import annotations

import importlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from xknx import XKNX
from xknx.telegram.apci import MemoryWrite, PropertyValueRead, PropertyValueWrite
from xsdata.formats.dataclass.parsers import XmlParser

from xknxeditor.download.crc import segment_crc
from xknxeditor.download.errors import CompareMismatch, PartialDownloadError
from xknxeditor.download.image import DownloadImage, RelativeSegment, build_image
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.merge import resolve_download_controls
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.application_program_static_t_code_relative_segment import (
    ApplicationProgramStaticCodeRelativeSegment,
)
from xknxeditor.namespaces.intermediate.hawk_configuration_data_t import (
    HawkConfigurationData,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_base_t import LdCtrlBase
from xknxeditor.namespaces.intermediate.ld_ctrl_base_t_on_error import LdCtrlBaseOnError
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_error_cause_t import LdCtrlErrorCause
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
    LdCtrlLoadImageProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.master_data_t import (
    MasterData as RawMasterData,
)
from xknxeditor.prod import Application, MasterData, load
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .conftest import FakeDevice
from .test_group_communication import MAPPING, mask_fixture
from .test_image import _FIXTURE

WIRE = json.loads((Path(__file__).parent / "fixtures/wire_capture.json").read_text())
WIRE_WRITES = json.loads(
    (Path(__file__).parent / "fixtures/wire_capture_writes.json").read_text()
)


def seeded_application(
    *segments: tuple[int, int, bytes],
) -> tuple[Application, DynamicUI]:
    original = next(iter(load(_FIXTURE).applications.values()))
    program = deepcopy(original.program)
    assert program.static.code is not None
    declarations = [
        ApplicationProgramStaticCodeRelativeSegment(
            id=f"segment-{n}",
            size=len(data),
            data=data,
            load_state_machine=index,
            offset=offset,
        )
        for n, (index, offset, data) in enumerate(segments)
    ]
    program.static.code.absolute_segment = []
    program.static.code.relative_segment = declarations
    program.application_number = 0x200
    program.application_version = 0x10
    if program.static.options is not None:
        program.static.options.prefer_partial_download_if_application_loaded = False
    app = cast(Application, SimpleNamespace(program=program, manufacturer_id="M-00FA"))
    ui = cast(
        DynamicUI,
        SimpleNamespace(
            segment_base_addrs=lambda: {s.id: s.offset for s in declarations},
            encode_to_memory_masked=lambda: {
                s.id: (s.data, b"\x00" * s.size) for s in declarations
            },
            encode_to_typed_properties=lambda: {},
        ),
    )
    return app, ui


def test_initialized_real_gira_header_survives_full_image() -> None:
    app = next(iter(load(_FIXTURE).applications.values()))
    image = build_image(app)
    r = LoadProcedureRunner(
        app, image, DeviceProgrammer(FakeDevice()), controls=[], object_types=MAPPING
    )
    control = LdCtrlWriteRelMem(obj_idx=4, offset=0, size=5, verify=True)
    assert r._relative_runs(control) == [(0, bytes.fromhex("496e733006"))]
    r.scope = DownloadScope.PARAMETERS
    assert r._relative_runs(control) == []


def test_relative_objects_offsets_and_complete_object_data() -> None:
    app, ui = seeded_application((4, 0, b"AAAA"), (5, 0, b"BBBB"), (4, 4, b"CCCC"))
    image = build_image(app, ui=ui)
    assert image.segments == ()
    assert image.object_segments == {4: b"AAAACCCC", 5: b"BBBB"}
    r = LoadProcedureRunner(
        app, image, DeviceProgrammer(FakeDevice()), controls=[], object_types=MAPPING
    )
    assert r._relative_runs(
        LdCtrlWriteRelMem(obj_idx=4, offset=2, size=4, verify=True)
    ) == [(2, b"AA"), (4, b"CC")]
    assert r._relative_runs(
        LdCtrlWriteRelMem(obj_idx=5, offset=0, size=4, verify=True)
    ) == [(0, b"BBBB")]


@pytest.mark.parametrize("preview", [False, True])
async def test_relative_write_uses_device_resolved_object_index(preview) -> None:
    app, ui = seeded_application((4, 0, b"AAAA"), (5, 0, b"BBBB"))
    # Runtime object location is authoritative for type-addressed controls.
    device = FakeDevice({**MAPPING, 4: 4, 5: 3}, 0x07B0)
    device.table_references = {4: 0x3804, 5: 0x4000}
    r = LoadProcedureRunner(
        app,
        build_image(app, ui=ui),
        DeviceProgrammer(device),
        controls=[LdCtrlWriteRelMem(obj_type=3, offset=0, size=4, verify=True)],
        object_types=MAPPING,
    )
    if preview:
        report = await r.preflight()
        assert [(s.address, s.planned) for s in report.segments] == [(0x4000, b"BBBB")]
        assert not device.memory
    else:
        await r.run()
        assert [
            (p.address, p.data) for p in device.sent if isinstance(p, MemoryWrite)
        ] == [(0x4000, b"BBBB")]


class CaptureDevice(FakeDevice):
    """Serialize every request and model the captured System B memory lifecycle."""

    def __init__(self) -> None:
        super().__init__(MAPPING, 0x07B0)
        self.table_references = {int(i): row["base"] for i, row in WIRE.items()}
        self.allocations: dict[int, int] = {}
        self.load_states = {i: LoadState.LOADED for i in range(1, 5)}
        self.load_states[5] = LoadState.UNLOADED
        for i, row in WIRE.items():
            self.memory.update(
                {row["base"] + n: b for n, b in enumerate(bytes.fromhex(row["data"]))}
            )
            self.properties[int(i), 27] = bytes.fromhex(row["mcb"])
        self.properties[4, 13] = bytes.fromhex("00fa020010")
        self.compare_checkpoints: list[dict[int, LoadState]] = []

    async def send_data(self, payload, wait_for_ack=True):
        payload.to_knx()
        assert payload.calculated_length() <= 15
        await super().send_data(payload, wait_for_ack)

    async def request(self, payload, expected):
        payload.to_knx()
        assert payload.calculated_length() <= 15
        if (
            isinstance(payload, PropertyValueRead)
            and self.load_states[4] == 2
            and (payload.object_index, payload.property_id) == (4, 27)
        ):
            self.compare_checkpoints.append(dict(self.load_states))
        return await super().request(payload, expected)

    def _handle_property_write(self, payload: PropertyValueWrite) -> None:
        super()._handle_property_write(payload)
        index, data = payload.object_index, payload.data
        if payload.property_id != 5:
            return
        if data[:2] == b"\x03\x0b":
            size = int.from_bytes(data[2:6], "big")
            self.allocations[index] = size
            if data[6] & 1:
                self.memory.update(
                    {self.table_references[index] + n: data[7] for n in range(size)}
                )
        elif data[0] == 2:
            size = self.allocations[index]
            actual = bytes(
                self.memory[self.table_references[index] + n] for n in range(size)
            )
            access = 0x32 if index == 4 else 0x33
            self.properties[index, 27] = (
                size.to_bytes(4, "big")
                + bytes([0, access])
                + segment_crc(actual).to_bytes(2, "big")
            )


def capture_application(
    *merge2_controls: LdCtrlBase,
) -> tuple[Application, DownloadImage]:
    """Build the captured application and splice optional early property gates."""
    data = bytes.fromhex(WIRE["4"]["data"])
    app, ui = seeded_application((4, 0, data))
    app.load_procedure_style = LoadProcedureStyle.MERGED_PROCEDURE  # type: ignore[misc]
    app.program.id = "M-00FA_A-1000-10-0001"
    app.load_procedures = XmlParser().from_path(  # type: ignore[misc]
        Path(__file__).parent / "fixtures/systemb_fragments.xml", LoadProcedures
    )
    app.load_procedures.load_procedure[0].choice.extend(merge2_controls)
    built = build_image(app, ui=ui)
    image = DownloadImage(
        built.segments,
        built.properties,
        tuple(
            RelativeSegment(MAPPING[i], bytes.fromhex(WIRE[str(i)]["data"]))
            for i in (1, 2, 3)
        ),
        object_segments=built.object_segments,
        application_segments=built.application_segments,
    )
    return app, image


def capture_master() -> RawMasterData:
    configuration = XmlParser().from_path(
        Path(__file__).parent / "fixtures/systemb_procedures.xml",
        HawkConfigurationData,
    )
    return cast(
        RawMasterData,
        SimpleNamespace(
            mask_versions=SimpleNamespace(
                mask_version=[
                    SimpleNamespace(
                        id="MV-07B0", hawk_configuration_data=[configuration]
                    )
                ]
            )
        ),
    )


async def run_capture_download(
    monkeypatch: pytest.MonkeyPatch, device: CaptureDevice, *controls: LdCtrlBase
) -> None:
    """Exercise the public download path with only the connection replaced."""
    dl = importlib.import_module("xknxeditor.download.download")
    app, image = capture_application(*controls)
    master = capture_master()

    class OfflineConnection:
        async def open(self):
            return device

        async def close(self):
            pass

    monkeypatch.setattr(dl, "_connection_manager", lambda *a: OfflineConnection())
    await dl.download(
        cast(XKNX, object()),
        "1.1.5",
        app,
        image=image,
        master=cast(MasterData, SimpleNamespace(raw=master)),
        scope=DownloadScope.APPLICATION,
        max_apdu_length=15,
        expected_descriptor=0x07B0,
    )


def early_mcb_compare(**kwargs) -> LdCtrlCompareProp:
    # The allocated MCB has access 0x32, making this a valid-data mismatch.
    return LdCtrlCompareProp(
        obj_idx=4,
        prop_id=27,
        inline_data=bytes.fromhex("0000001000330000"),
        **kwargs,
    )


@pytest.mark.parametrize("gate", ["none", "ignore", "static-pid7", "par-only"])
async def test_ap1_matches_all_captured_memory_allocations_identity_and_final_mcbs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, gate: str
) -> None:
    controls = {
        "none": [],
        "ignore": [
            early_mcb_compare(
                on_error=[
                    LdCtrlBaseOnError(
                        cause=LdCtrlErrorCause.COMPARE_MISMATCH, ignore=True
                    )
                ]
            )
        ],
        "static-pid7": [LdCtrlCompareProp(obj_idx=4, prop_id=7, inline_data=bytes(4))],
        "par-only": [early_mcb_compare(applies_to=LdCtrlProcType.PAR)],
    }[gate]
    device = CaptureDevice()
    with caplog.at_level("INFO"):
        await run_capture_download(monkeypatch, device, *controls)
    if gate == "ignore":
        assert device.compare_checkpoints == [{1: 0, 2: 0, 3: 0, 4: 2, 5: 0}]
        assert "continuing after OnError Ignore=true" in caplog.text
        assert "cause=CompareMismatch" in caplog.text
    else:
        assert not device.compare_checkpoints
    for i in (1, 2, 3, 4):
        row = WIRE[str(i)]
        expected = bytes.fromhex(row["data"])
        assert (
            bytes(device.memory[row["base"] + n] for n in range(len(expected)))
            == expected
        )
        assert device.properties[i, 27].hex() == row["mcb"]
        assert device.load_states[i] == 1
    assert device.load_states[5] == 0
    assert device.properties[4, 13].hex() == "00fa020010"
    reads = [
        (p.object_index, p.property_id, p.start_index)
        for p in device.sent
        if isinstance(p, PropertyValueRead)
    ]
    assert [i for i, pid, start in reads if pid == 7] == (
        [4, 4, 3, 2, 1] if gate == "static-pid7" else [4, 3, 2, 1]
    )
    assert [(i, start) for i, pid, start in reads if pid == 27][-4:] == [
        (1, 1),
        (2, 1),
        (3, 1),
        (4, 1),
    ]
    allocations = [
        p.data.hex()
        for p in device.sent
        if isinstance(p, PropertyValueWrite)
        and p.property_id == 5
        and p.data[:2] == b"\x03\x0b"
    ]
    assert allocations == [WIRE[str(i)]["allocation"] for i in (4, 3, 1, 2)]
    mcb_writes = [
        p
        for p in device.sent
        if isinstance(p, PropertyValueWrite) and p.property_id == 27
    ]
    assert [(p.object_index, p.data.hex()) for p in mcb_writes] == [
        (4, "0000001000320000")
    ]
    events = [
        (p.object_index, p.data[0])
        for p in device.sent
        if isinstance(p, PropertyValueWrite) and p.property_id == 5
    ]
    assert [i for i, event in events if event == 4] == [5, 4, 3, 2, 1]
    assert [i for i, event in events if event == 1] == [4, 3, 1, 2]
    assert [i for i, event in events if event == 2] == [4, 3, 2, 1]
    writes = [p for p in device.sent if isinstance(p, MemoryWrite)]
    assert len(writes) == 52
    assert sum(len(p.data) for p in writes) == 596
    emitted = [
        ["property", p.object_index, p.property_id, p.data.hex()]
        if isinstance(p, PropertyValueWrite)
        else ["memory", p.address, p.data.hex()]
        for p in device.sent
        if isinstance(p, (PropertyValueWrite, MemoryWrite))
    ]
    assert emitted == WIRE_WRITES
    reference_read = next(
        n
        for n, p in enumerate(device.sent)
        if isinstance(p, PropertyValueRead)
        and (p.object_index, p.property_id) == (4, 7)
    )
    verify_mode = next(
        n
        for n, p in enumerate(device.sent)
        if isinstance(p, PropertyValueWrite)
        and (p.object_index, p.property_id) == (0, 14)
    )
    assert reference_read < verify_mode
    from xknx.telegram.apci import MemoryRead

    assert not any(isinstance(p, MemoryRead) for p in device.sent)


@pytest.mark.parametrize("handler", ["absent", "wrong-cause", "fail", "first-wins"])
async def test_ap1_unhandled_early_compare_reports_partial_download(
    monkeypatch: pytest.MonkeyPatch, handler: str
) -> None:
    ignore = LdCtrlBaseOnError(cause=LdCtrlErrorCause.COMPARE_MISMATCH, ignore=True)
    fail = LdCtrlBaseOnError(cause=LdCtrlErrorCause.COMPARE_MISMATCH, ignore=False)
    handlers = {
        "absent": [],
        "wrong-cause": [
            LdCtrlBaseOnError(cause=LdCtrlErrorCause.RESOURCE_NOT_FOUND, ignore=True)
        ],
        "fail": [fail],
        "first-wins": [fail, ignore],
    }[handler]
    device = CaptureDevice()
    with pytest.raises(PartialDownloadError) as failure:
        await run_capture_download(
            monkeypatch, device, early_mcb_compare(on_error=handlers)
        )
    assert isinstance(failure.value.__cause__, CompareMismatch)
    assert "object 4 property 27" in str(failure.value)
    assert device.load_states == {1: 0, 2: 0, 3: 0, 4: 2, 5: 0}
    assert not any(
        isinstance(p, PropertyValueWrite)
        and p.property_id == 5
        and p.object_index in (1, 2)
        and p.data[0] in (1, 2)
        for p in device.sent
    )
    assert not device.restarted


def test_real_ap1_has_no_compare_and_commits_after_all_writes() -> None:
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
        LdCtrlLoadCompleted,
    )

    app, _ = capture_application()
    controls = resolve_download_controls(
        app, capture_master(), scope=DownloadScope.APPLICATION
    )
    assert not any(isinstance(c, LdCtrlCompareProp) for c in controls)
    mcb = next(
        c for c in controls if isinstance(c, LdCtrlWriteProp) and c.prop_id == 27
    )
    assert mcb.inline_data == bytes.fromhex("00000010003200000000")
    assert not mcb.verify
    identity = next(
        c for c in controls if isinstance(c, LdCtrlWriteProp) and c.prop_id == 13
    )
    assert identity.inline_data == bytes(5)
    first_completion = next(
        n for n, c in enumerate(controls) if isinstance(c, LdCtrlLoadCompleted)
    )
    assert controls[first_completion - 1] == identity
    assert all(
        n < first_completion
        for n, c in enumerate(controls)
        if isinstance(c, (LdCtrlWriteProp, LdCtrlWriteRelMem))
    )
    assert [c.obj_idx for c in controls if isinstance(c, LdCtrlLoadImageProp)] == [
        1,
        2,
        3,
        4,
    ]


async def test_ap1_reads_new_object_bases_after_allocation(monkeypatch) -> None:
    class RelocatingDevice(CaptureDevice):
        def _handle_property_write(self, payload):
            if payload.property_id == 5 and payload.data[:2] == b"\x03\x0b":
                self.table_references[payload.object_index] = WIRE[
                    str(payload.object_index)
                ]["base"]
            super()._handle_property_write(payload)

    device = RelocatingDevice()
    device.table_references = {
        i: base + 0x1000 for i, base in device.table_references.items()
    }
    await run_capture_download(monkeypatch, device)
    for i in (1, 2, 3, 4):
        row = WIRE[str(i)]
        assert device.load_states[i] == 1
        assert device.properties[i, 27].hex() == row["mcb"]
        assert bytes(
            device.memory[row["base"] + n]
            for n in range(len(bytes.fromhex(row["data"])))
        ) == bytes.fromhex(row["data"])
    assert all(
        0x3400 <= p.address < 0x3814 for p in device.sent if isinstance(p, MemoryWrite)
    )


async def test_ap1_real_identity_rejection_stops_before_commit(monkeypatch) -> None:
    from xknx.telegram.apci import PropertyValueResponse

    class WrongIdentityDevice(CaptureDevice):
        async def request(self, payload, expected):
            reply = await super().request(payload, expected)
            if isinstance(payload, PropertyValueWrite) and payload.property_id == 13:
                assert payload.data == bytes.fromhex("00fa020010")
                assert isinstance(reply.payload, PropertyValueResponse)
                reply.payload.data = bytes.fromhex("00fa020011")
            return reply

    device = WrongIdentityDevice()
    with pytest.raises(PartialDownloadError, match="property 13") as error:
        await run_capture_download(monkeypatch, device)
    assert isinstance(error.value.__cause__, CompareMismatch)
    assert device.load_states == {1: 2, 2: 2, 3: 2, 4: 2, 5: 0}
    assert not device.restarted


@pytest.mark.parametrize(
    "scope,mode",
    [
        (DownloadScope.FULL, 1),
        (DownloadScope.APPLICATION, 1),
        (DownloadScope.PARAMETERS, 0),
    ],
)
def test_real_gira_applies_to_selects_only_one_allocation(scope, mode) -> None:
    app = next(iter(load(_FIXTURE).applications.values()))
    _, master = mask_fixture()
    controls = resolve_download_controls(app, master, scope=scope)
    allocations = [
        c for c in controls if isinstance(c, LdCtrlRelSegment) and c.lsm_idx == 4
    ]
    assert len(allocations) == 1
    assert allocations[0].mode == mode


@pytest.mark.parametrize("relative", [False, True])
async def test_inline_size_limits_writes_and_rejects_short_data_before_unload(
    relative,
) -> None:
    from xknxeditor.download.errors import ImageError
    from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem

    from .test_procedure import _application

    write = (
        LdCtrlWriteRelMem(obj_idx=4, offset=0, size=1, inline_data=b"ABCD", verify=True)
        if relative
        else LdCtrlWriteMem(address=0x3804, size=1, inline_data=b"ABCD", verify=True)
    )
    device = FakeDevice(MAPPING, descriptor=0x07B0)
    device.table_references[4] = 0x3804
    r = LoadProcedureRunner(
        _application(write),
        DownloadImage((), ()),
        DeviceProgrammer(device),
        object_types=MAPPING,
    )
    await r.run()
    assert device.memory == {0x3804: 65}
    write.size = 5
    device.sent.clear()
    r = LoadProcedureRunner(
        _application(LdCtrlUnload(lsm_idx=4), write),
        DownloadImage((), ()),
        DeviceProgrammer(device),
        object_types=MAPPING,
    )
    with pytest.raises(ImageError, match="declared size"):
        await r.run()
    assert not device.sent


async def test_verified_property_chunks_serialize_without_oversize_readback() -> None:
    from .test_procedure import _application

    device = CaptureDevice()
    control = LdCtrlWriteProp(
        obj_idx=4, prop_id=50, count=20, inline_data=bytes(range(20)), verify=True
    )
    await LoadProcedureRunner(
        _application(control), DownloadImage((), ()), DeviceProgrammer(device)
    ).run()
    writes = [p for p in device.sent if isinstance(p, PropertyValueWrite)]
    assert [(p.count, p.start_index) for p in writes] == [(10, 1), (10, 11)]
    assert b"".join(p.data for p in writes) == bytes(range(20))


@pytest.mark.parametrize("width,count", [(1, 20), (2, 12)])
async def test_property_preflight_chunks_to_response_apdu_budget(width, count) -> None:
    from xknx.telegram.apci import PropertyValueRead

    from .test_procedure import _application

    device = CaptureDevice()
    data = bytes(range(width * count))
    device.properties[4, 60] = data
    device.property_element_sizes[4, 60] = width
    control = LdCtrlWriteProp(
        obj_idx=4, prop_id=60, count=count, inline_data=data, verify=True
    )
    report = await LoadProcedureRunner(
        _application(control), DownloadImage((), ()), DeviceProgrammer(device)
    ).preflight()
    assert report.properties[0].current == report.properties[0].planned == data
    reads = [
        p
        for p in device.sent
        if isinstance(p, PropertyValueRead) and p.property_id == 60
    ]
    assert [p.count for p in reads] == ([10, 10] if width == 1 else [5, 5, 2])
    assert not any(isinstance(p, PropertyValueWrite) for p in device.sent)


@pytest.mark.parametrize("apdu,expected_counts", [(15, [11, 5]), (55, [15, 1])])
async def test_user_memory_serialization_limits(apdu, expected_counts) -> None:
    from xknx.telegram.apci import UserMemoryWrite

    device = FakeDevice()
    p = DeviceProgrammer(device, max_apdu_length=apdu)
    await p.write_memory(0x10000, bytes(range(16)))
    writes = [v for v in device.sent if isinstance(v, UserMemoryWrite)]
    assert [v.count for v in writes] == expected_counts
    for v in writes:
        v.to_knx()
        assert v.calculated_length() <= apdu
    from xknxeditor.download.errors import VerificationError

    device.sent.clear()
    with pytest.raises(VerificationError, match="20-bit"):
        await p.write_memory(0xFFFFF, b"xx")
    assert not device.sent


@pytest.mark.parametrize(
    "data", [b"\x38", bytes.fromhex("000038"), bytes.fromhex("0000003804")]
)
async def test_malformed_pid7_never_becomes_write_address(data) -> None:

    from xknxeditor.download.errors import VerificationError

    class BadReference(FakeDevice):
        def _handle_property_read(self, request: PropertyValueRead):
            response = super()._handle_property_read(request)
            if request.property_id == 7:
                response.data = data
            return response

    with pytest.raises(VerificationError, match="reference width"):
        await DeviceProgrammer(BadReference()).read_table_reference(4)


async def test_max_length_emits_relative_allocation() -> None:
    from xknxeditor.namespaces.intermediate.ld_ctrl_max_length_t import LdCtrlMaxLength

    from .test_procedure import _application

    device = FakeDevice(MAPPING)
    r = LoadProcedureRunner(
        _application(LdCtrlMaxLength(lsm_idx=4, size=0x100)),
        DownloadImage((), ()),
        DeviceProgrammer(device),
        object_types=MAPPING,
    )
    await r.run()
    assert device.sent[0].data.hex() == "030a0100000000000000"
    assert device.load_states[4] == 2


async def test_control_variable_suppresses_segment_write() -> None:
    from xknxeditor.download.image import MemorySegment
    from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import (
        LdCtrlAbsSegment,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_control_variable_t import (
        LdCtrlControlVariable,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_set_control_variable_t import (
        LdCtrlSetControlVariable,
    )

    from .test_procedure import _application

    controls = [
        LdCtrlSetControlVariable(
            name=LdCtrlControlVariable.ENABLE_SEGMENT_WRITE, value=False
        ),
        LdCtrlAbsSegment(
            lsm_idx=4,
            seg_type=0,
            address=0x100,
            size=4,
            access=0,
            mem_type=3,
            seg_flags=0,
        ),
    ]
    device = FakeDevice(MAPPING)
    r = LoadProcedureRunner(
        _application(*controls),
        DownloadImage((MemorySegment(0x100, b"DATA"),), ()),
        DeviceProgrammer(device),
        object_types=MAPPING,
    )
    await r.run()
    assert not device.memory
    assert device.load_states[4] == 2


async def test_verify_on_write_direct_verifies_inline_and_can_skip_it() -> None:
    from xknx.telegram.apci import MemoryRead

    from xknxeditor.download.image import MemorySegment
    from xknxeditor.namespaces.intermediate.ld_ctrl_control_variable_t import (
        LdCtrlControlVariable,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_set_control_variable_t import (
        LdCtrlSetControlVariable,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem

    from .test_procedure import _application

    verified = [
        LdCtrlSetControlVariable(
            name=LdCtrlControlVariable.ENABLE_VERIFY_ON_WRITE_DIRECT, value=True
        ),
        LdCtrlWriteMem(address=0x10D, size=1, verify=True, inline_data=b"\x00"),
    ]
    device = FakeDevice()
    await LoadProcedureRunner(
        _application(*verified),
        DownloadImage((), ()),
        DeviceProgrammer(device),
    ).run()
    assert device.memory[0x10D] == 0
    assert any(isinstance(p, MemoryRead) and p.address == 0x10D for p in device.sent)

    skipped = [
        LdCtrlSetControlVariable(
            name=LdCtrlControlVariable.ENABLE_VERIFY_ON_WRITE_DIRECT, value=False
        ),
        LdCtrlWriteMem(address=0x10D, size=1, verify=True, inline_data=b"\xff"),
        LdCtrlWriteMem(address=0x100, size=2, verify=True),
    ]
    device = FakeDevice()
    await LoadProcedureRunner(
        _application(*skipped),
        DownloadImage((MemorySegment(0x100, b"AB"),), ()),
        DeviceProgrammer(device),
    ).run()
    assert device.memory[0x10D] == 0xFF
    assert bytes(device.memory[a] for a in (0x100, 0x101)) == b"AB"
    reads = [p.address for p in device.sent if isinstance(p, MemoryRead)]
    assert 0x10D not in reads
    assert 0x100 in reads


async def test_wrong_property_write_echo_fails_immediately() -> None:
    from xknxeditor.download.errors import VerificationError

    from .test_procedure import _application

    class WrongEcho(CaptureDevice):
        async def request(self, payload, expected):
            response = await super().request(payload, expected)
            if isinstance(payload, PropertyValueWrite) and payload.property_id == 27:
                response.payload.data = bytes(8)
            return response

    control = LdCtrlWriteProp(
        obj_idx=4,
        prop_id=27,
        inline_data=bytes.fromhex("0000001000320000"),
        verify=True,
    )
    with pytest.raises(VerificationError, match="echo mismatch"):
        await LoadProcedureRunner(
            _application(control), DownloadImage((), ()), DeviceProgrammer(WrongEcho())
        ).run()


def test_entire_group_object_table_matches_capture_including_unlinked_objects() -> None:
    from dataclasses import fields

    from xknxeditor.download.image import _resolved_group_object_descriptors
    from xknxeditor.download.tables import priority_from_bits
    from xknxeditor.download.tables_systemb import build_group_object_table_b
    from xknxeditor.prod.parser_v2.ui import UiComObject

    raw = bytes.fromhex(WIRE["3"]["data"])
    linked = set(bytes.fromhex(WIRE["2"]["data"])[3::2])
    nodes = []
    # Decode the capture's active descriptors into UI flags, then re-encode the
    # complete table. All eight active-but-unlinked records must survive.
    for number in range(1, 201):
        flags, size = raw[2 * number : 2 * number + 2]
        if flags == size == 0:
            continue
        values = {f.name: False for f in fields(UiComObject)}
        values.update(
            ref_id=f"obj{number}",
            name="",
            function_text="",
            number=number,
            dpt_codes=(),
            object_size={
                0: "1 Bit",
                1: "2 Bit",
                2: "3 Bit",
                3: "4 Bit",
                4: "5 Bit",
                5: "6 Bit",
                6: "7 Bit",
                7: "1 Byte",
                8: "2 Bytes",
                9: "3 Bytes",
                10: "4 Bytes",
                14: "8 Bytes",
                20: "14 Bytes",
            }[size],
            priority=priority_from_bits(flags),
            communication=bool(flags & 4),
            read=bool(flags & 8),
            write=bool(flags & 16),
            transmit=bool(flags & 64),
            update=bool(flags & 128),
            read_on_init=bool(flags & 32),
        )
        nodes.append(UiComObject(**values))
    ui = cast(DynamicUI, SimpleNamespace(ui=lambda: nodes))
    assert (
        build_group_object_table_b(_resolved_group_object_descriptors(ui, linked), 200)
        == raw
    )


async def test_auto_verify_real_xknx_p2p_consumes_echo_and_wraps_sequences() -> None:
    from xknx.management import P2PConnection
    from xknx.telegram import IndividualAddress, Telegram
    from xknx.telegram.apci import (
        MemoryResponse,
        PropertyValueRead,
        PropertyValueResponse,
    )
    from xknx.telegram.tpci import TAck, TDataConnected

    address, tool = IndividualAddress("1.1.5"), IndividualAddress("1.1.34")
    sequence = 0

    async def send(telegram):
        nonlocal sequence
        if not isinstance(telegram.tpci, TDataConnected):
            return
        conn.process(
            Telegram(
                destination_address=tool,
                source_address=address,
                tpci=TAck(sequence_number=telegram.tpci.sequence_number),
            )
        )
        p = telegram.payload
        if isinstance(p, MemoryWrite):
            response = MemoryResponse(address=p.address, data=p.data)
        elif isinstance(p, PropertyValueWrite):
            response = PropertyValueResponse(
                object_index=0, property_id=14, start_index=p.start_index, data=p.data
            )
        elif isinstance(p, PropertyValueRead):
            response = PropertyValueResponse(
                object_index=p.object_index,
                property_id=p.property_id,
                start_index=p.start_index,
                data=b"\x00" if p.property_id == 14 else bytes.fromhex("00003604"),
            )
        else:
            raise AssertionError(p)
        conn.process(
            Telegram(
                destination_address=tool,
                source_address=address,
                tpci=TDataConnected(sequence_number=sequence),
                payload=response,
            )
        )
        sequence = (sequence + 1) & 15

    conn = P2PConnection(
        SimpleNamespace(
            current_address=tool, cemi_handler=SimpleNamespace(send_telegram=send)
        ),
        address,
        rate_limit=0,
    )
    conn._connected = True
    p = DeviceProgrammer(conn)
    await p.enable_memory_auto_verify()
    await p.write_memory(0x3804, bytes(range(240)), verify=True)
    assert await p.read_table_reference(3) == 0x3604


@pytest.mark.parametrize("address", [0x3804, 0x10000])
@pytest.mark.parametrize("bad_echo", [False, True])
async def test_existing_memory_verify_mode_is_detected_and_echo_checked(
    address, bad_echo
) -> None:
    from xknx.telegram.apci import MemoryRead, UserMemoryRead, UserMemoryWrite

    from xknxeditor.download.errors import VerificationError

    class AlreadyVerifying(FakeDevice):
        async def send_data(self, payload, wait_for_ack=True):
            assert not isinstance(payload, (MemoryWrite, UserMemoryWrite))
            await super().send_data(payload, wait_for_ack)

        async def request(self, payload, expected):
            response = await super().request(payload, expected)
            if bad_echo and isinstance(payload, (MemoryWrite, UserMemoryWrite)):
                response.payload.address += 1
            return response

    device = AlreadyVerifying()
    device.properties[0, 14] = b"\x04"
    programmer = DeviceProgrammer(device)
    if bad_echo:
        with pytest.raises(VerificationError, match="memory write echo mismatch"):
            await programmer.write_memory(address, b"DATA", verify=True)
    else:
        await programmer.write_memory(address, b"DATA", verify=True)
    assert programmer.memory_auto_verify
    assert not any(isinstance(p, (MemoryRead, UserMemoryRead)) for p in device.sent)


async def test_property_element_slice_and_inline_padding_use_declared_width() -> None:
    from xknxeditor.download.image import PropertyValue
    from xknxeditor.namespaces.intermediate.ld_ctrl_declare_prop_desc_t import (
        LdCtrlDeclarePropDesc,
    )
    from xknxeditor.namespaces.intermediate.prop_type_t import PropType

    from .test_procedure import _application

    declaration = LdCtrlDeclarePropDesc(
        obj_idx=4,
        prop_id=60,
        prop_type=PropType.PDT_GENERIC_02,
        max_elements=3,
        read_access=0,
        write_access=0,
        writable=True,
    )
    control = LdCtrlWriteProp(
        obj_idx=4,
        prop_id=60,
        start_element=2,
        count=1,
        inline_data=b"xxxx",
        verify=True,
    )
    image = DownloadImage((), (PropertyValue(4, 60, 0, b"ABCDEF", element_size=2),))
    device = FakeDevice()
    await LoadProcedureRunner(
        _application(declaration, control), image, DeviceProgrammer(device)
    ).run()
    writes = [p for p in device.sent if isinstance(p, PropertyValueWrite)]
    assert [(p.data, p.start_index, p.count) for p in writes] == [(b"CD", 2, 1)]


async def test_partial_property_capture_does_not_write_zero_filled_gaps() -> None:
    from xknxeditor.download.errors import ImageError
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
        LdCtrlLoadImageProp,
    )

    from .test_procedure import _application

    device = FakeDevice()
    device.properties[4, 60] = b"ABCD"
    device.property_element_sizes[4, 60] = 2
    controls = [
        LdCtrlLoadImageProp(obj_idx=4, prop_id=60, start_element=2, count=1),
        LdCtrlWriteProp(obj_idx=4, prop_id=60, start_element=1, count=2, verify=True),
    ]
    with pytest.raises(ImageError, match="incomplete property image"):
        await LoadProcedureRunner(
            _application(*controls), DownloadImage((), ()), DeviceProgrammer(device)
        ).run()
    assert not any(isinstance(p, PropertyValueWrite) for p in device.sent)
    assert device.properties[4, 60] == b"ABCD"


async def test_application_identity_resolves_type_addressed_control() -> None:
    application, _ = mask_fixture()
    device = FakeDevice({0: 0, 1: 3}, 0x07B0)
    await LoadProcedureRunner(
        application,
        DownloadImage((), ()),
        DeviceProgrammer(device),
        controls=[
            LdCtrlWriteProp(obj_type=3, prop_id=13, inline_data=bytes(5), verify=True)
        ],
    ).run()
    assert device.properties[1, 13].hex() == "00fa020010"


@pytest.mark.parametrize("indexed_image", [False, True])
async def test_property_image_occurrence_matches_resolved_object(indexed_image) -> None:
    from xknxeditor.download.image import PropertyValue

    from .test_procedure import _application

    device = FakeDevice({0: 0, 1: 3, 2: 3})
    image = DownloadImage(
        (),
        (
            PropertyValue(1 if indexed_image else None, 60, 0, b"AAAA", element_size=1),
            PropertyValue(
                2 if indexed_image else None,
                60,
                0 if indexed_image else 1,
                b"BCDE",
                element_size=2,
            ),
        ),
    )
    control = LdCtrlWriteProp(
        obj_type=3, occurrence=1, prop_id=60, start_element=2, count=1, verify=True
    )
    await LoadProcedureRunner(
        _application(control), image, DeviceProgrammer(device)
    ).run()
    writes = [p for p in device.sent if isinstance(p, PropertyValueWrite)]
    assert [(p.object_index, p.start_index, p.data) for p in writes] == [(2, 2, b"DE")]


@pytest.mark.parametrize("relative", [False, True])
async def test_memory_capture_populates_later_write(relative) -> None:
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_mem_t import (
        LdCtrlLoadImageMem,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_rel_mem_t import (
        LdCtrlLoadImageRelMem,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem

    from .test_procedure import _application

    capture = (
        LdCtrlLoadImageRelMem(obj_idx=4, offset=0, size=4)
        if relative
        else LdCtrlLoadImageMem(address=0x100, size=4)
    )
    write = (
        LdCtrlWriteRelMem(obj_idx=4, offset=0, size=4, verify=True)
        if relative
        else LdCtrlWriteMem(address=0x100, size=4, verify=True)
    )
    device = FakeDevice(MAPPING, descriptor=0x07B0)
    device.table_references[4] = 0x100
    device.memory.update({0x100 + n: value for n, value in enumerate(b"DATA")})
    await LoadProcedureRunner(
        _application(capture, write),
        DownloadImage((), ()),
        DeviceProgrammer(device),
        object_types=MAPPING,
    ).run()
    assert [p.data for p in device.sent if isinstance(p, MemoryWrite)] == [b"DATA"]


async def test_short_memory_compare_mask_defaults_to_ff() -> None:
    from xknxeditor.download.errors import VerificationError
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_mem_t import (
        LdCtrlCompareMem,
    )

    from .test_procedure import _application

    control = LdCtrlCompareMem(address=0x100, size=2, inline_data=b"AB", mask=b"\xff")
    device = FakeDevice()
    device.memory = {0x100: 65, 0x101: 67}
    with pytest.raises(VerificationError, match="memory compare failed"):
        await LoadProcedureRunner(
            _application(control), DownloadImage((), ()), DeviceProgrammer(device)
        ).run()


@pytest.mark.parametrize(
    "address,count,data", [(0x101, 2, b"ab"), (0x100, 0, b"ab"), (0x100, 1, b"ab")]
)
async def test_memory_read_rejects_wrong_address_or_count(address, count, data) -> None:
    from xknx.telegram.apci import MemoryResponse

    from xknxeditor.download.errors import VerificationError

    class WrongResponse(FakeDevice):
        async def request(self, payload, expected):
            return self._telegram(
                MemoryResponse(address=address, count=count, data=data)
            )

    with pytest.raises(VerificationError, match="address/count mismatch"):
        await DeviceProgrammer(WrongResponse()).read_memory(0x100, 2)


@pytest.mark.parametrize("handler", ["variable", "filter"])
async def test_unsupported_conditional_semantics_fail_before_unload(handler) -> None:
    from xknxeditor.download.errors import UnsupportedProcedureError
    from xknxeditor.namespaces.intermediate.ld_ctrl_control_variable_t import (
        LdCtrlControlVariable,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_map_error_t import LdCtrlMapError
    from xknxeditor.namespaces.intermediate.ld_ctrl_set_control_variable_t import (
        LdCtrlSetControlVariable,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload

    from .test_procedure import _application

    controls = {
        "variable": LdCtrlSetControlVariable(
            name=LdCtrlControlVariable.ENABLE_OPTIMISTIC_WRITE, value=True
        ),
        "filter": LdCtrlMapError(
            ld_ctrl_filter=255, original_error=0xC0042B08, mapped_error=0
        ),
    }
    device = FakeDevice(MAPPING)
    with pytest.raises(UnsupportedProcedureError):
        await LoadProcedureRunner(
            _application(LdCtrlUnload(lsm_idx=4), controls[handler]),
            DownloadImage((), ()),
            DeviceProgrammer(device),
            object_types=MAPPING,
        ).run()
    assert not device.sent


async def test_unload_error_filter_does_not_hide_property_write_failure() -> None:
    from xknxeditor.download.errors import PropertyAccessRejected
    from xknxeditor.namespaces.intermediate.ld_ctrl_map_error_t import LdCtrlMapError
    from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload

    from .test_procedure import _application

    device = FakeDevice(MAPPING)
    device.absent_objects = {4}
    mapping = LdCtrlMapError(
        ld_ctrl_filter=5, original_error=0xC0042B08, mapped_error=0
    )
    controls = [
        mapping,
        LdCtrlUnload(lsm_idx=4),
        LdCtrlWriteProp(
            obj_idx=4,
            prop_id=27,
            inline_data=bytes.fromhex("0000001000320000"),
            verify=True,
        ),
    ]
    with pytest.raises(PropertyAccessRejected):
        await LoadProcedureRunner(
            _application(*controls),
            DownloadImage((), ()),
            DeviceProgrammer(device),
            object_types=MAPPING,
        ).run()
    assert len([p for p in device.sent if isinstance(p, PropertyValueWrite)]) == 2


async def test_rejected_read_uses_read_error_identity() -> None:
    from xknx.telegram.apci import PropertyValueResponse

    from xknxeditor.download.errors import PropertyAccessRejected

    class Rejected(FakeDevice):
        async def request(self, payload, expected):
            return self._telegram(
                PropertyValueResponse(
                    object_index=4, property_id=7, start_index=1, count=0, data=b""
                )
            )

    with pytest.raises(PropertyAccessRejected) as caught:
        await DeviceProgrammer(Rejected()).read_table_reference(4)
    assert caught.value.error_code == 0xC0042B07


async def test_memory_capture_overlays_compare_placeholder() -> None:
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_mem_t import (
        LdCtrlCompareMem,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_mem_t import (
        LdCtrlLoadImageMem,
    )

    from .test_procedure import _application

    device = FakeDevice()
    device.memory = {0x100: 65, 0x101: 66}
    controls = [
        LdCtrlLoadImageMem(address=0x100, size=2),
        LdCtrlCompareMem(address=0x100, size=2, inline_data=b"xx"),
    ]
    await LoadProcedureRunner(
        _application(*controls), DownloadImage((), ()), DeviceProgrammer(device)
    ).run()


@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("preview", [False, True])
async def test_captured_and_supplied_memory_overlay_inline_defaults(
    relative, preview
) -> None:
    from xknxeditor.download.image import MemorySegment, ObjectMemorySegment
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_mem_t import (
        LdCtrlLoadImageMem,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_rel_mem_t import (
        LdCtrlLoadImageRelMem,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem

    from .test_procedure import _application

    device = FakeDevice(MAPPING, descriptor=0x07B0)
    device.table_references[4] = 0x100
    device.memory = {0x100 + n: value for n, value in enumerate(b"ABCD")}
    if relative:
        capture = LdCtrlLoadImageRelMem(obj_idx=4, offset=0, size=3)
        write = LdCtrlWriteRelMem(
            obj_idx=4, offset=0, size=4, inline_data=b"xxxxPAD", verify=True
        )
        image = DownloadImage(
            (),
            (),
            application_segments=(ObjectMemorySegment(4, MemorySegment(1, b"Z")),),
        )
    else:
        capture = LdCtrlLoadImageMem(address=0x100, size=3)
        write = LdCtrlWriteMem(
            address=0x100, size=4, inline_data=b"xxxxPAD", verify=True
        )
        image = DownloadImage((MemorySegment(0x101, b"Z"),), ())
    runner = LoadProcedureRunner(
        _application(capture, write),
        image,
        DeviceProgrammer(device),
        object_types=MAPPING,
    )
    if preview:
        report = await runner.preflight()
        assert [(s.address, s.planned) for s in report.segments] == [(0x100, b"AZCx")]
        assert not any(isinstance(p, MemoryWrite) for p in device.sent)
    else:
        await runner.run()
        assert [
            (p.address, p.data) for p in device.sent if isinstance(p, MemoryWrite)
        ] == [(0x100, b"AZCx")]


async def test_memory_captures_are_keyed_by_address_space() -> None:
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_mem_t import (
        LdCtrlLoadImageMem,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_mem_addr_space_t import (
        LdCtrlMemAddrSpace,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem

    from .test_procedure import _application

    device = FakeDevice()
    device.memory = {0x100: ord("A")}
    controls = [
        LdCtrlLoadImageMem(
            address=0x100, size=1, address_space=LdCtrlMemAddrSpace.LC_SLAVE
        ),
        LdCtrlWriteMem(address=0x100, size=1, inline_data=b"X", verify=True),
        LdCtrlWriteMem(
            address=0x100,
            size=1,
            inline_data=b"Y",
            verify=True,
            address_space=LdCtrlMemAddrSpace.LC_SLAVE,
        ),
    ]
    await LoadProcedureRunner(
        _application(*controls), DownloadImage((), ()), DeviceProgrammer(device)
    ).run()
    assert [p.data for p in device.sent if isinstance(p, MemoryWrite)] == [b"X", b"A"]


@pytest.mark.parametrize(
    "reset,first_wildcard", [(True, False), (False, True), (False, False)]
)
async def test_error_mapping_reset_and_registration_order(
    reset, first_wildcard
) -> None:
    from xknxeditor.download.errors import PropertyAccessRejected
    from xknxeditor.namespaces.intermediate.ld_ctrl_map_error_t import LdCtrlMapError
    from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload

    from .test_procedure import _application

    original, remapped = 0xC0042B08, 0xC0042B28
    wildcard = LdCtrlMapError(original_error=original, mapped_error=0)
    specific = LdCtrlMapError(
        ld_ctrl_filter=5, original_error=original, mapped_error=remapped
    )
    controls = [wildcard, specific] if first_wildcard else [specific, wildcard]
    if reset:
        controls.append(
            LdCtrlMapError(
                ld_ctrl_filter=5, original_error=original, mapped_error=original
            )
        )
    controls.append(LdCtrlUnload(lsm_idx=4))
    device = FakeDevice(MAPPING)
    device.absent_objects = {4}
    runner = LoadProcedureRunner(
        _application(*controls),
        DownloadImage((), ()),
        DeviceProgrammer(device),
        object_types=MAPPING,
    )
    if reset or first_wildcard:
        await runner.run()
    else:
        with pytest.raises(PropertyAccessRejected) as caught:
            await runner.run()
        assert caught.value.error_code == remapped
