"""A mask that numbers a load state machine in the download image other than on the device
(the load control's image location against its location): the load events go to the device's
object, where the load state is read, and a machine only the image has gets none."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from xknx.telegram.apci import PropertyValueWrite

from xknxeditor.download import resources as resources_module
from xknxeditor.download.image import DownloadImage, MemorySegment
from xknxeditor.download.load_state import PID_LOAD_STATE_CONTROL, LoadState
from xknxeditor.download.partial import application_id, partial_download_mismatch
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.resources import (
    MaskResources,
    bundled_mask_resources,
    mask_resources,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)
from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_location_t import ResourceLocation
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName
from xknxeditor.prod import Application
from xknxeditor.prod.master import parse_master_xml

from .conftest import FakeDevice

# A made-up mask: the association table's machine is object 6 on the device and 2 in the
# image, the application's 7 and 3; the PEI program's machine is in the image only (4).
_MASK = "MV-0300-09000000000000000000"
_PROPERTY = "SystemProperty"
_TABLE = {
    _MASK: {
        "features": {"PropertyMappedLsms": 1},
        # The mask's memory; the malformed rows are skipped.
        "memory": [[256, 512], [1024], [2048, 0], "x"],
        "resources": {
            "GroupAddressTableLoadControl": [
                _PROPERTY,
                1,
                5,
                0,
                "LoadControl_Bcu2",
                10,
            ],
            "GroupAssociationTableLoadControl": [
                *[_PROPERTY, 6, 5, 0, "LoadControl_Bcu2", 10],
                *[_PROPERTY, 2, 5, 0],
            ],
            "ApplicationLoadControl": [
                *[_PROPERTY, 7, 5, 0, "LoadControl_Bcu2", 10],
                *[_PROPERTY, 3, 5, 0],
            ],
            "ApplicationLoadStatus": [_PROPERTY, 7, 5, 0, "LoadControl_Bcu2", 1],
            "ApplicationId": [_PROPERTY, 7, 13, 0, None, 5],
            "PeiprogLoadControl": [
                *[None, None, None, None, "LoadControl_Bcu2", 10],
                *[_PROPERTY, 4, 5, 0],
            ],
        },
    }
}


@pytest.fixture(autouse=True)
def _hand_made_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resources_module, "_bundled_table", lambda: _TABLE)


def _application(*controls: object) -> Application:
    procedure = LoadProceduresLoadProcedure(choice=list(controls))  # type: ignore[arg-type]
    fake = SimpleNamespace(
        id="M-00FD_A-TEST-01-0001",
        load_procedures=LoadProcedures(load_procedure=[procedure]),
        load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
        manufacturer_id="M-00FD",
        program=SimpleNamespace(
            pei_type=0,
            application_number=0x9001,
            application_version=0x11,
            mask_version=_MASK,
            static=SimpleNamespace(options=None),
        ),
    )
    return cast(Application, fake)


def _load_state_writes(device: FakeDevice) -> list[int]:
    return [
        payload.object_index
        for payload in device.sent
        if isinstance(payload, PropertyValueWrite)
        and payload.property_id == PID_LOAD_STATE_CONTROL
    ]


def test_the_device_object_of_a_load_state_machine() -> None:
    resources = bundled_mask_resources(_MASK)
    assert resources is not None
    # Image numbering to the device's objects; the device's own numbering stays.
    assert [resources.lsm_object(index) for index in (1, 2, 3, 6, 7, 5)] == [
        *[1, 6, 7],
        *[6, 7, 5],
    ]
    # The PEI program's machine is in the image only.
    assert resources.lsm_object(4) == 4
    assert resources.omits_load_event(4)
    assert not any(resources.omits_load_event(index) for index in (1, 3, 5, 6, 7))


def test_a_mask_without_image_locations_keeps_the_numbering() -> None:
    plain = MaskResources(
        {
            ResourceName.APPLICATION_LOAD_CONTROL: ResourceLocation(
                address_space=ResourceAddrSpace.SYSTEM_PROPERTY,
                interface_object_ref=4,
                property_id=5,
                start_address=0,
            )
        }
    )
    assert [plain.lsm_object(index) for index in range(6)] == list(range(6))
    assert not plain.omits_load_event(4)


async def test_load_events_go_where_the_load_state_is_read() -> None:
    segment = bytes(range(1, 9))
    application = _application(
        LdCtrlUnload(lsm_idx=3),
        LdCtrlUnload(lsm_idx=2),
        LdCtrlUnload(lsm_idx=4),
        LdCtrlLoad(lsm_idx=2),
        LdCtrlLoadCompleted(lsm_idx=2),
        LdCtrlLoad(lsm_idx=4),
        LdCtrlAbsSegment(
            lsm_idx=4,
            seg_type=0,
            address=0x0124,
            size=8,
            access=0x33,
            mem_type=3,
            seg_flags=0x83,
        ),
        LdCtrlLoadCompleted(lsm_idx=4),
        LdCtrlLoad(lsm_idx=3),
        LdCtrlTaskSegment(lsm_idx=3, address=0),
        LdCtrlLoadCompleted(lsm_idx=3),
    )
    image = DownloadImage(
        segments=(MemorySegment(address=0x0124, data=segment),), properties=()
    )
    device = FakeDevice(descriptor=0x0300)
    await LoadProcedureRunner(application, image, DeviceProgrammer(device)).run()

    # Every load event reached the device's machines (association table 6, application 7);
    # none went to the image's numbers, and none to the PEI program the device lacks.
    assert set(_load_state_writes(device)) == {6, 7}
    assert device.load_states[6] == LoadState.LOADED
    assert device.load_states[7] == LoadState.LOADED
    # The segment of the image-only machine is written all the same.
    assert bytes(device.memory[0x0124 + i] for i in range(8)) == segment
    # A partial download reads the load state where the full one left it.
    device.properties[7, 13] = application_id(application)
    resources = mask_resources(None, _MASK)
    programmer = DeviceProgrammer(device)
    assert await partial_download_mismatch(programmer, application, resources) is None


def test_the_bundled_mask_memory() -> None:
    resources = bundled_mask_resources(_MASK)
    assert resources is not None
    assert resources.memory() == ((256, 512),)


def test_the_master_data_maps_load_state_machines_and_memory() -> None:
    def load_control(name: str, location: bytes, image: bytes) -> bytes:
        return (
            b'<Resource Name="'
            + name.encode()
            + b'" Access="remote">'
            + location
            + b'<ImgLocation AddressSpace="SystemProperty" InterfaceObjectRef="'
            + image
            + b'" PropertyID="5" StartAddress="0" />'
            b'<ResourceType Length="10" Flavour="LoadControl_Bcu2" />'
            b'<AccessRights Read="None" Write="Configuration" /></Resource>'
        )

    master = parse_master_xml(
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<KNX xmlns="http://knx.org/xml/project/20">'
        b'<MasterData Id="M" Version="1" Signature="AA=="><MaskVersions>'
        b'<MaskVersion Id="' + _MASK.encode() + b'" MaskVersion="768" Name="T"'
        b' ManagementModel="PropertyBased" MediumTypeRefId="MT-0">'
        b"<HawkConfigurationData><Resources>"
        + load_control(
            "ApplicationLoadControl",
            b'<Location AddressSpace="SystemProperty" InterfaceObjectRef="7"'
            b' PropertyID="5" StartAddress="0" />',
            b"3",
        )
        + load_control("PeiprogLoadControl", b"", b"4")
        + b"</Resources><MemorySegments>"
        b'<MemorySegment MemoryType="EEPROM" Length="880">'
        b'<Location AddressSpace="StandardMemory" StartAddress="256" />'
        b'<AccessRights Read="Configuration" Write="Configuration" />'
        b"</MemorySegment></MemorySegments></HawkConfigurationData></MaskVersion>"
        b"</MaskVersions></MasterData></KNX>"
    )
    resources = mask_resources(master, _MASK)
    assert resources is not None
    assert (resources.lsm_object(3), resources.lsm_object(4)) == (7, 4)
    assert resources.omits_load_event(4)
    assert resources.memory() == ((256, 880),)
