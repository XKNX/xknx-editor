from pathlib import Path
from zipfile import ZipFile

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.download import build_image
from xknxeditor.vendor.mdt.visucontrol import (
    Configuration,
    create_configuration,
    pack_json,
)


def test_visucontrol_import_image_and_undo(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[7] / ".references/mdtdca"
    source = root / "VC-08P82-01S_MDT_KP_V10.knxprod"
    if not source.exists():
        pytest.skip("Optional VisuControl product not installed")
    catalog = CatalogService(tmp_path / "catalog.xknxcatalog")
    product = catalog.import_knxprod(source)[0]
    app = catalog.get_application(product.application_id)
    service = ProjectService(catalog)
    service.set_logger(Logger(LogService(), "project"))
    service.new(tmp_path / "project.xknx")
    try:
        node_id = service.add_device(
            product.product_ref_id, product.hardware2program_ref_id, "VC", app
        )
        assert node_id is not None
        db = {
            "versions": {"database": "3.0.0", "dca_api": "1"},
            "items": {
                "objects_list": {
                    "1": {
                        "id_driver": "knx",
                        "name": "Light",
                        "type": "dtp_1",
                        "typeformat": "1",
                        "command": "1",
                        "flags": 4,
                    }
                }
            },
            "elements": {"objects_list": {}},
        }
        config = create_configuration(
            (pack_json(db), b"scheduler", b"scenes"),
            b"##01" + pack_json({}),
            {"name": "VC"},
        )
        assert service.import_mdt_visucontrol(node_id, config.write()) == []
        device = service.find_device_by_node_id(node_id)
        assert device is not None
        assert [co.number for co in device.get_visible_com_objects()] == [1]
        image = build_image(app, ui=device.dynamic_ui)
        segment = next(s for s in image.application_segments if s.object_index == 4)
        assert len(segment.memory.data) == 66660
        assert segment.memory.data[1127:1131] == len(config.parts[1]).to_bytes(4, "big")
        assert (
            segment.memory.data[1131 : 1131 + len(config.parts[1])] == config.parts[1]
        )
        assert (
            Configuration.read(service.export_mdt_visucontrol(node_id)).parts[1:4]
            == config.parts[1:4]
        )
        service.undo()
        device = service.find_device_by_node_id(node_id)
        assert device is not None and not device.get_visible_com_objects()
        service.redo()
        assert (
            Configuration.read(service.export_mdt_visucontrol(node_id)).parts[1:4]
            == config.parts[1:4]
        )
        empty_optional = create_configuration(
            (config.parts[1], b"", b""), config.function_texts, {}
        )
        service.import_mdt_visucontrol(node_id, empty_optional.write())
        assert (
            Configuration.read(service.export_mdt_visucontrol(node_id)).parts[2:4]
            == config.parts[2:4]
        )
        device = service.find_device_by_node_id(node_id)
        co = device.get_visible_com_objects()[0]
        ga = service.create_group_address_value(0x0A01, "Light")
        service.link_com_object_to_ga(co.db_id, ga, is_sending=True)
        links = service._capture_links(device)
        assert service.import_mdt_visucontrol(node_id, config.write()) == []
        assert service._capture_links(service.find_device_by_node_id(node_id)) == links
        cursor = service.cursor
        invalid = config.database
        invalid["items"]["objects_list"]["1"]["command"] = "65000"
        with pytest.raises(ValueError, match="capacity"):
            service.import_mdt_visucontrol(
                node_id,
                create_configuration(
                    (pack_json(invalid), b"", b""), config.function_texts, {}
                ).write(),
            )
        assert service.cursor == cursor
        assert service._capture_links(service.find_device_by_node_id(node_id)) == links
        incompatible = config.database
        incompatible["items"]["objects_list"]["1"].update(type="dtp_5", typeformat="1")
        lost = service.import_mdt_visucontrol(
            node_id,
            create_configuration(
                (pack_json(incompatible), b"", b""), config.function_texts, {}
            ).write(),
        )
        assert [link["address"] for link in lost] == ["1/2/1"]
        assert not service._capture_links(service.find_device_by_node_id(node_id))
        service.undo()
        assert service._capture_links(service.find_device_by_node_id(node_id)) == links
        service.close()
        service.open(tmp_path / "project.xknx")
        current = service.find_device_by_node_id(node_id)
        assert service._capture_links(current) == links
        assert current.get_visible_com_objects()[0].name == "Light"
        assert (
            Configuration.read(service.export_mdt_visucontrol(node_id)).parts[1:4]
            == config.parts[1:4]
        )
        from xknxeditor.proj import export_knxproj

        export_knxproj(
            tmp_path / "project.xknx", tmp_path / "visu.knxproj", schema="20"
        )
        service.close()
        service.import_knxproj(tmp_path / "visu.knxproj", tmp_path / "roundtrip.xknx")
        device = service.devices[0]
        assert device.get_visible_com_objects()[0].name == "Light"
        assert service._capture_links(device)
        assert (
            Configuration.read(service.export_mdt_visucontrol(device.node_id)).parts[
                1:4
            ]
            == config.parts[1:4]
        )
    finally:
        service.close()


@pytest.fixture
def mdt_project(tmp_path):
    source = Path(__file__).resolve().parents[7] / ".references/mdtdca/~mdtdca.knxproj"
    if not source.exists():
        pytest.skip("Optional MDT project not installed")
    service = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    service.set_logger(Logger(LogService(), "project"))
    service.import_knxproj(source, tmp_path / "project.xknx")
    yield service
    service.close()


def test_original_project_symbols_edit_export_and_undo(mdt_project, tmp_path):
    from xknxeditor.proj import export_knxproj
    from xknxeditor.vendor.mdt.symbols import read_symbols

    service = mdt_project
    device = service.devices[2]
    node = device.node_id
    profile = service.mdt_dca_profile(device)
    original = service._svc.device_addin_data(service._pid, node, profile.app_id)
    view = service.mdt_symbols(node)
    assert len(view["symbols"]) == 5
    assert service.mdt_symbols(node) is view
    export_knxproj(
        tmp_path / "project.xknx", tmp_path / "unchanged.knxproj", schema="20"
    )
    with ZipFile(tmp_path / "unchanged.knxproj") as archive:
        for key, data in original.items():
            assert (
                archive.read(f"P-026D/AddinData/{profile.app_id}/4355-{key}.dat")
                == data
            )
    service.set_mdt_symbol(node, 2, "00" * 4096)
    changed = service.mdt_symbols(node)
    assert changed != view
    service.undo()
    assert (
        service._svc.device_addin_data(service._pid, node, profile.app_id) == original
    )
    service.redo()
    assert service.mdt_symbols(node) == changed
    service.set_device_individual_address(node, "1.1.3", "1.1.33")
    export_knxproj(tmp_path / "project.xknx", tmp_path / "changed.knxproj", schema="20")
    with ZipFile(tmp_path / "changed.knxproj") as archive:
        entries = {
            Path(name).stem.split("-", 1)[1]: archive.read(name)
            for name in archive.namelist()
            if f"/{profile.app_id}/4385-" in name
        }
        assert not any(
            f"/{profile.app_id}/4355-" in name for name in archive.namelist()
        )
    assert len(read_symbols(entries, profile)) == 5
    assert entries["Symbol_2_bmp"].startswith(b"BM")
    assert entries["Symbol_3_bmp"] == original["Symbol_3_bmp"]
    service.close()
    service.import_knxproj(tmp_path / "changed.knxproj", tmp_path / "roundtrip.xknx")
    device = next(d for d in service.devices if d.individual_address == "1.1.33")
    assert service.mdt_symbols(device.node_id) == changed


def test_original_gts_layout_image_links_and_undo(mdt_project):
    from dataclasses import replace

    from xknxeditor.vendor.mdt.layout import Tile

    service = mdt_project
    device = service.devices[3]
    node = device.node_id
    view = service.mdt_layout(node)
    assert [len(p["tiles"]) for p in view["pages"]] == [0, 4, 2, 5]
    _, layout = service._mdt_layout(node)
    page = next(p for p in layout.pages("V") if p.number == 1)
    co = next(c for c in device.get_visible_com_objects() if c.db_id is not None)
    ga = service.create_group_address_value(0x0A01, "Test")
    service.link_com_object_to_ga(co.db_id, ga, is_sending=True)
    original_links = service._capture_links(device)
    original_image = build_image(device.app, ui=device.dynamic_ui)
    changed = replace(
        page, tiles=(replace(page.tiles[0], color=1, transparency=29), *page.tiles[1:])
    )
    service.set_mdt_layout(
        node, changed.number, changed.name, "V", [vars(t) for t in changed.tiles]
    )
    current = service.find_device_by_node_id(node)
    assert service._capture_links(current) == original_links
    image = build_image(current.app, ui=current.dynamic_ui)
    segment = next(s for s in image.application_segments if s.object_index == 4)
    assert len(segment.memory.data) == 39828
    assert image != original_image
    decoded = current.dynamic_ui.decode_memory_parameters(
        {
            s.id: block.memory.data
            for s in current.app.program.static.code.relative_segment
            for block in image.application_segments
            if s.load_state_machine == block.object_index
            and s.offset == block.memory.address
        }
    )
    for ref, value in layout.changes(changed).items():
        parameter = ref.rsplit("_R-", 1)[0]
        if parameter in decoded:
            assert decoded[parameter] == value, ref
    service.undo()
    assert service.mdt_layout(node) == view
    restored = service.find_device_by_node_id(node)
    assert build_image(restored.app, ui=restored.dynamic_ui) == original_image
    assert service._capture_links(restored) == original_links
    service.redo()
    assert service.mdt_layout(node)["pages"][1]["tiles"][0]["color"] == 1
    with pytest.raises(ValueError, match="outside"):
        service.set_mdt_layout(
            node, page.number, page.name, "V", [vars(Tile(710, 29, 0))]
        )


def test_clone_and_copy_config_preserve_symbols(mdt_project):
    project = mdt_project
    source = project.devices[2].node_id
    original_row = next(r for r in project._svc.devices(project._pid) if r.id == source)
    original_row.addin_data[
        0
    ].metadata_xml = '<AddinData Extra="keep"><Extra /></AddinData>'
    original = project.mdt_symbols(source)
    clone = project.clone_device(source)[0]
    assert clone is not None
    assert project.mdt_symbols(clone) == original
    clone_row = next(r for r in project._svc.devices(project._pid) if r.id == clone)
    assert (
        clone_row.addin_data[0].metadata_xml == original_row.addin_data[0].metadata_xml
    )
    project.set_mdt_symbol(clone, 2, "00" * 4096)
    changed_metadata = {
        r.suffix: '<AddinData Extra="target" />' for r in clone_row.addin_data
    }
    project._svc.set_device_addin_data(
        project._pid,
        clone,
        "M0083-A0041",
        project._svc.device_addin_data(project._pid, clone, "M0083-A0041"),
        metadata=changed_metadata,
    )
    changed = project.mdt_symbols(clone)
    clip = project.copy_device_config(source)
    cursor = project.cursor
    assert project.paste_device_config(clone, clip, include_links=False)[0]
    assert project.cursor == cursor + 1
    assert project.mdt_symbols(clone) == original
    assert {r.suffix: r.metadata_xml for r in clone_row.addin_data} == {
        r.suffix: r.metadata_xml for r in original_row.addin_data
    }
    project.undo()
    assert project.mdt_symbols(clone) == changed
    assert {r.suffix: r.metadata_xml for r in clone_row.addin_data} == changed_metadata


def test_panels_render_and_restore_library(mdt_project):
    from imgui_bundle import imgui

    from editor_gui.plugins.project.ui.mdt_dca import MdtDcaPanel

    project = mdt_project
    node = project.devices[2].node_id
    view = project.mdt_symbols(node)
    assert len(view["defaults"]) == 36
    original = view["symbols"]
    project.restore_mdt_symbol(node, 2)
    assert (
        next(s for s in project.mdt_symbols(node)["symbols"] if s["slot"] == 2)[
            "data_hex"
        ]
        == view["defaults"][2]["data_hex"]
    )
    project.undo()
    assert project.mdt_symbols(node)["symbols"] == original
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(400, 700)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        panel = MdtDcaPanel()
        for device in project.devices:
            imgui.new_frame()
            imgui.begin("Configuration")
            panel.render(device, project)
            imgui.end()
            imgui.end_frame()
    finally:
        imgui.destroy_context(context)


def test_gts_status_page_and_horizontal_coordinates(mdt_project):
    from xknxeditor.vendor.mdt.layout import Tile

    project = mdt_project
    device = project.devices[3]
    node = device.node_id
    before = project.mdt_layout(node)
    assert before["pages"][0]["status"]
    cursor = project.cursor
    project.set_mdt_layout(node, 0, "", "V", [])
    assert project.cursor == cursor
    project.set_mdt_layout(node, 0, "", "V", [vars(Tile(19, 29, 6))])
    assert project.cursor == cursor + 1
    assert project.mdt_layout(node)["pages"][0]["tiles"][0]["kind"] == 6
    project.undo()
    assert project.mdt_layout(node) == before
    project.redo()
    project.set_mdt_layout(node, 0, "", "V", [])
    assert not project.mdt_layout(node)["pages"][0]["tiles"]
    project.undo()
    project.undo()
    current = project.find_device_by_node_id(node)
    refs = current.dynamic_ui.parameter_ref_ids()
    night = next(r for r in refs if r.endswith("_P-336_R-353"))
    horizontal = next(r for r in refs if r.endswith("_P-28_R-128"))
    project.edit_params(current, [(night, "1"), (horizontal, "1")])
    before = project.mdt_layout(node)
    cursor = project.cursor
    project.set_mdt_layout(node, -1, "", "H", [vars(Tile(19, 1079, 2))])
    assert project.cursor == cursor + 1
    current = project.find_device_by_node_id(node)
    assert build_image(current.app, ui=current.dynamic_ui)
    assert project.mdt_layout(node)["pages"][0]["number"] == -1
    project.undo()
    assert project.mdt_layout(node) == before
    cursor = project.cursor
    with pytest.raises(ValueError):
        project.set_mdt_layout(
            node, -1, "", "H", [vars(Tile(19, 1079, 2, function=65535))]
        )
    assert project.cursor == cursor and project.mdt_layout(node) == before
    assert Tile(19, 29, 2).position("V") == (20, 30)
    assert Tile(19, 1079, 2).position("H") == (30, 30)
    Tile(19, 1079, 2).validate("H")


def test_symbol_write_guard_and_invalid_profile_never_connect(mdt_project):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from editor_gui.plugins.connection.service import ConnectionService

    device = mdt_project.devices[2]
    view = mdt_project.mdt_symbols(device.node_id)
    symbols = {s["slot"]: bytes.fromhex(s["data_hex"]) for s in view["symbols"]}
    service = SimpleNamespace(
        writes_refused=Mock(return_value=True), _run_mdt_symbols=Mock()
    )
    assert (
        ConnectionService.run_mdt_symbols(service, device, symbols, writes=True) is None
    )
    service._run_mdt_symbols.assert_not_called()
    with pytest.raises(ValueError):
        ConnectionService.run_mdt_symbols(service, mdt_project.devices[0], symbols)
    service._run_mdt_symbols.assert_not_called()
    ConnectionService.run_mdt_symbols(service, device, symbols, writes=False)
    service._run_mdt_symbols.assert_called_once_with(
        device, symbols, writes=False, synchronize=False
    )


def test_reserved_symbols_and_import_dialog_target(mdt_project, tmp_path):
    from types import SimpleNamespace

    from editor_gui.plugins.project.ui.mdt_dca import MdtDcaPanel
    from xknxeditor.vendor.mdt.symbols import export_symbol_image

    project = mdt_project
    device = project.devices[2]
    cursor = project.cursor
    with pytest.raises(ValueError, match="slot"):
        project.set_mdt_symbol(device.node_id, 0, "00" * 4096)
    assert project.cursor == cursor
    path = tmp_path / "symbol.png"
    path.write_bytes(export_symbol_image(bytes(4096)))
    panel = MdtDcaPanel()
    panel._target = (project.generation, device.node_id, 2, "previous-application")
    panel._open = SimpleNamespace(ready=lambda: True, result=lambda: [str(path)])
    panel._poll(project)
    assert project.cursor == cursor
    panel._target = (project.generation, device.node_id, 2, device.app.id)
    panel._open = SimpleNamespace(ready=lambda: True, result=lambda: [str(path)])
    panel._poll(project)
    assert project.cursor == cursor + 1
    assert (
        project.mdt_symbols(device.node_id)["symbols"][0]["grayscale_hex"]
        == "00" * 4096
    )


@pytest.mark.parametrize("change", ["generation", "address", "application", "symbols"])
def test_queued_symbol_transfer_rechecks_target(change):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from editor_gui.plugins.project.plugin import ProjectPlugin
    from editor_gui.plugins.project.program_queue import ProgramQueue

    device = SimpleNamespace(
        node_id=1,
        name="Smart",
        individual_address="1.1.1",
        app=SimpleNamespace(id="app"),
    )
    view = {"symbols": [{"slot": 2, "data_hex": "00"}]}
    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _: device,
        mdt_symbol_payload=lambda _, **kw: {
            s["slot"]: bytes.fromhex(s["data_hex"]) for s in view["symbols"]
        },
    )
    connection = SimpleNamespace(run_mdt_symbols=Mock(), xknx=object())
    plugin = SimpleNamespace(
        _api=SimpleNamespace(project=project, connection=connection),
        _program_queue=ProgramQueue(is_busy=lambda: True, start=lambda _: None),
    )
    item = ProjectPlugin._queue_bus_action(
        plugin, 1, "mdt_symbols", symbols={2: b"\x00"}, writes=True
    )
    if change == "generation":
        project.generation += 1
    elif change == "address":
        device.individual_address = "1.1.2"
    elif change == "application":
        device.app.id = "changed"
    else:
        view["symbols"][0]["data_hex"] = "01"
    with pytest.raises(ValueError, match="changed"):
        item.action.start().result()
    connection.run_mdt_symbols.assert_not_called()


