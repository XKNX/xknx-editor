"""Runtime property-image regression from the MV-07B0 par hardware trace."""

from __future__ import annotations

import logging
from copy import deepcopy

import pytest
from xknx.telegram import Telegram
from xknx.telegram.apci import (
    APCI,
    MemoryRead,
    MemoryWrite,
    PropertyValueRead,
    PropertyValueResponse,
    PropertyValueWrite,
)

from xknxeditor.download.errors import (
    PropertyAccessRejected,
    UnsupportedProcedureError,
    VerificationError,
)
from xknxeditor.download.image import DownloadImage, PropertyValue
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.merge import resolve_download_controls
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
    LdCtrlLoadImageProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_map_error_t import LdCtrlMapError
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures

from .conftest import FakeDevice
from .test_group_communication import MAPPING, mask_fixture
from .test_merge import _fragment
from .test_procedure import _application


class PropertyDevice(FakeDevice):
    """Serve explicitly sized/ranged property elements, including 4-byte PID 7."""

    def __init__(self) -> None:
        super().__init__(object_types=MAPPING, descriptor=0x07B0)
        self.elements: dict[tuple[int, int, int], bytes] = {}
        self.move_reference = False
        self.capture_count = 1
        # The elements say how wide a property's elements are.
        del self.property_element_sizes[4, 50]

    def _element_size(self, object_index: int, property_id: int) -> int:
        element = self.elements.get((object_index, property_id, 1))
        return (
            len(element)
            if element
            else super()._element_size(object_index, property_id)
        )

    def _handle_property_read(
        self, payload: PropertyValueRead
    ) -> PropertyValueResponse:
        key = (payload.object_index, payload.property_id, payload.start_index)
        if key not in self.elements:
            return super()._handle_property_read(payload)
        return PropertyValueResponse(
            object_index=payload.object_index,
            property_id=payload.property_id,
            count=payload.count if self.capture_count else 0,
            start_index=payload.start_index,
            data=b"".join(
                self.elements[(payload.object_index, payload.property_id, element)]
                for element in range(
                    payload.start_index, payload.start_index + payload.count
                )
            ),
        )

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        if isinstance(payload, PropertyValueWrite) and payload.property_id == 27:
            self.sent.append(payload)
            self._handle_property_write(payload)
            if self.move_reference:
                self.elements[(4, 7, 1)] = bytes.fromhex("00004804")
            return self._telegram(
                PropertyValueResponse(
                    object_index=payload.object_index,
                    property_id=27,
                    count=0,
                    start_index=payload.start_index,
                    data=b"",
                )
            )
        return await super().request(payload, expected)


def par_runner(device: PropertyDevice) -> tuple[LoadProcedureRunner, list[object]]:
    application, master = mask_fixture()
    application.load_procedures = LoadProcedures(
        load_procedure=[
            _fragment(
                2,
                LdCtrlRelSegment(lsm_idx=4, size=16, mode=1, fill=0),
                LdCtrlRelSegment(lsm_idx=4, size=16, mode=0, fill=0),
                LdCtrlWriteProp(
                    obj_idx=4,
                    prop_id=27,
                    verify=False,
                    inline_data=bytes.fromhex("00000010003247c90000"),
                ),
            ),
            _fragment(
                4, LdCtrlWriteMem(address=0x3804, size=1, verify=True, inline_data=b"P")
            ),
            _fragment(
                6, LdCtrlWriteMem(address=0x4000, size=1, verify=True, inline_data=b"A")
            ),
        ]
    )
    controls = resolve_download_controls(
        application, master, scope=DownloadScope.PARAMETERS
    )
    assert len(controls) == 12
    assert isinstance(controls[6], LdCtrlCompareProp)
    assert not any(isinstance(c, LdCtrlMapError) for c in controls)
    return LoadProcedureRunner(
        application,
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device),
        controls=controls,
        scope=DownloadScope.PARAMETERS,
        object_types=MAPPING,
    ), controls


