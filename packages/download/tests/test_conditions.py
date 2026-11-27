"""Prepared property gates must read the device and never substitute image values."""

import pytest
from xknx.telegram.apci import PropertyValueWrite

from xknxeditor.download.conditions import ConditionalWrite, PropertyAlternatives
from xknxeditor.download.errors import CompareMismatch
from xknxeditor.download.image import DownloadImage, PropertyValue
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp

from .conftest import FakeDevice
from .test_prepared import applications


@pytest.mark.parametrize("preflight", [True, False])
@pytest.mark.parametrize("valid", [True, False])
async def test_alternative_property_gate(preflight, valid):
    app, _ = applications()
    fake = FakeDevice({0: 0, 4: 3}, 0x07B0)
    fake.properties[4, 13] = b"\0\x01\x98\x20\x02" if valid else b"\0\x07\x61\x08\x01"
    fake.property_element_sizes[4, 13] = 5
    gate = PropertyAlternatives(
        tuple(
            LdCtrlCompareProp(obj_idx=4, prop_id=13, inline_data=v)
            for v in (b"\0\x01\x98\x21\x02", b"\0\x01\x98\x20\x02")
        )
    )
    # A conflicting image value must not replace a literal compatibility condition.
    image = DownloadImage((), (PropertyValue(4, 13, 0, b"\0\x07\x61\x08\x01", 5),))
    runner = LoadProcedureRunner(
        app, image, DeviceProgrammer(fake), controls=[gate], object_types={0: 0, 4: 3}
    )
    action = runner.preflight if preflight else runner.run
    if valid:
        await action()
    else:
        with pytest.raises(CompareMismatch):
            await action()
    assert not any(isinstance(p, PropertyValueWrite) for p in fake.sent)


async def test_conditional_write_only_previews_when_condition_matches():
    app, _ = applications()
    fake = FakeDevice({0: 0, 4: 3, 6: 0xFFFF}, 0x07B0)
    fake.properties[4, 6] = b"\x01"
    fake.properties[6, 205] = b"\0"
    fake.property_element_sizes[6, 205] = 1
    control = ConditionalWrite(
        (LdCtrlCompareProp(obj_idx=4, prop_id=6, inline_data=b"\x01"),),
        LdCtrlWriteProp(obj_idx=6, prop_id=205, inline_data=b"\x5a", verify=True),
    )
    runner = LoadProcedureRunner(
        app,
        DownloadImage((), ()),
        DeviceProgrammer(fake),
        controls=[control],
        object_types=fake.object_types,
    )
    report = await runner.preflight()
    assert report.properties
    assert not any(isinstance(p, PropertyValueWrite) for p in fake.sent)
    await runner.run()
    assert fake.properties[6, 205] == b"\x5a"
    fake.properties[4, 6] = b"\0"
    fake.properties[6, 205] = b"\0"
    report = await runner.preflight()
    assert not report.properties
    await runner.run()
    assert fake.properties[6, 205] == b"\0"


async def test_final_property_reads_keep_latest_record_without_changing_baseline():
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
        LdCtrlLoadImageProp,
    )

    app, _ = applications()
    fake = FakeDevice({0: 0, 4: 3}, 0x07B0)
    fake.property_element_sizes[4, 27] = 8
    fake.properties[4, 27] = b"12345678"
    runner = LoadProcedureRunner(
        app,
        DownloadImage((), ()),
        DeviceProgrammer(fake),
        controls=[],
        object_types=fake.object_types,
    )
    read = LdCtrlLoadImageProp(obj_idx=4, prop_id=27)
    await runner._execute(read)
    fake.properties[4, 27] = b"ABCDEFGH"
    await runner._execute(read)
    assert runner.property_reads[4, 27, 1] == b"ABCDEFGH"
    assert runner._property_captures[4, 27, 1] == b"12345678"