def test_symbol_names_follow_locale_without_editing(mdt_project):
    from editor_gui.strings import get_locale, in_english, set_locale

    project = mdt_project
    node = project.devices[2].node_id
    before = project._svc.device_addin_data(project._pid, node, "M0083-A0041")
    cursor = project.cursor
    locale = get_locale()
    try:
        set_locale("en")
        english = project.mdt_symbols(node)
        assert english["slot_info"][2]["name"] == "Symbol 3: Mode: Automatic"
        assert english["slot_info"][8]["name"] == "Symbol 9: Temperature"
        assert english["slot_info"][9]["name"] == "Symbol 10"
        assert english["slot_info"][0]["editable"] is False
        assert english["slot_info"][2]["editable"] is True
        assert len(english["slot_info"]) == 36
        set_locale("de")
        german = project.mdt_symbols(node)
        assert german["slot_info"][2]["name"] == "Symbol 3: Betriebsart: Automatik"
        assert german["groups"]["functional"] == "Funktionsgebundene Symbole"
        assert german["symbols"] == english["symbols"]
        assert german["defaults"] == english["defaults"]
        assert project.mdt_symbols(node) is german
        with in_english():
            assert project.mdt_symbols(node) is english
        assert project.mdt_symbols(node) is german
    finally:
        set_locale(locale)
    assert project.cursor == cursor
    assert project._svc.device_addin_data(project._pid, node, "M0083-A0041") == before


