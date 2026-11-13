"""Complete native images share a plan across simulation and read-only preflight."""

import importlib
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.telegram.apci import MemoryWrite, PropertyValueWrite
from xsdata.formats.dataclass.parsers import XmlParser

from packages.vendor.tests.powertool_fixtures import open_archive
from xknxeditor.download.download import prepared_controls
from xknxeditor.download.errors import UnsupportedProcedureError
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.mask_version_t import MaskVersion
from xknxeditor.prod import MasterData, parse_application_xml
from xknxeditor.vendor.powertool import (
    Association,
    HostProfile,
    compile_image,
    default_config,
)
from xknxeditor.vendor.powertool.prepare import prepare_image

from .conftest import FakeDevice

ROOT = Path(__file__).parent / "fixtures/powertool"
MAPPING = {0: 0, 1: 1, 2: 2, 3: 9, 4: 3, 5: 4}


def applications(variant="01"):
    return [
        parse_application_xml((ROOT / f"{v}.xml").read_bytes(), "M-0007")[0]
        for v in (["21", "31"] if variant == "01" else ["22", "32"])
    ]


def master():
    root = ET.parse(ROOT / "mask.xml").getroot()
    for node in root.iter():
        node.tag = node.tag.split("}")[-1]
    mask = XmlParser().from_string(ET.tostring(root, encoding="unicode"), MaskVersion)
    return MasterData(
        cast(Any, SimpleNamespace(mask_versions=SimpleNamespace(mask_version=[mask])))
    )


@pytest.mark.parametrize("short", [False, True])
@pytest.mark.parametrize("variant", ["01", "02"])
async def test_complete_plan(short, variant):
    config = default_config(variant)
    role = compile_image(config).objects[0].key
    compiled = compile_image(
        config, HostProfile(short_associations=short), [Association(role, 123, True)]
    )
    prepared = prepare_image(compiled, short_associations=short)
    app, pei = applications(variant)
    controls = prepared_controls(app, prepared, master(), pei)
    boundary = next(
        i for i, c in enumerate(controls) if isinstance(c, LdCtrlLoadCompleted)
    )
    assert all(
        i < boundary for i, c in enumerate(controls) if isinstance(c, LdCtrlWriteProp)
    )
    assert not any(isinstance(c, LdCtrlWriteProp) and c.prop_id == 27 for c in controls)
    fake = FakeDevice(MAPPING, 0x07B0)
    fake.table_references = {i: 0x1000 + i * 0x1000 for i in range(1, 6)}
    for prop in compiled.properties:
        fake.property_element_sizes[prop.object_index, prop.pid] = len(prop.data)
    runner = LoadProcedureRunner(
        app,
        prepared.image,
        DeviceProgrammer(fake),
        controls=controls,
        object_types=MAPPING,
        application2=pei,
    )
    report = await runner.preflight()
    assert report.segments
    assert not any(isinstance(p, (MemoryWrite, PropertyValueWrite)) for p in fake.sent)
    fake.sent.clear()
    await runner.run()
    for r in compiled.resources:
        base = fake.table_references[r.object_index]
        assert all(
            fake.memory[base + i] == v for i, v in enumerate(r.data) if r.mask[i]
        )
    for prop in compiled.properties:
        assert fake.properties[prop.object_index, prop.pid] == prop.data
    assert not any(
        isinstance(p, PropertyValueWrite) and p.property_id == 27 for p in fake.sent
    )


async def test_prepared_write_gate_precedes_bus(monkeypatch):
    from dataclasses import replace

    dl = importlib.import_module("xknxeditor.download.download")
    app, pei = applications()

    def forbidden(*args, **kwargs):
        raise AssertionError("bus accessed")

    monkeypatch.setattr(dl, "_learn_device_mask", forbidden)
    for scope in DownloadScope:
        with pytest.raises(UnsupportedProcedureError):
            await dl.download(
                cast(Any, None),
                "1.1.1",
                app,
                prepared=replace(
                    prepare_image(compile_image(default_config("01"))),
                    programming_allowed=False,
                ),
                scope=scope,
                application2=pei,
            )


