from dataclasses import replace
from types import SimpleNamespace

import pytest
from xknx.profile.const import ResourceObjectType

from editor_gui.plugins.project.tests import test_mdt_dca
from editor_gui.plugins.recover.mdt import (
    addin_reader,
    editable_parameter_refs,
    parameter_candidates,
)
from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
from xknxeditor.download import build_image
from xknxeditor.download.tables import Association
from xknxeditor.recover import (
    RecoveredDevice,
    RecoveredParameters,
    RecoveryEffort,
    devices_from_snapshot,
    recover_parameters,
    snapshots_json,
)
from xknxeditor.recover.recover import com_object_ref_by_number, seed_dynamic_ui
from xknxeditor.recover.tables_decode import (
    DecodedGroupObject,
    DecodedLink,
    decode_group_address_table_b,
)
from xknxeditor.vendor.mdt import encode_symbol
from xknxeditor.vendor.mdt.symbols import recovered_symbol_entries

mdt_project = test_mdt_dca.mdt_project


def _recovered(device, *, values=None, entries=None):
    return RecoveredDevice(
        address="1.1.50",
        application_id=device.app.id,
        device_address=0x1132,
        group_addresses=[],
        links=[],
        group_objects={},
        parameters=RecoveredParameters(values=values or {}, unknown=[]),
        addin_data=entries or {},
    )


def _entry(project, device, recovered):
    product = next(
        p for p in project._catalog.get_products() if p.application_id == device.app.id
    )
    return RecoverEntry(
        address=recovered.address,
        mask_version=0x07B0,
        application=device.app,
        product_ref_id=product.product_ref_id,
        hardware2program_ref_id=product.hardware2program_ref_id,
        recovered=recovered,
    )


def _service(project):
    return RecoverService(SimpleNamespace(project=project))


def _segments(device):
    image = build_image(device.app, ui=device.dynamic_ui)
    return {
        segment.id: block.memory.data
        for segment in device.app.program.static.code.relative_segment
        for block in image.application_segments
        if segment.load_state_machine == block.object_index
        and segment.offset == block.memory.address
    }


def test_symbol_recovery_snapshot_project_undo_and_export(mdt_project, tmp_path):
    from xknxeditor.proj import export_knxproj

    project = mdt_project
    source = project.devices[2]
    profile = project.mdt_dca_profile(source)
    symbols = project.mdt_symbol_payload(source.node_id, include_defaults=True)
    symbols[35] = encode_symbol(bytes(range(256)) * 16)
    entries = {profile.app_id: recovered_symbol_entries(symbols, profile)}
    recovered = devices_from_snapshot(
        snapshots_json([_recovered(source, entries=entries)])
    )[0]
    refs = com_object_ref_by_number(source.app)
    number = next(iter(refs))
    recovered = replace(
        recovered,
        group_addresses=[0x0A01, 0x0A02],
        links=[DecodedLink(0x0A01, number, True), DecodedLink(0x0A02, number, False)],
        group_objects={
            number: DecodedGroupObject(
                number=number,
                priority="low",
                communication=True,
                read=False,
                write=True,
                transmit=True,
                update=False,
                read_on_init=None,
                size_code=0,
                object_size="1 Bit",
            )
        },
        com_object_refs=refs,
    )
    service = _service(project)
    entry = _entry(project, source, recovered)
    cursor = project.cursor
    assert service._add_recovered(entry, {}), entry.error
    current = project.find_device_by_node_id(entry.device_id)
    assert project.mdt_symbol_payload(current.node_id, include_defaults=True) == symbols
    assert project.mdt_symbols(current.node_id)["new_library"] is None
    assert project.mdt_symbols(current.node_id)["defaults"] == []
    assert "Lib" not in project._svc.device_addin_data(
        project._pid, current.node_id, profile.app_id
    )
    co = current.find_com_object(refs[number])
    assert co.flags.write and not co.flags.read
    assert len(project._capture_links(current)) == 2
    assert project.undo()
    assert project.cursor == cursor
    assert project.find_device_by_address("1.1.50") is None
    assert project.redo()
    project.close()
    project.open(tmp_path / "project.xknx")
    current = project.find_device_by_address("1.1.50")
    assert project.mdt_symbol_payload(current.node_id, include_defaults=True) == symbols
    export_knxproj(
        tmp_path / "project.xknx", tmp_path / "recovered.knxproj", schema="20"
    )
    project.close()
    project.import_knxproj(tmp_path / "recovered.knxproj", tmp_path / "roundtrip.xknx")
    current = project.find_device_by_address("1.1.50")
    assert project.mdt_symbol_payload(current.node_id, include_defaults=True) == symbols
    assert len(project._capture_links(current)) == 2