@pytest.mark.parametrize(
    "loaded,running", [(True, True), (True, False), (False, True), (False, False)]
)
async def test_conditional_write_requires_every_condition(loaded, running):
    from xknxeditor.download.load_state import LoadState

    app, _ = applications()
    fake = FakeDevice({0: 0, 4: 3, 6: 0xFFFF}, 0x07B0)
    fake.load_states[4] = LoadState.LOADED if loaded else LoadState.UNLOADED
    fake.properties[4, 6] = bytes([running])
    fake.properties[6, 205] = b"\0"
    fake.property_element_sizes[6, 205] = 1
    control = ConditionalWrite(
        tuple(
            LdCtrlCompareProp(obj_idx=4, prop_id=pid, inline_data=b"\x01")
            for pid in (5, 6)
        ),
        LdCtrlWriteProp(obj_idx=6, prop_id=205, inline_data=b"\x5a", verify=True),
    )
    runner = LoadProcedureRunner(
        app,
        DownloadImage((), ()),
        DeviceProgrammer(fake),
        controls=[control],
        object_types=fake.object_types,
    )
    report = await runner.preflight()
    assert bool(report.properties) == (loaded and running)
    await runner.run()
    assert fake.properties[6, 205] == (b"\x5a" if loaded and running else b"\0")
    assert runner.state_mutated == (loaded and running)


async def test_required_property_read_cannot_return_an_old_record():
    from xknxeditor.download.conditions import FinalPropertyRead
    from xknxeditor.download.errors import VerificationError
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
        LdCtrlLoadImageProp,
    )

    app, _ = applications()
    fake = FakeDevice({0: 0, 4: 3}, 0x07B0)
    fake.property_element_sizes[4, 27] = 8
    fake.properties[4, 27] = b"12345678"
    runner = LoadProcedureRunner(
        app,
        DownloadImage((), ()),
        DeviceProgrammer(fake),
        controls=[],
        object_types=fake.object_types,
    )
    read = FinalPropertyRead(LdCtrlLoadImageProp(obj_idx=4, prop_id=27), 8)
    await runner._execute(read)
    fake.properties[4, 27] = b""
    with pytest.raises(VerificationError):
        await runner._execute(read)
    assert (4, 27, 1) not in runner.property_reads


async def test_final_record_read_runs_only_after_programming():
    from xknxeditor.download.conditions import FinalPropertyRead
    from xknxeditor.download.errors import VerificationError
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
        LdCtrlLoadImageProp,
    )

    app, _ = applications()
    fake = FakeDevice({0: 0, 4: 3}, 0x07B0)
    fake.property_element_sizes[4, 27] = 8
    control = FinalPropertyRead(LdCtrlLoadImageProp(obj_idx=4, prop_id=27), 8)
    runner = LoadProcedureRunner(
        app,
        DownloadImage((), ()),
        DeviceProgrammer(fake),
        controls=[control],
        object_types=fake.object_types,
    )
    await runner.preflight()
    assert not fake.sent
    fake.properties[4, 27] = b"12345678"
    await runner.run()
    assert runner.property_reads[4, 27, 1] == b"12345678"
    fake.properties[4, 27] = b""
    with pytest.raises(VerificationError):
        await runner.run()
    assert runner.state_mutated is False


@pytest.mark.parametrize("loaded", [True, False])
@pytest.mark.parametrize("preflight", [True, False])
async def test_conditional_read_reads_only_when_its_condition_matches(
    loaded, preflight
):
    from xknxeditor.download.conditions import ConditionalRead
    from xknxeditor.download.load_state import LoadState
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
        LdCtrlLoadImageProp,
    )

    app, _ = applications()
    fake = FakeDevice({0: 0, 4: 3, 6: 0xFFFF}, 0x07B0)
    fake.load_states[4] = LoadState.LOADED if loaded else LoadState.UNLOADED
    fake.properties[6, 203] = b"\x03"
    fake.property_element_sizes[6, 203] = 1
    control = ConditionalRead(
        (LdCtrlCompareProp(obj_idx=4, prop_id=5, inline_data=b"\x01"),),
        LdCtrlLoadImageProp(obj_idx=6, prop_id=203),
    )
    runner = LoadProcedureRunner(
        app,
        DownloadImage((), ()),
        DeviceProgrammer(fake),
        controls=[control],
        object_types=fake.object_types,
    )
    await (runner.preflight() if preflight else runner.run())
    assert (runner.property_reads.get((6, 203, 1)) == b"\x03") == loaded
    assert not any(isinstance(p, PropertyValueWrite) for p in fake.sent)
    assert not runner.state_mutated
