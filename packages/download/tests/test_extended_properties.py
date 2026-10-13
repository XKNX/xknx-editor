"""Property controls addressed by object type over the extended property services."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from xknx.telegram import Telegram
from xknx.telegram.apci import (
    APCI,
    PropertyDescriptionRead,
    PropertyExtDescriptionRead,
    PropertyExtDescriptionResponse,
    PropertyExtValueRead,
    PropertyExtValueResponse,
    PropertyExtValueWriteCon,
    PropertyExtValueWriteConRes,
    PropertyValueRead,
    PropertyValueWrite,
    ReturnCode,
)

from xknxeditor.download.errors import PropertyAccessRejected
from xknxeditor.download.image import DownloadImage
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)
from xknxeditor.prod import Application

from .conftest import FakeDevice

_IP_OBJECT = 11
_NAME = b"\xa0\xa0" + b" " * 14


class ExtendedDevice(FakeDevice):
    """Answers the extended property services for the KNXnet/IP object only, like the
    device of the recording: its index based description of property 201 is empty."""

    def __init__(self, return_code: ReturnCode = ReturnCode.E_SUCCESS) -> None:
        # The object list of the recorded device.
        super().__init__(
            object_types={0: 0, 1: 1, 2: 2, 3: 9, 4: 3, 5: _IP_OBJECT, 6: 8}
        )
        self.ext_values: dict[tuple[int, int, int], bytes] = {}
        self.return_code = return_code

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        if isinstance(payload, PropertyExtDescriptionRead):
            self.sent.append(payload)
            return self._telegram(
                PropertyExtDescriptionResponse(
                    interface_object_type=payload.interface_object_type,
                    object_instance=payload.object_instance,
                    property_id=payload.property_id,
                    property_index=4,
                    writable=True,
                    pdt=2,
                    max_nr_of_elem=16,
                )
            )
        if isinstance(payload, PropertyExtValueWriteCon):
            self.sent.append(payload)
            key = (
                payload.interface_object_type,
                payload.object_instance,
                payload.property_id,
            )
            self.ext_values[key] = payload.data
            return self._telegram(
                PropertyExtValueWriteConRes(
                    interface_object_type=payload.interface_object_type,
                    object_instance=payload.object_instance,
                    property_id=payload.property_id,
                    return_code=self.return_code,
                    nr_of_elem=payload.nr_of_elem,
                    start_index=payload.start_index,
                )
            )
        if isinstance(payload, PropertyExtValueRead):
            self.sent.append(payload)
            key = (
                payload.interface_object_type,
                payload.object_instance,
                payload.property_id,
            )
            return self._telegram(
                PropertyExtValueResponse(
                    interface_object_type=payload.interface_object_type,
                    object_instance=payload.object_instance,
                    property_id=payload.property_id,
                    nr_of_elem=payload.nr_of_elem,
                    start_index=payload.start_index,
                    data=self.ext_values.get(key, bytes(16)),
                )
            )
        return await super().request(payload, expected)


def _application(*, extended: bool) -> Application:
    control = LdCtrlWriteProp(
        obj_type=_IP_OBJECT,
        prop_id=201,
        count=16,
        verify=True,
        inline_data=_NAME,
    )
    procedure = LoadProceduresLoadProcedure(choice=[control])  # type: ignore[list-item]
    return cast(
        "Application",
        SimpleNamespace(
            load_procedures=LoadProcedures(load_procedure=[procedure]),
            load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
            manufacturer_id="M-00FA",
            program=SimpleNamespace(
                pei_type=0,
                application_number=0xA502,
                application_version=0x13,
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


def _runner(device: FakeDevice, *, extended: bool) -> LoadProcedureRunner:
    return LoadProcedureRunner(
        _application(extended=extended),
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device, max_apdu_length=50),
    )


async def test_object_type_is_written_over_extended_services() -> None:
    device = ExtendedDevice()
    await _runner(device, extended=True).run()

    assert device.ext_values[(_IP_OBJECT, 1, 201)] == _NAME
    (description,) = [
        p for p in device.sent if isinstance(p, PropertyExtDescriptionRead)
    ]
    assert (description.interface_object_type, description.object_instance) == (
        _IP_OBJECT,
        1,
    )
    # Neither the object list nor index based property services are used.
    assert not any(
        isinstance(p, PropertyValueRead | PropertyValueWrite | PropertyDescriptionRead)
        for p in device.sent
    )


async def test_preflight_reads_over_extended_services() -> None:
    device = ExtendedDevice()
    device.ext_values[(_IP_OBJECT, 1, 201)] = bytes(16)

    report = await _runner(device, extended=True).preflight()

    (prop,) = report.properties
    assert (prop.object_type, prop.object_label, prop.property_id) == (
        _IP_OBJECT,
        "type 11",
        201,
    )
    assert prop.current == bytes(16) and prop.planned == _NAME
    assert not any(isinstance(p, PropertyExtValueWriteCon) for p in device.sent)


async def test_rejected_extended_write_fails_the_download() -> None:
    device = ExtendedDevice(return_code=ReturnCode.E_ACCESS_DENIED)
    with pytest.raises(PropertyAccessRejected, match="E_ACCESS_DENIED"):
        await _runner(device, extended=True).run()


async def test_without_the_option_the_object_index_is_used() -> None:
    device = ExtendedDevice()
    await _runner(device, extended=False).run()

    assert not any(
        isinstance(p, PropertyExtDescriptionRead | PropertyExtValueWriteCon)
        for p in device.sent
    )
    write = next(p for p in device.sent if isinstance(p, PropertyValueWrite))
    assert (write.object_index, write.property_id) == (5, 201)