@pytest.mark.parametrize("failure", ["missing", "crc", "wrong_application"])
def test_invalid_recovered_symbols_roll_back_the_entire_device(mdt_project, failure):
    project = mdt_project
    source = project.devices[2]
    profile = project.mdt_dca_profile(source)
    entries = recovered_symbol_entries(
        project.mdt_symbol_payload(source.node_id, include_defaults=True), profile
    )
    app_id = profile.app_id
    if failure == "missing":
        del entries["Symbol_35_data"], entries["Symbol_35_bmp"]
    elif failure == "crc":
        entries["Symbol_2_data"] = b"\x00" * 514
    else:
        app_id = "M0083-A00D1"
    entry = _entry(project, source, _recovered(source, entries={app_id: entries}))
    cursor = project.cursor
    assert not _service(project)._add_recovered(entry, {})
    assert entry.error
    assert project.cursor == cursor
    assert project.find_device_by_address("1.1.50") is None


def test_gts_layout_recovers_from_full_image(mdt_project):
    from xknxeditor.vendor.mdt.layout import Tile

    project = mdt_project
    source = project.devices[3]
    view = project.mdt_layout(source.node_id)
    page = view["pages"][1]
    tiles = list(page["tiles"])
    tiles[0] = {**tiles[0], "color": 1, "transparency": 29}
    project.set_mdt_layout(source.node_id, page["number"], "Recovered", "V", tiles)
    project.set_mdt_layout(source.node_id, 0, "", "V", [vars(Tile(19, 29, 6))])
    source = project.find_device_by_node_id(source.node_id)
    segments = _segments(source)
    params = recover_parameters(
        source.app,
        segments,
        effort=RecoveryEffort.FAST,
        editable_parameter_refs=editable_parameter_refs(source.app),
        parameter_candidates=parameter_candidates(source.app),
        object_sizes={
            co.number: co.object_size for co in source.get_visible_com_objects()
        },
        linked_objects={co.number for co in source.get_visible_com_objects()},
    )
    assert params.complete, (params.differing_bytes, params.differing_objects)
    recovered = replace(
        _recovered(source), parameters=params, parameter_segments=segments
    )
    recovered = replace(
        recovered,
        com_object_refs=com_object_ref_by_number(
            source.app, seed_dynamic_ui(source.app, params.values)
        ),
    )
    entry = _entry(project, source, recovered)
    assert _service(project)._add_recovered(entry, {}), entry.error
    current = project.find_device_by_node_id(entry.device_id)
    actual = project.mdt_layout(current.node_id)
    assert actual["pages"] == project.mdt_layout(source.node_id)["pages"]
    actual_segments = _segments(current)
    actual_mask = current.dynamic_ui.encode_to_memory_masked()
    for key, data in segments.items():
        programmed = bytes(
            (old & ~mask) | (new & mask)
            for old, new, mask in zip(
                data, actual_segments[key], actual_mask[key][1], strict=True
            )
        )
        assert programmed == data


def test_reader_is_selected_only_for_registered_symbol_applications():
    assert addin_reader("M-0083_A-018B-31-369F") is not None
    assert addin_reader("M-0083_A-0054-14-0D63") is not None
    assert addin_reader("M-0083_A-FFFF-31-369F") is None
    assert addin_reader("M-0007_A-018B-31-369F") is None