async def test_public_preflight_and_format_disagreement(monkeypatch):
    from xknxeditor.download.errors import ImageError

    dl = importlib.import_module("xknxeditor.download.download")
    app, pei = applications()
    prepared = prepare_image(compile_image(default_config("01")))
    fake = FakeDevice(MAPPING, 0x07B0)
    fake.table_references = {i: 0x1000 + i * 0x1000 for i in range(1, 6)}
    fake.property_element_sizes[2, 23] = 4

    class Offline:
        async def open(self):
            return fake

        async def close(self):
            pass

    monkeypatch.setattr(dl, "_connection_manager", lambda *a: Offline())
    report = await dl.preflight(
        cast(Any, None),
        "1.1.1",
        app,
        prepared=prepared,
        master=master(),
        application2=pei,
        max_apdu_length=15,
    )
    assert {s.address for s in report.segments} >= set(fake.table_references.values())
    assert not any(isinstance(p, (PropertyValueWrite, MemoryWrite)) for p in fake.sent)
    fake.property_element_sizes[2, 23] = 2
    with pytest.raises(ImageError, match="association format"):
        await dl.preflight(
            cast(Any, None),
            "1.1.1",
            app,
            prepared=prepared,
            master=master(),
            application2=pei,
            max_apdu_length=15,
        )


@pytest.mark.parametrize("variant", ["01", "02"])
async def test_alignment_holes_are_preserved(variant):
    from dataclasses import replace

    from xknxeditor.vendor.powertool import ChannelConfig

    config = default_config(variant)
    config = replace(
        config, channels=(ChannelConfig("dim_rocker"), *config.channels[1:])
    )
    compiled = compile_image(config)
    resource = compiled.resources[0]
    holes = [i for i, value in enumerate(resource.mask) if value == 0]
    assert holes
    prepared = prepare_image(compiled)
    app, pei = applications(variant)
    fake = FakeDevice(MAPPING, 0x07B0)
    fake.table_references = {i: 0x1000 + i * 0x1000 for i in range(1, 6)}
    for prop in compiled.properties:
        fake.property_element_sizes[prop.object_index, prop.pid] = len(prop.data)
    base = fake.table_references[4]
    for offset in holes:
        fake.memory[base + offset] = 0xA5
    runner = LoadProcedureRunner(
        app,
        prepared.image,
        DeviceProgrammer(fake),
        controls=prepared_controls(app, prepared, master(), pei),
        object_types=MAPPING,
        application2=pei,
    )
    await runner.run()
    assert all(fake.memory[base + i] == 0xA5 for i in holes)
    report = await runner.preflight()
    assert all(not s.changed_bytes for s in report.segments)


def test_incomplete_and_late_prepared_controls_are_rejected():
    from xknxeditor.download.errors import ImageError
    from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import (
        LdCtrlWriteRelMem,
    )

    app, pei = applications()
    prepared = prepare_image(compile_image(default_config("01")))
    controls = prepared_controls(app, prepared, master(), pei)
    write = next(c for c in controls if isinstance(c, LdCtrlWriteRelMem))
    missing = [c for c in controls if c is not write]
    with pytest.raises(ImageError, match="cover"):
        prepared.materialize(missing, DownloadScope.FULL)
    with pytest.raises(ImageError, match="after load completion"):
        prepared.materialize([*missing, write], DownloadScope.FULL)