async def test_par_captures_reference_before_allocation_and_completes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    device = PropertyDevice()
    device.elements = {(4, 7, 1): bytes.fromhex("00003804"), (5, 7, 1): bytes(4)}
    runner, controls = par_runner(device)
    original = deepcopy(controls)
    with caplog.at_level(logging.DEBUG):
        await runner.run()
    assert device.load_states == {4: LoadState.LOADED}
    assert device.memory[0x3804] == ord("P")
    assert device.memory[0x4000] == ord("A")
    assert device.restarted
    assert controls == original
    assert runner.image.properties == ()
    assert "control=(2, 12) kind=LdCtrlLoadImageProp" in caplog.text
    assert "octets=4 data=00003804" in caplog.text
    assert "control=(7, 12) kind=LdCtrlCompareProp" in caplog.text
    assert "source=inline+captured" in caplog.text
    assert "expected=00003804 read=00003804" in caplog.text
    assert "zero-element confirmation accepted; storage not verified" in caplog.text
    assert (device.sent[0].object_index, device.sent[0].property_id) == (4, 7)
    assert not any(getattr(p, "object_index", None) == 5 for p in device.sent)


async def test_par_still_fails_if_allocation_moves_reference() -> None:
    device = PropertyDevice()
    device.elements = {(4, 7, 1): bytes.fromhex("00003804"), (5, 7, 1): bytes(4)}
    device.move_reference = True
    runner, _ = par_runner(device)
    with pytest.raises(VerificationError, match="expected 00003804 read 00004804"):
        await runner.run()
    assert device.load_states == {4: LoadState.LOADING}
    assert not device.restarted
    assert not any(isinstance(p, MemoryWrite) for p in device.sent)


async def test_par_preflight_captures_without_writes_and_run_recaptures() -> None:
    device = PropertyDevice()
    device.elements = {(4, 7, 1): bytes.fromhex("00003804"), (5, 7, 1): bytes(4)}
    runner, _ = par_runner(device)
    await runner.preflight()
    assert all(isinstance(p, PropertyValueRead | MemoryRead) for p in device.sent)
    assert not runner.state_mutated
    device.elements[(4, 7, 1)] = bytes.fromhex("00004804")
    await runner.run()
    # A second run must not reuse the first run's captures either.
    device.elements[(4, 7, 1)] = bytes.fromhex("00005804")
    await runner.run()


def capture_runner(
    device: PropertyDevice, *controls: object, image: DownloadImage | None = None
) -> LoadProcedureRunner:
    return LoadProcedureRunner(
        _application(*controls),
        image or DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device),
        object_types=MAPPING,
    )


@pytest.mark.parametrize("capture", [False, True])
async def test_ap1_runtime_descriptor_ignores_static_pid7(capture: bool) -> None:
    # The ap1 wire capture: PID7 is read as 00003804; PID27 is written
    # as 0000001000320000 and echoed. No static PID7 value is written/verified.
    class CapturedDevice(FakeDevice):
        def _handle_property_read(
            self, payload: PropertyValueRead
        ) -> PropertyValueResponse:
            response = super()._handle_property_read(payload)
            if payload.object_index == 4 and payload.property_id == 7:
                response.data = bytes.fromhex("00003804")
            return response

    device = CapturedDevice(object_types=MAPPING, descriptor=0x07B0)
    mcb = bytes.fromhex("0000001000320000")
    controls: list[object] = (
        [LdCtrlLoadImageProp(obj_idx=4, prop_id=7)] if capture else []
    )
    controls.extend(
        [
            LdCtrlUnload(lsm_idx=4),
            LdCtrlLoad(lsm_idx=4),
            LdCtrlRelSegment(lsm_idx=4, size=0x10, mode=1, fill=0),
            LdCtrlCompareProp(obj_idx=4, prop_id=7, inline_data=bytes(4)),
            LdCtrlWriteProp(obj_idx=4, prop_id=27, inline_data=mcb, verify=True),
            LdCtrlLoadCompleted(lsm_idx=4),
        ]
    )
    application, _ = mask_fixture()
    runner = LoadProcedureRunner(
        application,
        DownloadImage(segments=(), properties=(PropertyValue(4, 7, 0, bytes(4)),)),
        DeviceProgrammer(device),
        controls=controls,
        object_types=MAPPING,
    )

    await runner.run()

    assert device.load_states[4] == LoadState.LOADED
    assert device.properties[(4, 27)] == mcb
    assert any(
        isinstance(p, PropertyValueRead) and p.object_index == 4 and p.property_id == 7
        for p in device.sent
    )
    assert not any(
        isinstance(p, PropertyValueWrite) and p.property_id == 7 for p in device.sent
    )
    # Verify the write response directly while loading.
    assert not any(
        isinstance(p, PropertyValueRead) and p.property_id == 27 for p in device.sent
    )