async def test_read_failure_is_visible_and_cannot_apply_a_previous_capture(monkeypatch):
    import editor_gui.plugins.recover.service as module

    async def fail(*args, **kwargs):
        assert kwargs["addin_read"] is not None
        raise ValueError("MDT symbol 1 pixels are not readable")

    monkeypatch.setattr(module, "recover_device_at", fail)
    api = SimpleNamespace(
        project=SimpleNamespace(project_code_page=lambda: None),
        connection=SimpleNamespace(
            xknx=object(), interface_max_apdu_length=15, master=None
        ),
    )
    service = RecoverService(api)
    monkeypatch.setattr(service, "_factory_image", lambda entry: None)
    device = SimpleNamespace(app=SimpleNamespace(id="M-0083_A-018B-31-369F"))
    entry = RecoverEntry(
        address="1.1.1",
        mask_version=0x07B0,
        application=device.app,
        recovered=_recovered(device),
    )
    await service._recover([entry])
    assert entry.recovered is None
    assert entry.error == "MDT symbol 1 pixels are not readable"


def test_visucontrol_recovers_blobs_and_objects_from_full_image(tmp_path):
    from pathlib import Path

    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.catalog.service import CatalogService
    from editor_gui.plugins.logger.service import LogService
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.vendor.mdt.visucontrol import Configuration

    root = Path(__file__).resolve().parents[7] / ".references/mdtdca"
    product_file = root / "VC-08P82-01S_MDT_KP_V10.knxprod"
    export_file = root / "1 - Copy.etsvcp"
    if not product_file.exists() or not export_file.exists():
        pytest.skip("Optional VisuControl references not installed")
    catalog = CatalogService(tmp_path / "catalog.xknxcatalog")
    product = catalog.import_knxprod(product_file)[0]
    app = catalog.get_application(product.application_id)
    project = ProjectService(catalog)
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "project.xknx")
    try:
        node = project.add_device(
            product.product_ref_id, product.hardware2program_ref_id, "VC", app
        )
        config = Configuration.read(export_file.read_bytes())
        project.import_mdt_visucontrol(node, config.write())
        source = project.find_device_by_node_id(node)
        segments = _segments(source)
        params = recover_parameters(
            app,
            segments,
            effort=RecoveryEffort.FAST,
            editable_parameter_refs=editable_parameter_refs(app),
        )
        recovered = replace(
            _recovered(source), parameters=params, parameter_segments=segments
        )
        recovered = replace(
            recovered,
            com_object_refs=com_object_ref_by_number(
                app, seed_dynamic_ui(app, params.values)
            ),
            group_addresses=[0x0A01],
            links=[DecodedLink(0x0A01, 1, True)],
            group_objects={
                1: DecodedGroupObject(
                    number=1,
                    priority="low",
                    communication=True,
                    read=False,
                    write=True,
                    transmit=True,
                    update=False,
                    read_on_init=False,
                    size_code=0,
                    object_size="1 Bit",
                )
            },
        )
        from xknxeditor.recover.verify import build_group_communication

        communication = build_group_communication(recovered, app)
        assert len(communication.links) == 1
        verification = build_image(app, group_communication=communication)
        addresses = verification.relative_segment(
            ResourceObjectType.OBJECT_ADDRESS_TABLE
        )
        associations = verification.relative_segment(
            ResourceObjectType.OBJECT_ASSOCIATION_TABLE
        )
        assert addresses is not None
        assert decode_group_address_table_b(addresses.data) == recovered.group_addresses
        assert associations is not None
        assert associations.associations == (Association(0, 1),)
        entry = _entry(project, source, recovered)
        cursor = project.cursor
        assert _service(project)._add_recovered(entry, {}), entry.error
        current = project.find_device_by_node_id(entry.device_id)
        assert _segments(current) == segments
        assert (
            Configuration.read(project.export_mdt_visucontrol(current.node_id)).parts[
                1:4
            ]
            == config.parts[1:4]
        )
        objects = {co.number: co for co in current.get_visible_com_objects()}
        assert len(project._capture_links(current)) == 1
        assert objects[1].flags.write and not objects[1].flags.read
        for obj in config.objects():
            assert objects[obj.number].name == obj.name
            assert objects[obj.number].dpt.major == obj.dpt
        assert not project.device_binary_data(current, "FunctionTexts")
        assert project.undo() and project.cursor == cursor
        assert project.redo()
        project.close()
        project.open(tmp_path / "project.xknx")
        current = project.find_device_by_address("1.1.50")
        assert _segments(current) == segments
        assert len(project._capture_links(current)) == 1
    finally:
        project.close()