@pytest.mark.parametrize("width", [360, 1000])
def test_symbol_overview_selection_and_protected_canvas(
    mdt_project, monkeypatch, width
):
    from imgui_bundle import imgui

    from editor_gui.plugins.project.ui.mdt_dca import MdtDcaPanel

    project = mdt_project
    device = project.devices[2]
    cursor = project.cursor
    view = project.mdt_symbols(device.node_id)
    selected_slot = 0
    seen = []
    real_selectable = imgui.selectable

    def select(label, current, *args, **kwargs):
        seen.append(label)
        real_selectable(label, current, *args, **kwargs)
        return label == f"##symbol_{selected_slot}", current

    monkeypatch.setattr(imgui, "selectable", select)
    monkeypatch.setattr(
        "editor_gui.plugins.project.ui.mdt_dca.collapsing_header", lambda _: True
    )
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(width, 900)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        panel = MdtDcaPanel()
        for selected_slot in [0, 2, 8, 35]:
            imgui.new_frame()
            imgui.set_next_window_size(imgui.ImVec2(width, 900))
            imgui.begin("Symbols")
            panel.render(device, project)
            imgui.end()
            imgui.end_frame()
            assert panel._states[device.node_id].slot == selected_slot
        assert set(seen) == {f"##symbol_{slot}" for slot in range(36)}
        state = panel._states[device.node_id]
        state.slot = 0
        state.source = None
        imgui.new_frame()
        imgui.begin("Symbols")
        monkeypatch.setattr(imgui, "is_item_active", lambda: True)
        monkeypatch.setattr(imgui, "is_mouse_down", lambda _: True)
        panel._symbol_editor(device, project, state, view)
        imgui.end()
        imgui.end_frame()
        assert not state.drawing
        assert bytes(state.pixels).hex() == view["defaults"][0]["grayscale_hex"]
    finally:
        imgui.destroy_context(context)
    assert project.cursor == cursor
    assert project.mdt_symbols(device.node_id) == view