@pytest.mark.parametrize(
    ("index", "reference"),
    [(4, "00003804"), (3, "00003604"), (2, "00003502"), (1, "00003400")],
)
async def test_runtime_descriptor_without_capture_reads_captured_bytes(
    index: int,
    reference: str,
) -> None:
    device = PropertyDevice()
    device.elements[(index, 7, 1)] = bytes.fromhex(reference)
    runner = capture_runner(
        device, LdCtrlCompareProp(obj_idx=index, prop_id=7, inline_data=bytes(4))
    )

    await runner.preflight()
    await runner.run()

    assert len(device.sent) == 2
    assert all(isinstance(p, PropertyValueRead) for p in device.sent)


@pytest.mark.parametrize("invalid", [b"", b"\x00", bytes(3)])
async def test_runtime_descriptor_read_rejects_malformed_data(invalid: bytes) -> None:
    device = PropertyDevice()
    device.elements[(4, 7, 1)] = invalid
    with pytest.raises(VerificationError):
        await capture_runner(
            device, LdCtrlCompareProp(obj_idx=4, prop_id=7, inline_data=bytes(4))
        ).run()


@pytest.mark.parametrize(
    ("pid", "start", "actual", "expected"),
    [
        (5, 1, "02", "01"),
        (27, 1, "00000010003247c9", "0000001000320000"),
        (7, 0, "0001", "0000"),
    ],
)
async def test_runtime_descriptor_exception_keeps_other_compares_strict(
    pid: int,
    start: int,
    actual: str,
    expected: str,
) -> None:
    device = PropertyDevice()
    device.elements[(4, pid, start)] = bytes.fromhex(actual)
    with pytest.raises(VerificationError, match="property compare failed"):
        await capture_runner(
            device,
            LdCtrlCompareProp(
                obj_idx=4,
                prop_id=pid,
                start_element=start,
                inline_data=bytes.fromhex(expected),
            ),
        ).run()


async def test_verified_mcb_write_still_rejects_mismatch() -> None:
    class MismatchedMcbDevice(FakeDevice):
        def _handle_property_read(
            self, payload: PropertyValueRead
        ) -> PropertyValueResponse:
            response = super()._handle_property_read(payload)
            if payload.property_id == 27:
                response.data = bytes.fromhex("00000010003247c9")
            return response

    runner = LoadProcedureRunner(
        _application(
            LdCtrlWriteProp(
                obj_idx=4,
                prop_id=27,
                verify=True,
                inline_data=bytes.fromhex("0000001000320000"),
            )
        ),
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(MismatchedMcbDevice()),
    )
    with pytest.raises(VerificationError, match="property verification failed"):
        await runner.run()


@pytest.mark.parametrize(
    "reference", [bytes.fromhex("3804"), bytes.fromhex("00003804"), bytes(4)]
)
async def test_capture_keeps_raw_reference_width_and_resolves_type_to_index(
    reference: bytes,
) -> None:
    device = PropertyDevice()
    device.elements[(4, 7, 1)] = reference
    await capture_runner(
        device,
        LdCtrlLoadImageProp(obj_idx=4, prop_id=7),
        LdCtrlCompareProp(obj_type=3, prop_id=7, inline_data=bytes(4)),
    ).run()


@pytest.mark.parametrize("invalid", [b"", b"\x00", bytes(3)])
async def test_invalid_capture_fails_before_unload(invalid: bytes) -> None:
    device = PropertyDevice()
    device.elements[(4, 7, 1)] = invalid
    runner = capture_runner(
        device, LdCtrlLoadImageProp(obj_idx=4, prop_id=7), LdCtrlUnload(lsm_idx=4)
    )
    with pytest.raises(VerificationError, match="capture"):
        await runner.run()
    assert not runner.state_mutated
    assert not any(isinstance(p, PropertyValueWrite) for p in device.sent)


