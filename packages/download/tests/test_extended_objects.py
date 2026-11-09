"""Interface objects past the one-octet object index, reachable by object type and
instance over the extended property services only."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from xknx.telegram import Telegram
from xknx.telegram.apci import (
    APCI,
    FunctionPropertyCommand,
    FunctionPropertyExtCommand,
    FunctionPropertyExtStateResponse,
    FunctionPropertyStateRead,
    PropertyDescriptionRead,
    PropertyExtDescriptionRead,
    PropertyExtDescriptionResponse,
    PropertyExtValueRead,
    PropertyExtValueResponse,
    PropertyExtValueWriteCon,
    PropertyExtValueWriteConRes,
    PropertyExtValueWriteUnCon,
    PropertyValueRead,
    PropertyValueWrite,
    ReturnCode,
)

from xknxeditor.download.errors import LoadStateError
from xknxeditor.download.image import DownloadImage
from xknxeditor.download.load_state import LoadState, start_loading
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import (
    DeviceProgrammer,
    extended_object,
    extended_object_index,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_declare_prop_desc_t import (
    LdCtrlDeclarePropDesc,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)
from xknxeditor.namespaces.intermediate.prop_type_t import PropType
from xknxeditor.prod import Application

from .conftest import FakeDevice

# A manufacturer's object type of which a module keeps one object per instance.
_RECORD = 50004
# The objects of the device: the system objects, then 300 of the module's.
_SYSTEM = {0: 0, 1: 1, 2: 2, 3: 3, 4: 11}
_RECORDS = 300


def _records_device_types() -> dict[int, int]:
    types = dict(_SYSTEM)
    for n in range(_RECORDS):
        types[len(_SYSTEM) + n] = _RECORD
    return types


class RecordsDevice(FakeDevice):
    """A device with more interface objects than the one-octet object index reaches; those
    past it answer the extended property services only (by type and instance)."""

    def __init__(self, object_types: dict[int, int] | None = None) -> None:
        super().__init__(object_types=object_types or _records_device_types())

    def _index(self, object_type: int, instance: int) -> int | None:
        same = [i for i, t in sorted(self.object_types.items()) if t == object_type]
        return same[instance - 1] if 0 < instance <= len(same) else None

    @staticmethod
    def _check_index(payload: APCI) -> None:
        index = getattr(payload, "object_index", None)
        if isinstance(index, int) and not 0 <= index <= 0xFF:
            raise AssertionError(f"object index {index} does not fit one octet")

    async def send_data(self, payload: APCI, wait_for_ack: bool = True) -> None:
        self._check_index(payload)
        if isinstance(payload, PropertyExtValueWriteUnCon):
            self.sent.append(payload)
            index = self._index(payload.interface_object_type, payload.object_instance)
            assert index is not None
            self._handle_property_write(
                PropertyValueWrite(
                    object_index=index,
                    property_id=payload.property_id,
                    count=payload.nr_of_elem,
                    start_index=payload.start_index,
                    data=payload.data,
                )
            )
            return
        await super().send_data(payload, wait_for_ack)

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        self._check_index(payload)
        if isinstance(payload, PropertyExtValueRead):
            self.sent.append(payload)
            index = self._index(payload.interface_object_type, payload.object_instance)
            response = (
                self._handle_property_read(
                    PropertyValueRead(
                        object_index=index,
                        property_id=payload.property_id,
                        count=payload.nr_of_elem,
                        start_index=payload.start_index,
                    )
                )
                if index is not None
                else None
            )
            data = response.data if response is not None else b""
            return self._telegram(
                PropertyExtValueResponse(
                    interface_object_type=payload.interface_object_type,
                    object_instance=payload.object_instance,
                    property_id=payload.property_id,
                    nr_of_elem=payload.nr_of_elem if data else 0,
                    start_index=payload.start_index,
                    data=data,
                )
            )
        if isinstance(payload, PropertyExtValueWriteCon):
            self.sent.append(payload)
            index = self._index(payload.interface_object_type, payload.object_instance)
            if index is not None:
                self._handle_property_write(
                    PropertyValueWrite(
                        object_index=index,
                        property_id=payload.property_id,
                        count=payload.nr_of_elem,
                        start_index=payload.start_index,
                        data=payload.data,
                    )
                )
            return self._telegram(
                PropertyExtValueWriteConRes(
                    interface_object_type=payload.interface_object_type,
                    object_instance=payload.object_instance,
                    property_id=payload.property_id,
                    return_code=ReturnCode.E_SUCCESS
                    if index is not None
                    else ReturnCode.E_ADDRESS_VOID,
                    nr_of_elem=payload.nr_of_elem,
                    start_index=payload.start_index,
                )
            )
        if isinstance(payload, PropertyExtDescriptionRead):
            self.sent.append(payload)
            index = self._index(payload.interface_object_type, payload.object_instance)
            assert index is not None
            width = self.property_element_sizes.get(
                (index, payload.property_id)
            ) or self._element_size(index, payload.property_id)
            return self._telegram(
                PropertyExtDescriptionResponse(
                    interface_object_type=payload.interface_object_type,
                    object_instance=payload.object_instance,
                    property_id=payload.property_id,
                    property_index=1,
                    writable=True,
                    pdt=16 + width,
                    max_nr_of_elem=64,
                )
            )
        if isinstance(payload, FunctionPropertyExtCommand):
            self.sent.append(payload)
            index = self._index(payload.interface_object_type, payload.object_instance)
            assert index is not None
            self.function_properties[(index, payload.property_id)] = payload.data
            return self._telegram(
                FunctionPropertyExtStateResponse(
                    interface_object_type=payload.interface_object_type,
                    object_instance=payload.object_instance,
                    property_id=payload.property_id,
                    return_code=ReturnCode.E_SUCCESS,
                    data=b"\x07",
                )
            )
        return await super().request(payload, expected)

    def ext_sent(self) -> list[APCI]:
        """The extended property services sent."""
        return [
            p
            for p in self.sent
            if isinstance(
                p,
                PropertyExtValueRead
                | PropertyExtValueWriteCon
                | PropertyExtValueWriteUnCon
                | PropertyExtDescriptionRead
                | FunctionPropertyExtCommand,
            )
        ]


def test_extended_object_index_round_trips() -> None:
    index = extended_object_index(_RECORD, 4095)
    assert index > 0xFF
    assert extended_object(index) == (_RECORD, 4095)
    assert extended_object(0xFF) is None
    assert extended_object(0) is None


async def test_an_object_past_the_one_octet_index_is_located_by_type_and_instance() -> (
    None
):
    device = RecordsDevice()
    programmer = DeviceProgrammer(device)

    # The 251st object of the type is at index 255, the last the index reaches.
    assert await programmer.locate_object(_RECORD, 250) == 255
    index = await programmer.locate_object(_RECORD, 251)

    assert extended_object(index) == (_RECORD, 252)
    probe = device.ext_sent()[-1]
    assert isinstance(probe, PropertyExtValueRead)
    assert (probe.interface_object_type, probe.object_instance) == (_RECORD, 252)
    # Cached like an object index.
    sent = len(device.sent)
    assert await programmer.locate_object(_RECORD, 251) == index
    assert len(device.sent) == sent


async def test_an_absent_instance_past_the_one_octet_index_is_not_found() -> None:
    programmer = DeviceProgrammer(RecordsDevice())
    with pytest.raises(LoadStateError, match="occurrence 300 not found"):
        await programmer.locate_object(_RECORD, _RECORDS)


async def test_a_device_with_fewer_objects_is_not_asked_by_type() -> None:
    device = RecordsDevice(object_types=dict(_SYSTEM))
    programmer = DeviceProgrammer(device)
    with pytest.raises(LoadStateError, match="not found"):
        await programmer.locate_object(_RECORD)
    assert device.ext_sent() == []


async def test_properties_of_an_object_past_the_index_go_over_extended_services() -> (
    None
):
    device = RecordsDevice()
    programmer = DeviceProgrammer(device, max_apdu_length=50)
    index = await programmer.locate_object(_RECORD, 299)
    device_index = len(_SYSTEM) + 299

    await programmer.write_property(index, 51, b"\x01\x02\x03", count=3)
    assert device.properties[(device_index, 51)] == b"\x01\x02\x03"
    assert await programmer.read_property(index, 51, count=3) == b"\x01\x02\x03"
    assert await programmer.read_property_element_size(index, 51) == 1
    await programmer.write_property(index, 52, b"\x09", verify=False)
    assert device.properties[(device_index, 52)] == b"\x09"
    assert await programmer.invoke_function_property(index, 60, b"\x01") == b"\x07"
    assert device.function_properties[(device_index, 60)] == b"\x01"
    assert isinstance(device.sent[-1], FunctionPropertyExtCommand)
    assert not any(
        isinstance(p, FunctionPropertyCommand | FunctionPropertyStateRead)
        for p in device.sent
    )


async def test_a_load_event_on_an_object_past_the_index_reads_its_state_back() -> None:
    device = RecordsDevice()
    # A load event (10 octets) and the extended frame's header need more than 15 octets.
    programmer = DeviceProgrammer(device, max_apdu_length=50)
    index = await programmer.locate_object(_RECORD, 280)

    await programmer.send_load_event(index, start_loading(), LoadState.LOADING)

    assert device.load_states[len(_SYSTEM) + 280] == LoadState.LOADING
    assert isinstance(device.ext_sent()[-2], PropertyExtValueWriteCon)


def _application(occurrence: int, *, extended: bool) -> Application:
    controls = [
        LdCtrlDeclarePropDesc(
            obj_type=_RECORD,
            occurrence=occurrence,
            prop_id=53,
            prop_type=PropType.PDT_UNSIGNED_INT,
            max_elements=1,
            read_access=3,
            write_access=2,
            writable=True,
        ),
        LdCtrlWriteProp(
            obj_type=_RECORD,
            occurrence=occurrence,
            prop_id=53,
            verify=True,
            inline_data=b"\x12\x34",
        ),
    ]
    procedure = LoadProceduresLoadProcedure(choice=controls)  # type: ignore[arg-type]
    return cast(
        "Application",
        SimpleNamespace(
            load_procedures=LoadProcedures(load_procedure=[procedure]),
            load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
            manufacturer_id="M-0008",
            program=SimpleNamespace(
                pei_type=0,
                application_number=0x0004,
                application_version=0x73,
                mask_version="MV-07B0",
                is_secure_enabled=False,
                static=SimpleNamespace(
                    options=SimpleNamespace(
                        supports_extended_property_services=extended
                    )
                ),
            ),
        ),
    )


def _runner(
    device: FakeDevice, occurrence: int, *, extended: bool
) -> LoadProcedureRunner:
    return LoadProcedureRunner(
        _application(occurrence, extended=extended),
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device, max_apdu_length=50),
    )


@pytest.mark.parametrize("extended", [True, False])
async def test_a_declared_property_past_the_index_is_written(extended: bool) -> None:
    device = RecordsDevice()

    await _runner(device, 290, extended=extended).run()

    assert device.properties[(len(_SYSTEM) + 290, 53)] == b"\x12\x34"
    (write,) = [p for p in device.sent if isinstance(p, PropertyExtValueWriteCon)]
    assert (write.interface_object_type, write.object_instance) == (_RECORD, 291)
    # The declared width is taken: the device is not asked for the description.
    assert not any(
        isinstance(p, PropertyExtDescriptionRead | PropertyDescriptionRead)
        for p in device.sent
    )


async def test_a_declared_property_of_an_extended_application_is_not_located() -> None:
    device = RecordsDevice()

    await _runner(device, 3, extended=True).run()

    assert device.properties[(len(_SYSTEM) + 3, 53)] == b"\x12\x34"
    # Addressed by type and instance: the object list is not read.
    assert not any(isinstance(p, PropertyValueRead) for p in device.sent)


async def test_without_the_option_an_object_the_index_reaches_keeps_its_index() -> None:
    device = RecordsDevice()

    await _runner(device, 3, extended=False).run()

    write = next(p for p in device.sent if isinstance(p, PropertyValueWrite))
    assert (write.object_index, write.property_id) == (len(_SYSTEM) + 3, 53)
    assert device.ext_sent() == []


@pytest.mark.parametrize("extended", [True, False])
async def test_the_preview_shows_an_object_past_the_index_by_type(
    extended: bool,
) -> None:
    device = RecordsDevice()

    report = await _runner(device, 290, extended=extended).preflight()

    (prop,) = report.properties
    assert (prop.object_type, prop.property_id) == (_RECORD, 53)
    assert prop.current == b"" or prop.current == bytes(2)
    assert prop.planned == b"\x12\x34"
    assert not any(isinstance(p, PropertyExtValueWriteCon) for p in device.sent)