@pytest.mark.parametrize("short", [False, True])
@pytest.mark.parametrize(
    ("case", "profile"),
    [
        ("DI-3", "ObjektRTR"),
        ("DI-4", "PraesenzStandard"),
        ("DI-5", "PraesenzKomfort"),
        ("DI-11", "ObjektRTRunified"),
        ("DI-12", "SoloRTR3"),
        ("DI-13", "SoloRTR3oBE"),
    ],
)
async def test_family_complete_plan_and_preserved_masks(
    case, profile, short, monkeypatch
):
    from xknxeditor.vendor.powertool.family import from_saved
    from xknxeditor.vendor.powertool.family_codec import PROFILES, compile_family

    with open_archive("host-projects.zip") as archive:
        config = from_saved(archive.read(case + ".blob"))
    number, main, secondary, _ = PROFILES[profile]
    with open_archive("host-products.zip") as archive:
        app, pei = [
            parse_application_xml(
                archive.read(
                    next(
                        n
                        for n in archive.namelist()
                        if f"A-{number:04X}-{version:02X}-" in n
                    )
                ),
                "M-0007",
            )[0]
            for version in (main, secondary)
        ]
    role = next(o.key for o in compile_family(config).objects if o.visible)
    compiled = compile_family(
        config, HostProfile(short_associations=short), [Association(role, 0x1234, True)]
    )
    prepared = prepare_image(
        compiled, short_associations=short, secondary_application=pei
    )
    controls = prepared_controls(app, prepared, master(), pei)
    boundary = next(
        i for i, c in enumerate(controls) if isinstance(c, LdCtrlLoadCompleted)
    )
    assert all(
        i < boundary for i, c in enumerate(controls) if isinstance(c, LdCtrlWriteProp)
    )
    fake = FakeDevice(MAPPING, 0x07B0)
    fake.table_references = {i: 0x1000 + i * 0x1000 for i in range(1, 6)}
    for prop in compiled.properties:
        fake.property_element_sizes[prop.object_index, prop.pid] = len(prop.data)
    holes = []
    for resource in compiled.resources:
        for i, mask in enumerate(resource.mask):
            if not mask:
                address = fake.table_references[resource.object_index] + i
                fake.memory[address] = 0xA5
                holes.append(address)
    runner = LoadProcedureRunner(
        app,
        prepared.image,
        DeviceProgrammer(fake),
        controls=controls,
        object_types=MAPPING,
        application2=pei,
    )
    dl = importlib.import_module("xknxeditor.download.download")

    class Offline:
        async def open(self):
            return fake

        async def close(self):
            pass

    fake.property_element_sizes[2, 23] = 2 if short else 4
    monkeypatch.setattr(dl, "_connection_manager", lambda *args: Offline())
    await dl.download(
        None,
        "1.1.1",
        app,
        prepared=prepared,
        master=master(),
        application2=pei,
        commission=False,
        max_apdu_length=15,
    )
    assert all(fake.memory[a] == 0xA5 for a in holes)
    for r in compiled.resources:
        base = fake.table_references[r.object_index]
        assert all(
            fake.memory[base + i] == v for i, v in enumerate(r.data) if r.mask[i]
        )
    assert not any(
        isinstance(p, PropertyValueWrite) and p.property_id == 27 for p in fake.sent
    )
    fake.sent.clear()
    report = await runner.preflight()
    assert all(not s.changed_bytes for s in report.segments)
    assert not any(isinstance(p, (MemoryWrite, PropertyValueWrite)) for p in fake.sent)


async def test_prepared_write_rejects_incompatible_format_without_writes(monkeypatch):
    from xknxeditor.download.errors import ImageError

    dl = importlib.import_module("xknxeditor.download.download")
    app, pei = applications()
    prepared = prepare_image(compile_image(default_config("01")))
    fake = FakeDevice(MAPPING, 0x07B0)
    fake.property_element_sizes[2, 23] = 2

    class Offline:
        async def open(self):
            return fake

        async def close(self):
            pass

    monkeypatch.setattr(dl, "_connection_manager", lambda *args: Offline())
    with pytest.raises(ImageError, match="association format"):
        await dl.download(
            None,
            "1.1.1",
            app,
            prepared=prepared,
            master=master(),
            application2=pei,
            commission=False,
            max_apdu_length=15,
        )
    assert not any(isinstance(p, (MemoryWrite, PropertyValueWrite)) for p in fake.sent)