async def test_capture_is_required_but_ordinary_read_remains_optional() -> None:
    device = PropertyDevice()
    device.elements[(4, 7, 1)] = b""
    device.capture_count = 0
    assert await DeviceProgrammer(device).read_property(4, 7) == b""
    with pytest.raises(PropertyAccessRejected, match="returned 0 elements"):
        await capture_runner(
            device, LdCtrlLoadImageProp(obj_idx=4, prop_id=7), LdCtrlUnload(lsm_idx=4)
        ).run()
    assert not device.load_states


async def test_element_zero_is_separate_from_property_value() -> None:
    device = PropertyDevice()
    device.elements = {(4, 50, 0): b"\x00\x02", (4, 50, 1): b"ABCD"}
    await capture_runner(
        device,
        LdCtrlLoadImageProp(obj_idx=4, prop_id=50, start_element=0),
        LdCtrlLoadImageProp(obj_idx=4, prop_id=50),
        LdCtrlCompareProp(obj_idx=4, prop_id=50, start_element=0, inline_data=bytes(2)),
        LdCtrlCompareProp(obj_idx=4, prop_id=50, inline_data=bytes(4)),
    ).run()


async def test_capture_rejects_incomplete_element_range() -> None:
    device = PropertyDevice()
    device.elements = {(4, 50, 1): b"AB", (4, 50, 2): b"C"}
    with pytest.raises(VerificationError, match=r"3 octet.*2 element"):
        await capture_runner(
            device,
            LdCtrlLoadImageProp(obj_idx=4, prop_id=50, count=2),
            LdCtrlUnload(lsm_idx=4),
        ).run()
    assert not device.load_states


async def test_capture_extension_keeps_unmasked_suffix_checked() -> None:
    device = PropertyDevice()
    device.elements[(4, 50, 1)] = b"ABCD"
    runner = capture_runner(
        device,
        LdCtrlLoadImageProp(obj_idx=4, prop_id=50),
        LdCtrlCompareProp(obj_idx=4, prop_id=50, inline_data=bytes(2), mask=bytes(2)),
    )

    def change(done: int, total: int) -> None:
        if done == 1:
            device.elements[(4, 50, 1)] = b"XYCE"

    with pytest.raises(VerificationError, match="expected 41424344 read 58594345"):
        await runner.run(change)


async def test_capture_ranges_and_mask_keep_other_elements_inline() -> None:
    device = PropertyDevice()
    device.elements = {
        (4, 50, 1): b"aa",
        (4, 50, 2): b"\x10\x20",
        (4, 50, 3): b"\x30\x40",
    }
    runner = capture_runner(
        device,
        LdCtrlLoadImageProp(obj_idx=4, prop_id=50, start_element=2, count=2),
        LdCtrlCompareProp(
            obj_idx=4,
            prop_id=50,
            count=3,
            inline_data=b"aa\0\0\0\0",
            mask=b"\xff" * 5 + b"\xf0",
        ),
    )

    def change(done: int, total: int) -> None:
        if done == 1:
            device.elements[(4, 50, 3)] = b"\x30\x4f"

    await runner.run(change)


async def test_capture_does_not_hide_unmasked_mismatch_or_refresh_baseline() -> None:
    device = PropertyDevice()
    device.elements[(4, 50, 1)] = b"\x10\x20"
    runner = capture_runner(
        device,
        LdCtrlLoadImageProp(obj_idx=4, prop_id=50),
        LdCtrlLoadImageProp(obj_idx=4, prop_id=50),
        LdCtrlCompareProp(
            obj_idx=4, prop_id=50, inline_data=bytes(2), mask=b"\xff\xf0"
        ),
    )

    def change(done: int, total: int) -> None:
        if done == 1:
            device.elements[(4, 50, 1)] = b"\x10\x30"

    with pytest.raises(VerificationError, match="expected 1020 read 1030"):
        await runner.run(change)


