"""Typed DALI parameter exchange preserves hidden values and module identities."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest
from editor_gui.device import Device
from editor_gui.insta_dgwd import export_configuration, import_configuration, revision
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.project.service import ProjectService

from xknxeditor.prod import parse_application_xml

APP = "M-0009_A-E80A-22-7594-O000A"


def application():
    data = (Path(__file__).parent / "fixtures/insta_dgwd_parameters.xml").read_bytes()
    return parse_application_xml(data, "M-0009")[0]


def device():
    return Device(7, "DALI", application(), "1.1.8")


def modified(data):
    root = ET.fromstring(data)
    top = root.find("DataBlock/ParameterBlock")
    top.find("ParametersColor/ParameterEntryColor").set("Value", str(-16711165))
    top.find("ParametersRaw/ParameterEntryRaw").set("Value", "FF1000")
    top.find("ParametersText/ParameterEntryText").set("Value", "A & B")
    for i, module in enumerate(top.findall("ParameterBlocks/ParameterBlock")):
        module.find("ParametersNumber/ParameterEntryNumber").set("Value", str(50 + i))
    return ET.tostring(root)


def test_complete_roundtrip_keeps_defaults_modules_raw_and_signed_color():
    gateway = device()
    before = export_configuration(gateway)
    root = ET.fromstring(before)
    assert root.get("DataVersion") == "1"
    assert root.find("DataBlock").get("ApplicationNo") == "59402"
    top = root.find("DataBlock/ParameterBlock")
    assert top.find("ParametersRaw/ParameterEntryRaw").get("Value") == "00FF80"
    number = top.find("ParametersFloat/ParameterEntryFloat").get("Value")
    assert "E" not in number.upper() and float(number) == 300
    assert top.find("ParametersColor/ParameterEntryColor").get("Value") == str(
        0xFF12ABEF - 2**32
    )
    modules = top.findall("ParameterBlocks/ParameterBlock")
    assert len(modules) == 2
    assert all(
        m.get("ParameterBlockId").startswith("P-0000_DI-0_M-0009_") for m in modules
    )
    assert (
        modules[0].find("ParametersNumber/ParameterEntryNumber").get("ParameterId")
        == f"{APP}_MD-1_P-1_R-1"
    )
    assert import_configuration(gateway, before) == []
    edits = dict(import_configuration(gateway, modified(before)))
    assert edits[f"{APP}_P-2_R-2"] == "#010203"
    assert edits[f"{APP}_P-3_R-3"] == "/xAA"
    assert edits[f"{APP}_P-1_R-1"] == "A & B"
    assert edits[f"{APP}_MD-1_M-1_MI-1_P-1_R-1"] == "50"
    assert edits[f"{APP}_MD-1_M-1_MI-2_P-1_R-1"] == "51"
    assert (
        export_configuration(gateway) == before and not gateway.parameter_instance_refs
    )


@pytest.mark.parametrize(
    "problem",
    [
        "manufacturer",
        "application",
        "version",
        "future",
        "missing",
        "duplicate",
        "module",
        "type",
        "raw",
        "number",
        "float",
        "color",
        "text",
        "groups",
        "unknown",
    ],
)
def test_invalid_import_is_rejected_without_mutating_any_value(problem):
    gateway = device()
    before = export_configuration(gateway)
    root = ET.fromstring(modified(before))
    block = root[0]
    top = block[0]
    if problem in ("manufacturer", "application", "version"):
        block.set(
            {
                "manufacturer": "ManufacturerId",
                "application": "ApplicationNo",
                "version": "ApplicationVersion",
            }[problem],
            "0",
        )
    elif problem == "future":
        root.set("DataVersion", "2")
    elif problem == "missing":
        top.find("ParametersColor").clear()
    elif problem == "duplicate":
        container = top.find("ParametersColor")
        container.append(deepcopy(container[0]))
    elif problem == "module":
        top.find("ParameterBlocks/ParameterBlock").set("ParameterBlockId", "wrong")
    elif problem == "type":
        entry = top.find("ParametersColor/ParameterEntryColor")
        entry.tag = "ParameterEntryNumber"
    elif problem in ("raw", "color", "text", "number", "float"):
        kind, value = {
            "raw": ("Raw", "GG"),
            "color": ("Color", "4294967295"),
            "text": ("Text", "x" * 100),
            "number": ("Number", "256"),
            "float": ("Float", "NaN"),
        }[problem]
        top.find(f"Parameters{kind}/ParameterEntry{kind}").set("Value", value)
    elif problem == "groups":
        ET.SubElement(
            block.find("GroupAddresseBlock/GroupAddresses"), "GroupAddressEntry"
        )
    else:
        top.find("ParametersColor/ParameterEntryColor").set(
            "ParameterId", "P-999_R-999"
        )
    with pytest.raises(ValueError):
        import_configuration(gateway, ET.tostring(root))
    assert export_configuration(gateway) == before


@pytest.mark.parametrize(
    "data", [b"<wrong/>", b"<", b'<!DOCTYPE x [<!ENTITY a "data">]><DataContainer/>']
)
def test_invalid_xml_and_doctype_rejected(data):
    with pytest.raises(ValueError):
        import_configuration(device(), data)


def test_fingerprint_and_project_prefix_do_not_block_module_matching():
    gateway = device()
    root = ET.fromstring(export_configuration(gateway))
    root[0].set("ApplicationFingerprint", "ABCD")
    for module in root.findall(".//ParameterBlocks/ParameterBlock"):
        module.set(
            "ParameterBlockId",
            module.get("ParameterBlockId")
            .replace("P-0000_DI-0", "P-ABCD-1_DI-99")
            .replace("7594", "ABCD"),
        )
    assert import_configuration(gateway, ET.tostring(root)) == []


def test_empty_child_module_containers_are_valid():
    gateway = device()
    root = ET.fromstring(export_configuration(gateway))
    for module in root.findall(".//ParameterBlocks/ParameterBlock"):
        ET.SubElement(module, "ParameterBlocks")
    assert import_configuration(gateway, ET.tostring(root)) == []


def test_float_formatting_preserves_values_without_a_spurious_edit():
    gateway = device()
    root = ET.fromstring(export_configuration(gateway))
    entry = root.find(".//ParametersFloat/ParameterEntryFloat")
    entry.set("Value", "300")
    assert import_configuration(gateway, ET.tostring(root)) == []
    entry.set("Value", "300.5")
    assert (
        dict(import_configuration(gateway, ET.tostring(root)))[f"{APP}_P-6_R-6"]
        == "300.5"
    )


def test_color_case_does_not_create_an_edit():
    gateway = device()
    gateway.apply_param_values({f"{APP}_P-2_R-2": "#12abef"})
    assert import_configuration(gateway, export_configuration(gateway)) == []


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_stored_float_is_rejected_on_export(value):
    gateway = device()
    gateway.apply_param_values({f"{APP}_P-6_R-6": value})
    with pytest.raises(ValueError, match="floating-point"):
        export_configuration(gateway)


@pytest.mark.parametrize(
    "app_id,manufacturer",
    [
        ("M-0008_A-C030-21-AA50-O000A", "M-0008"),
        (APP, "M-0009"),
        ("M-008E_A-C031-22-853A-O000A", "M-008E"),
    ],
)
def test_exchange_is_reachable_through_the_mapped_plugin_panel(
    monkeypatch, app_id, manufacturer
):
    from types import SimpleNamespace

    from editor_gui.plugin_params import collect_from_device, mapping_for_device
    from editor_gui.plugins.project.ui import plugin_params_panel as panel_ui

    source = (Path(__file__).parent / "fixtures/insta_dgwd_parameters.xml").read_bytes()
    source = source.replace(APP.encode(), app_id.encode()).replace(
        b'RefId="M-0009"', f'RefId="{manufacturer}"'.encode()
    )
    gateway = Device(
        7, "Gateway", parse_application_xml(source, manufacturer)[0], "1.1.7"
    )
    mapping = mapping_for_device(gateway)
    assert mapping is not None and not mapping.blob_reader
    assert collect_from_device(gateway, mapping)
    project = Mock()
    render = Mock()
    monkeypatch.setattr(
        panel_ui, "DgwdExchange", lambda: SimpleNamespace(render=render)
    )
    monkeypatch.setattr(panel_ui, "imgui", Mock())
    monkeypatch.setattr(panel_ui, "status_color", Mock())
    panel = panel_ui.PluginParamsPanel(lambda *_: None, _dgwd_project=project)
    monkeypatch.setattr(panel, "_render_sections", Mock())
    panel.render(gateway)
    render.assert_called_once_with(gateway, project)


@pytest.mark.parametrize("problem", ["unknown", "missing", "duplicate", "nested"])
def test_module_validation_also_applies_to_compatible_older_files(problem):
    gateway = device()
    root = ET.fromstring(export_configuration(gateway))
    root[0].set("ApplicationVersion", "33")
    modules = root.find(".//ParameterBlocks")
    if problem == "missing":
        modules.remove(modules[0])
    elif problem == "nested":
        ET.SubElement(ET.SubElement(modules[0], "ParameterBlocks"), "ParameterBlock")
    else:
        extra = deepcopy(modules[0])
        if problem == "unknown":
            extra.set("ParameterBlockId", extra.get("ParameterBlockId") + "0")
            for child in extra:
                child.clear()
        modules.append(extra)
    with pytest.raises(ValueError):
        import_configuration(gateway, ET.tostring(root))


def test_unsupported_application_is_rejected():
    gateway = device()
    gateway.app = parse_application_xml(
        (Path(__file__).parent / "fixtures/insta_dgwd_parameters.xml")
        .read_bytes()
        .replace(b"E80A", b"FFFF"),
        "M-0009",
    )[0]
    with pytest.raises(ValueError, match="Unsupported"):
        export_configuration(gateway)


def test_compatible_older_data_resets_new_fields_before_import():
    gateway = device()
    before = export_configuration(gateway)
    gateway.apply_param_values({f"{APP}_MD-1_M-1_MI-2_P-3_R-4": "99"})
    root = ET.fromstring(before)
    root.set("DataVersion", "0")
    root[0].set("ApplicationVersion", "33")
    module = root.findall(".//ParameterBlocks/ParameterBlock")[1]
    entries = module.find("ParametersNumber")
    entries.remove(entries[-1])
    edits = dict(import_configuration(gateway, ET.tostring(root)))
    assert edits[f"{APP}_MD-1_M-1_MI-2_P-3_R-4"] == "7"


@pytest.mark.parametrize("count", [0, 1, 3])
def test_import_changes_module_count_atomically_and_undo_restores_it(
    tmp_path, monkeypatch, count
):
    source = (Path(__file__).parent / "fixtures/insta_dgwd_parameters.xml").read_bytes()
    source = source.replace(b'Count="2"', f'ParameterRefId="{APP}_P-5_R-5"'.encode())
    app = parse_application_xml(source, "M-0009")[0]
    donor = Device(8, "Donor", app, "1.1.8")
    donor.apply_param_values({f"{APP}_P-5_R-5": str(count)})
    donor.get_module_instances()
    donor.apply_param_values(
        {f"{APP}_MD-1_M-1_MI-{i}_P-3_R-4": str(30 + i) for i in range(1, count + 1)}
    )
    imported = export_configuration(donor)
    project = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    project.set_logger(Mock())
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    node = project.add_device("P-1", "HP-1", "DALI", app)
    gateway = project.find_device_by_node_id(node)
    before = export_configuration(gateway)
    if count:
        bad = ET.fromstring(imported)
        bad.find(
            ".//ParameterBlocks/ParameterBlock/ParametersNumber/ParameterEntryNumber"
        ).set("Value", "999")
        cursor = project.cursor
        with pytest.raises(ValueError):
            project.import_insta_dgwd(gateway, revision(gateway), ET.tostring(bad))
        assert export_configuration(gateway) == before and project.cursor == cursor
    project.import_insta_dgwd(gateway, revision(gateway), imported)
    current = project.find_device_by_node_id(node)
    assert export_configuration(current) == imported
    assert len(current.get_module_instances()) == count
    assert len(current.module_instances) == count
    assert project.undo()
    assert export_configuration(project.find_device_by_node_id(node)) == before
    assert project.redo()
    assert export_configuration(project.find_device_by_node_id(node)) == imported
    project.close()
    project.open(tmp_path / "project.xknx")
    assert export_configuration(project.find_device_by_node_id(node)) == imported
    project.close()


def test_project_import_is_one_undo_step_and_rejects_stale_revision(
    tmp_path, monkeypatch
):
    app = application()
    catalog = CatalogService(tmp_path / "c.xknxcatalog")
    project = ProjectService(catalog)
    project.set_logger(Mock())
    project.new(tmp_path / "p.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    node = project.add_device("P-1", "HP-1", "DALI", app)
    gateway = project.find_device_by_node_id(node)
    before = project.export_insta_dgwd(gateway)
    rev = revision(gateway)
    updated = project.import_insta_dgwd(gateway, rev, modified(before))
    current = project.find_device_by_node_id(node)
    assert current.get_param_value(f"{APP}_P-2_R-2") == "#010203"
    assert current.get_param_value(f"{APP}_P-3_R-3") == "/xAA"
    assert updated == revision(current) != rev
    with pytest.raises(ValueError):
        project.import_insta_dgwd(current, rev, before)
    assert project.undo()
    assert project.export_insta_dgwd(project.find_device_by_node_id(node)) == before
    assert project.redo()
    assert revision(project.find_device_by_node_id(node)) == updated
    project.close()
    project.open(tmp_path / "p.xknx")
    assert revision(project.find_device_by_node_id(node)) == updated
    project.close()


def test_import_reconciles_objects_and_restores_links_on_undo(tmp_path, monkeypatch):
    app = application()
    project = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    project.set_logger(Mock())
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    node = project.add_device("P-1", "HP-1", "DALI", app)
    gateway = project.find_device_by_node_id(node)
    ga = project.create_group_address_value(0x0A01, "Light")
    co = gateway.get_visible_com_objects()[0]
    project.link_com_object_to_ga(co.db_id, ga, is_sending=True)
    gateway = project.find_device_by_node_id(node)
    links = project._capture_links(gateway)
    before = project.export_insta_dgwd(gateway)
    tree = ET.fromstring(before)
    tree.find(f".//ParameterEntryNumber[@ParameterId='{APP}_P-5_R-5']").set(
        "Value", "0"
    )
    cursor = project.cursor
    project.import_insta_dgwd(gateway, revision(gateway), ET.tostring(tree))
    assert project.cursor == cursor + 1
    current = project.find_device_by_node_id(node)
    assert not current.get_visible_com_objects()
    assert not project._capture_links(current)
    assert project.undo()
    current = project.find_device_by_node_id(node)
    assert project._capture_links(current) == links
    assert project.export_insta_dgwd(current) == before
    project.close()


@pytest.mark.parametrize(
    "problem", ["generation", "address", "application", "revision"]
)
def test_file_dialog_rejects_changed_target(tmp_path, monkeypatch, problem):
    from types import SimpleNamespace

    from editor_gui.plugins.project.ui import insta_dgwd as ui

    gateway = device()
    project = Mock(generation=1)
    path = tmp_path / "import.dgwd"
    path.write_bytes(export_configuration(gateway))
    expected = (
        1,
        gateway.node_id,
        gateway.app.id,
        gateway.individual_address,
        "revision",
    )
    view = ui.Exchange(
        open_dialog=Mock(ready=lambda: True, result=lambda: [str(path)]),
        expected=expected,
    )
    if problem == "generation":
        project.generation = 2
    elif problem == "address":
        gateway.individual_address = "1.1.99"
    elif problem == "application":
        gateway.app = None
    else:
        project.import_insta_dgwd.side_effect = ValueError("Changed revision")
    monkeypatch.setattr(
        ui,
        "imgui",
        SimpleNamespace(
            push_id=Mock(),
            pop_id=Mock(),
            begin_disabled=Mock(),
            end_disabled=Mock(),
            button=lambda _: False,
            text_wrapped=Mock(),
        ),
    )
    monkeypatch.setattr(ui, "button_width", lambda _: 1)
    monkeypatch.setattr(ui, "same_line_if_fits", Mock())
    view.render(gateway, project)
    assert view.open_dialog is None and view.message
    if problem == "revision":
        project.import_insta_dgwd.assert_called_once_with(
            gateway, "revision", path.read_bytes()
        )
    else:
        project.import_insta_dgwd.assert_not_called()


def test_export_file_keeps_the_snapshot_from_when_dialog_opened(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from editor_gui.plugins.project.ui import insta_dgwd as ui

    gateway = device()
    project = Mock(generation=1)
    payload = export_configuration(gateway)
    project.export_insta_dgwd.return_value = payload
    path = tmp_path / "export.dgwd"
    dialog = Mock(ready=lambda: True, result=lambda: str(path))
    save = Mock(return_value=dialog)
    monkeypatch.setattr(ui.file_dialogs, "save_file", save)
    buttons = iter((False, True, False, False))
    monkeypatch.setattr(
        ui,
        "imgui",
        SimpleNamespace(
            push_id=Mock(),
            pop_id=Mock(),
            begin_disabled=Mock(),
            end_disabled=Mock(),
            button=lambda _: next(buttons),
            text_wrapped=Mock(),
        ),
    )
    monkeypatch.setattr(ui, "button_width", lambda _: 1)
    monkeypatch.setattr(ui, "same_line_if_fits", Mock())
    view = ui.Exchange()
    view.render(gateway, project)
    assert view.save_dialog is dialog
    assert save.call_args.kwargs["default_path"].endswith(".dgwd")
    project.export_insta_dgwd.return_value = b"changed"
    view.render(gateway, project)
    assert path.read_bytes() == payload
    assert view.save_dialog is None and not view.export_data


def test_browser_exchange_uses_uploaded_file_and_downloads_export(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from editor_gui import web
    from editor_gui.plugins.project.ui import insta_dgwd as ui
    from editor_gui.web.dialogs import WebOpenFile, WebSaveFile
    from editor_gui.web.server import WebHub

    hub = WebHub(tmp_path / "files")
    monkeypatch.setattr(web, "active_hub", hub)
    offered = []
    monkeypatch.setattr(hub, "download_when_written", offered.append)
    gateway = device()
    payload = export_configuration(gateway)
    project = Mock(generation=1)
    project.export_insta_dgwd.return_value = payload
    buttons = iter((True, False, False, False, False, True, False, False))
    monkeypatch.setattr(
        ui,
        "imgui",
        SimpleNamespace(
            push_id=Mock(),
            pop_id=Mock(),
            begin_disabled=Mock(),
            end_disabled=Mock(),
            button=lambda _: next(buttons),
            text_wrapped=Mock(),
        ),
    )
    monkeypatch.setattr(ui, "button_width", lambda _: 1)
    monkeypatch.setattr(ui, "same_line_if_fits", Mock())
    view = ui.Exchange()
    view.render(gateway, project)
    assert isinstance(view.open_dialog, WebOpenFile)
    assert view.open_dialog.event()["accept"] == ".dgwd"
    (hub.files.root / "upload.dgwd").write_bytes(payload)
    view.open_dialog.upload_event({"state": "done", "names": ["upload.dgwd"]})
    view.render(gateway, project)
    project.import_insta_dgwd.assert_called_once_with(
        gateway, revision(gateway), payload
    )
    view.render(gateway, project)
    assert isinstance(view.save_dialog, WebSaveFile)
    view.save_dialog.answer(
        {"ok": True, "name": "export", "filter": 0, "download": True}
    )
    view.render(gateway, project)
    target = hub.files.root / "export.dgwd"
    assert target.read_bytes() == payload and offered == [target]