@pytest.mark.parametrize("short", [False, True])
async def test_powerline_plan_property_elements_and_domain(short):
    from xknxeditor.vendor.powertool.native import (
        compile_native,
        default,
        for_product,
        product_profile,
    )

    with open_archive("all-profile-products.zip") as archive:
        app, pei = [
            parse_application_xml(
                archive.read(
                    next(
                        n
                        for n in archive.namelist()
                        if n.startswith("host/M-0007/M-0007_A-6128-" + version)
                    )
                ),
                "M-0007",
            )[0]
            for version in ("90-", "A0-")
        ]
        root = ET.fromstring(archive.read("smarttouch/knx_master.xml"))
    for node in root.iter():
        node.tag = node.tag.split("}")[-1]
    mask_node = next(n for n in root.iter() if n.get("Id") == "MV-17B0")
    mask = XmlParser().from_string(
        ET.tostring(mask_node, encoding="unicode"), MaskVersion
    )
    pl_master = MasterData(
        SimpleNamespace(mask_versions=SimpleNamespace(mask_version=[mask]))
    )
    config = for_product(default(product_profile(app, pei)), app, pei)
    role = next(o.key for o in compile_native(config).objects if o.visible)
    compiled = compile_native(
        config,
        HostProfile(
            mask_version=0x17B0,
            domain_address=0x12,
            repeater_present=True,
            short_associations=short,
        ),
        [Association(role, i + 1, i == 0, i == 8) for i in range(9)],
    )
    prepared = prepare_image(
        compiled, short_associations=short, secondary_application=pei
    )
    assert prepared.domain_address == 0x12
    controls = prepared_controls(app, prepared, pl_master, pei)
    speaker_write = next(
        c
        for c in controls
        if isinstance(c, LdCtrlWriteProp) and (c.obj_idx, c.prop_id) == (1, 53)
    )
    assert speaker_write.count == 2
    assert speaker_write.inline_data == b"\0\x01"
    assert not any(
        isinstance(c, LdCtrlWriteProp) and c.prop_id in (27, 70) for c in controls
    )
    fake = FakeDevice(MAPPING, 0x17B0)
    fake.table_references = {i: 0x1000 + i * 0x1000 for i in range(1, 6)}
    for p in prepared.image.properties:
        fake.property_element_sizes[p.object_index, p.property_id] = p.element_size
    runner = LoadProcedureRunner(
        app,
        prepared.image,
        DeviceProgrammer(fake),
        controls=controls,
        object_types=MAPPING,
        application2=pei,
    )
    await runner.preflight()
    assert not any(isinstance(p, (MemoryWrite, PropertyValueWrite)) for p in fake.sent)
    await runner.run()
    assert fake.properties[1, 53] == b"\0\x01"
    assert fake.properties[0, 73] == b"\xef"
    for resource in compiled.resources:
        base = fake.table_references[resource.object_index]
        assert all(
            fake.memory[base + i] == b
            for i, b in enumerate(resource.data)
            if resource.mask[i]
        )


def test_completion_properties_require_an_explicit_final_step():
    from dataclasses import replace

    from xknxeditor.download.errors import ImageError

    app, pei = applications()
    prepared = replace(
        prepare_image(compile_image(default_config("01"))),
        completion_properties=frozenset({(0, 52)}),
    )
    plain = replace(prepared, completion_properties=frozenset())
    controls = prepared_controls(app, plain, master(), pei)
    write = next(
        c
        for c in controls
        if isinstance(c, LdCtrlWriteProp) and (c.obj_idx, c.prop_id) == (0, 52)
    )
    with pytest.raises(ImageError, match="completion property"):
        prepared.materialize(controls, DownloadScope.FULL)
    missing = [c for c in controls if c is not write]
    with pytest.raises(ImageError, match="completion property"):
        prepared.materialize(missing, DownloadScope.FULL)
    completion = max(
        i for i, c in enumerate(missing) if isinstance(c, LdCtrlLoadCompleted)
    )
    assert prepared.materialize(
        [*missing[: completion + 1], write, *missing[completion + 1 :]],
        DownloadScope.FULL,
    )