async def test_captured_zero_bytes_cannot_pass_truncated_compare() -> None:
    device = PropertyDevice()
    device.elements[(4, 7, 1)] = bytes(4)
    runner = capture_runner(
        device,
        LdCtrlLoadImageProp(obj_idx=4, prop_id=7),
        LdCtrlCompareProp(obj_idx=4, prop_id=7, inline_data=bytes(4)),
    )

    def truncate(done: int, total: int) -> None:
        if done == 1:
            device.elements[(4, 7, 1)] = bytes(2)

    with pytest.raises(VerificationError, match="captured range requires 4"):
        await runner.run(truncate)


@pytest.mark.parametrize("capture", [False, True])
async def test_supplied_image_overrides_inline_and_capture(capture: bool) -> None:
    device = PropertyDevice()
    device.elements[(4, 50, 1)] = b"AB"
    controls: list[object] = (
        [LdCtrlLoadImageProp(obj_idx=4, prop_id=50)] if capture else []
    )
    controls.append(LdCtrlCompareProp(obj_idx=4, prop_id=50, inline_data=bytes(2)))
    runner = capture_runner(
        device,
        *controls,
        image=DownloadImage(
            segments=(),
            properties=(PropertyValue(4, 50, 0, b"A"),),
        ),
    )
    # Image covers only byte A; B comes from the capture, otherwise inline zero.
    if capture:
        device.elements[(4, 50, 1)] = b"XB"

        def update_after_capture(done: int, total: int) -> None:
            if done == 1:
                device.elements[(4, 50, 1)] = b"AB"

        await runner.run(update_after_capture)
    else:
        with pytest.raises(VerificationError, match="expected 4100 read 4142"):
            await runner.run()


async def test_literal_compare_is_not_overridden_by_another_object_or_range() -> None:
    device = PropertyDevice()
    device.elements = {(4, 50, 1): b"aa", (4, 50, 2): b"bb", (5, 50, 1): b"cc"}
    runner = capture_runner(
        device,
        LdCtrlLoadImageProp(obj_idx=4, prop_id=50, start_element=2),
        LdCtrlLoadImageProp(obj_idx=5, prop_id=50),
        LdCtrlCompareProp(obj_idx=4, prop_id=50, inline_data=bytes(2)),
    )
    with pytest.raises(VerificationError, match="expected 0000 read 6161"):
        await runner.run()


async def test_mcb_capture_is_split_to_fit_the_apdu() -> None:
    """A 7-element MCB read does not fit an APDU of 55 and is rejected whole."""

    class ShortApdu(PropertyDevice):
        def _handle_property_read(
            self, payload: PropertyValueRead
        ) -> PropertyValueResponse:
            if payload.property_id == 27 and payload.count * 8 > 50:
                return PropertyValueResponse(
                    object_index=payload.object_index,
                    property_id=payload.property_id,
                    count=0,
                    start_index=payload.start_index,
                    data=b"",
                )
            return super()._handle_property_read(payload)

    device = ShortApdu()
    device.elements = {
        (4, 27, element): bytes([element]) * 8 for element in range(1, 8)
    }
    runner = LoadProcedureRunner(
        _application(LdCtrlLoadImageProp(obj_idx=4, prop_id=27, count=7)),
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device, max_apdu_length=55),
        object_types=MAPPING,
    )
    await runner.run()
    reads = [
        p
        for p in device.sent
        if isinstance(p, PropertyValueRead) and p.property_id == 27
    ]
    assert [(p.start_index, p.count) for p in reads] == [(1, 6), (7, 1)]
    assert [runner._property_captures[4, 27, element] for element in range(1, 8)] == [  # pyright: ignore[reportPrivateUsage]
        bytes([element]) * 8 for element in range(1, 8)
    ]


@pytest.mark.parametrize("count", [0, 16])
async def test_unsupported_capture_range_is_rejected_before_any_io(count: int) -> None:
    device = PropertyDevice()
    with pytest.raises(UnsupportedProcedureError, match="fixed range"):
        await capture_runner(
            device,
            LdCtrlUnload(lsm_idx=4),
            LdCtrlLoadImageProp(obj_idx=4, prop_id=7, count=count),
        ).run()
    assert device.sent == []