def test_finishing_symbol_stroke_before_selection_keeps_original_slot(mdt_project):
    from editor_gui.plugins.project.ui.mdt_dca import MdtDcaPanel, _SymbolState

    node = mdt_project.devices[2].node_id
    before = mdt_project.mdt_symbols(node)
    cursor = mdt_project.cursor
    state = _SymbolState(slot=2, pixels=bytearray(4096), drawing=True)
    MdtDcaPanel._finish_drawing(state, node, mdt_project)
    state.slot = 3
    after = mdt_project.mdt_symbols(node)
    assert (
        next(s for s in after["symbols"] if s["slot"] == 2)["grayscale_hex"]
        == "00" * 4096
    )
    assert next(s for s in after["symbols"] if s["slot"] == 3) == next(
        s for s in before["symbols"] if s["slot"] == 3
    )
    assert mdt_project.cursor == cursor + 1
    mdt_project.undo()
    assert mdt_project.mdt_symbols(node) == before


def test_full_libraries_zip_undo_export_and_reload(mdt_project, tmp_path):
    from xknxeditor.proj import export_knxproj
    from xknxeditor.vendor.mdt.symbols import import_symbol_library

    project = mdt_project
    node = project.devices[2].node_id
    profile = project.mdt_dca_profile(project.devices[2])
    before = project.mdt_symbols(node)
    original = project._svc.device_addin_data(project._pid, node, profile.app_id)
    cursor = project.cursor
    for newer in (False, True):
        project.load_mdt_default_symbols(node, new_library=newer)
        assert project.cursor == cursor + 1
        view = project.mdt_symbols(node)
        assert view["symbols"] == [] and view["new_library"] == newer
        expected = {s["slot"]: bytes.fromhex(s["data_hex"]) for s in view["defaults"]}
        assert len(expected) == 36
        assert project.mdt_symbol_payload(node, include_defaults=True) == expected
        project.undo()
        assert (
            project._svc.device_addin_data(project._pid, node, profile.app_id)
            == original
        )
        project.redo()
        assert project.mdt_symbols(node) == view
        project.undo()
    archive = project.export_mdt_symbol_library(node)
    expected = {
        s.slot: s.data for s in import_symbol_library(archive, profile, invert=True)
    }
    project.import_mdt_symbol_library(node, archive, invert=True)
    assert project.cursor == cursor + 1
    assert project.mdt_symbol_payload(node) == expected
    effective = project.mdt_symbol_payload(node, include_defaults=True)
    assert len(effective) == 36 and effective[0] == bytes.fromhex(
        before["defaults"][0]["data_hex"]
    )
    project.undo()
    assert project.mdt_symbols(node) == before
    project.redo()
    export_knxproj(tmp_path / "project.xknx", tmp_path / "library.knxproj", schema="20")
    project.close()
    project.import_knxproj(tmp_path / "library.knxproj", tmp_path / "library.xknx")
    assert (
        project.mdt_symbol_payload(project.devices[2].node_id, include_defaults=True)
        == effective
    )


