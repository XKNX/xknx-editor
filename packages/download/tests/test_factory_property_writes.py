"""Property writes start from the factory image and keep the device's own value of what no
parameter covers, in a full load too."""

from __future__ import annotations

import pytest
from xknx.telegram.apci import PropertyValueWrite

from xknxeditor.download.factory_image import parse_factory_image
from xknxeditor.download.image import DownloadImage, PropertyValue
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp

from .conftest import FakeDevice
from .test_factory_image import _image, _segment
from .test_procedure import _application

# MV-07B0 counts its application objects from index 6.
_FACTORY = parse_factory_image(
    _image(
        _segment(4, 4, 60, 0, b"\x11\x22\x33\x44"),
        _segment(5, 2, 60, 0, b"\x55\x66\x77\x88"),
    )
)


def _parameters(index: int) -> PropertyValue:
    # The second byte holds a parameter value; the first is covered by an inactive one.
    return PropertyValue(
        object_index=index,
        property_id=60,
        occurrence=0,
        data=b"\x00\x99",
        mask=b"\x00\xff",
        covered=b"\xff\xff",
    )


def _runner(
    index: int,
    scope: DownloadScope,
    properties: tuple[PropertyValue, ...],
) -> tuple[LoadProcedureRunner, FakeDevice]:
    device = FakeDevice()
    device.properties[index, 60] = b"ABCD"
    device.property_element_sizes[index, 60] = 2
    runner = LoadProcedureRunner(
        _application(
            LdCtrlWriteProp(
                obj_idx=index, prop_id=60, start_element=1, count=2, verify=True
            )
        ),
        DownloadImage((), properties, factory_image=_FACTORY),
        DeviceProgrammer(device),
    )
    runner.scope = scope
    return runner, device


def _written(device: FakeDevice) -> list[bytes]:
    return [p.data for p in device.sent if isinstance(p, PropertyValueWrite)]


@pytest.mark.parametrize(
    ("index", "scope", "written"),
    [
        # Every download writes what the parameters cover, under an inactive one the
        # factory contents, and keeps the device's rest.
        (4, DownloadScope.FULL, b"\x11\x99CD"),
        (4, DownloadScope.APPLICATION, b"\x11\x99CD"),
        # An application object's contents are held by its application object index.
        (8, DownloadScope.FULL, b"\x55\x99CD"),
        (4, DownloadScope.PARAMETERS, b"\x11\x99CD"),
        (8, DownloadScope.PARAMETERS, b"\x55\x99CD"),
    ],
)
async def test_property_writes_start_from_the_factory_image(
    index: int, scope: DownloadScope, written: bytes
) -> None:
    runner, device = _runner(index, scope, (_parameters(index),))
    await runner.run()
    assert _written(device) == [written]
    assert device.properties[index, 60] == written


@pytest.mark.parametrize("scope", [DownloadScope.FULL, DownloadScope.PARAMETERS])
async def test_a_property_no_parameter_covers_is_not_written(
    scope: DownloadScope,
) -> None:
    runner, device = _runner(4, scope, ())
    await runner.run()
    assert _written(device) == []
    assert device.properties[4, 60] == b"ABCD"


async def test_a_property_without_factory_contents_starts_from_zeros() -> None:
    device = FakeDevice()
    device.properties[4, 60] = b"ABCD"
    device.property_element_sizes[4, 60] = 2
    runner = LoadProcedureRunner(
        _application(
            LdCtrlWriteProp(
                obj_idx=4, prop_id=60, start_element=1, count=2, verify=True
            )
        ),
        DownloadImage((), (_parameters(4),)),
        DeviceProgrammer(device),
    )
    await runner.run()
    assert _written(device) == [b"\x00\x99CD"]


async def test_preflight_shows_the_kept_device_value() -> None:
    runner, _ = _runner(4, DownloadScope.PARAMETERS, (_parameters(4),))
    report = await runner.preflight()
    (diff,) = report.properties
    assert (diff.current, diff.planned) == (b"ABCD", b"\x11\x99CD")


async def test_preflight_leaves_out_a_property_no_parameter_covers() -> None:
    runner, _ = _runner(4, DownloadScope.PARAMETERS, ())
    report = await runner.preflight()
    assert list(report.properties) == []