def test_library_failure_is_atomic_and_sync_guard_precedes_connection(
    mdt_project, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from editor_gui.plugins.connection.service import ConnectionService

    project = mdt_project
    device = project.devices[2]
    node = device.node_id
    before = project.mdt_symbols(node)
    cursor = project.cursor
    with pytest.raises(ValueError):
        project.import_mdt_symbol_library(node, b"broken zip")
    assert project.cursor == cursor and project.mdt_symbols(node) == before
    payload = project.mdt_symbol_payload(node, include_defaults=True)
    service = SimpleNamespace(
        writes_refused=Mock(return_value=True), _run_mdt_symbols=Mock()
    )
    assert (
        ConnectionService.run_mdt_symbols(service, device, payload, synchronize=True)
        is None
    )
    service._run_mdt_symbols.assert_not_called()
    with pytest.raises(ValueError, match="complete"):
        ConnectionService.run_mdt_symbols(
            service, device, {2: payload[2]}, synchronize=True
        )
    service._run_mdt_symbols.assert_not_called()
    monkeypatch.setattr(project, "_mdt_symbol_library", lambda *args: [])
    project._bump(structural=False)
    with pytest.raises(ValueError, match="complete"):
        project.load_mdt_default_symbols(node)
    with pytest.raises(ValueError, match="complete"):
        project.mdt_symbol_payload(node, include_defaults=True)
    assert project.cursor == cursor


def test_library_dialog_snapshots_invert_and_rejects_intervening_edits(
    mdt_project, tmp_path
):
    from types import SimpleNamespace

    from editor_gui.plugins.project.ui.mdt_dca import MdtDcaPanel

    project = mdt_project
    device = project.devices[2]
    path = tmp_path / "symbols.zip"
    path.write_bytes(project.export_mdt_symbol_library(device.node_id))
    panel = MdtDcaPanel()
    panel._target = (project.generation, device.node_id, 0, device.app.id)
    panel._import_kind = "library"
    panel._import_revision = project.revision
    panel._import_invert = True
    project.set_mdt_symbol(device.node_id, 2, "00" * 4096)
    cursor = project.cursor
    panel._open = SimpleNamespace(ready=lambda: True, result=lambda: [str(path)])
    panel._poll(project)
    assert project.cursor == cursor
    assert panel._states[device.node_id].error
    panel._import_revision = project.revision
    panel._open = SimpleNamespace(ready=lambda: True, result=lambda: [str(path)])
    panel._poll(project)
    assert project.cursor == cursor + 1
    assert len(project.mdt_symbols(device.node_id)["symbols"]) == 34
    assert not panel._states[device.node_id].error


def test_queued_full_library_rejects_changed_default_selection(mdt_project):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from editor_gui.plugins.project.plugin import ProjectPlugin
    from editor_gui.plugins.project.program_queue import ProgramQueue

    project = mdt_project
    node = project.devices[2].node_id
    payload = project.mdt_symbol_payload(node, include_defaults=True)
    connection = SimpleNamespace(run_mdt_symbols=Mock(), xknx=object())
    plugin = SimpleNamespace(
        _api=SimpleNamespace(project=project, connection=connection),
        _program_queue=ProgramQueue(is_busy=lambda: True, start=lambda _: None),
    )
    item = ProjectPlugin._queue_bus_action(
        plugin, node, "mdt_symbols", symbols=payload, writes=True, include_defaults=True
    )
    project.set_mdt_symbol_library(node, not project.mdt_symbols(node)["new_library"])
    with pytest.raises(ValueError, match="changed"):
        item.action.start().result()
    connection.run_mdt_symbols.assert_not_called()


def test_pending_library_dialog_disables_edits_and_other_file_actions(
    mdt_project, monkeypatch
):
    from types import SimpleNamespace

    from imgui_bundle import imgui

    from editor_gui.plugins.project.ui.mdt_dca import MdtDcaPanel

    project = mdt_project
    device = project.devices[2]
    panel = MdtDcaPanel()
    panel._generation = project.generation
    panel._open = SimpleNamespace(ready=lambda: False)
    disabled = []
    rendered = []
    monkeypatch.setattr(imgui, "begin_disabled", lambda value: disabled.append(value))
    monkeypatch.setattr(imgui, "end_disabled", lambda: disabled.pop())
    monkeypatch.setattr(
        panel, "_render_device", lambda *args: rendered.append(tuple(disabled))
    )
    panel.render(device, project)
    assert rendered == [(True,)] and disabled == []
