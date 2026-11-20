from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from imgui_bundle import imgui

from editor_gui import web
from editor_gui.plugin_blob_readers.dali_backup import e64pro
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import e64pro as e64pro_ui
from editor_gui.plugins.project.ui.e64pro import Workspace, XmlExchange
from editor_gui.plugins.project.ui.e64pro_colour import Colour
from editor_gui.plugins.project.ui.plugin_params_panel import PluginParamsPanel
from editor_gui.web.server import WebHub
from xknxeditor.prod import parse_application_xml
from xknxeditor.prod.application import Application
from xknxeditor.proj import export_knxproj, import_knxproj

ROOT = next(p for p in Path(__file__).parents if (p / "packages/dali").exists())
PAYLOAD = (ROOT / "packages/dali/tests/fixtures/ipas_e64pro_backup.dat").read_bytes()
NAME = "Dalie64-Backup-Store"


@pytest.mark.parametrize(
    ("group_type", "mode", "original", "edited"),
    [
        ("7", "4", "255;127;155;0", "255;64;155;0"),
        ("6", "8", "255;120;57;121", "255;120;57;66"),
    ],
)
def test_rgb_scene_uses_four_byte_dca_encoding(group_type, mode, original, edited):
    payload = f"""<device type="e64pro">
      <groups><Group index="7" ctype="{group_type}" /></groups>
      <scenes><Scene index="9">
        <SceneItem itemtype="Dalie64.DcaApp.Group" itemindex="7" value="50"
                   setting="3" color-type="{mode}" color="{original}" />
      </Scene></scenes>
    </device>""".encode()
    edit = e64pro.Edit("scenes", "9", "colour", 0, mode, edited)
    root = e64pro.document(e64pro.apply(payload, edit))
    assert root.find("scenes/Scene/SceneItem").attrib == {
        "itemtype": "Dalie64.DcaApp.Group",
        "itemindex": "7",
        "value": "50",
        "setting": "3",
        "color-type": mode,
        "color": edited,
    }
    for invalid in ("255;64;155", "255;256;155;0", "1.0;0;0;0"):
        with pytest.raises(ValueError):
            e64pro.apply(payload, replace(edit, value=invalid))


def test_xml_export_flags_populated_slots_without_changing_project_state():
    payload = b"""<device type="e64pro"><scenes>
      <Scene index="0" isDirty="False"><SceneItem value="10" /></Scene>
      <Scene index="1" isDirty="True" />
    </scenes></device>"""
    exported = e64pro.document(e64pro.export_configuration(payload))
    assert [slot.get("isDirty") for slot in exported.findall("scenes/Scene")] == [
        "True",
        "False",
    ]
    assert [
        slot.get("isDirty") for slot in e64pro.document(payload).findall("scenes/Scene")
    ] == ["False", "True"]


def test_service_undo_export_and_reimport(tmp_path, monkeypatch):
    sample = parse_application_xml(
        (ROOT / "packages/download/tests/fixtures/powertool/21.xml").read_bytes(),
        "M-0007",
    )[0]
    app = Application(
        replace(sample.program, id="M-00EF_A-DA11-22-A545"), sample.version, "M-00EF"
    )
    project = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "fixture.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    try:
        node = project.add_device("P-1", "HP-1", "e64 Pro", app)

        def device():
            return project.find_device_by_node_id(node)

        project.persist_device_blob(device(), NAME, PAYLOAD)
        before = project.cursor
        op = e64pro.Edit("scenes", "0", "colour", 0, "2", "2850")
        project.edit_e64pro(device(), PAYLOAD, op)
        changed = project.device_binary_data(device(), NAME)
        assert changed == e64pro.apply(PAYLOAD, op)
        assert project.cursor == before + 1
        with pytest.raises(ValueError, match="changed"):
            project.edit_e64pro(device(), PAYLOAD, op)
        project.undo()
        assert project.device_binary_data(device(), NAME) == PAYLOAD
        project.redo()
        assert project.device_binary_data(device(), NAME) == changed
        export_knxproj(project.working_path, tmp_path / "copy.knxproj")
        project.close()
        import_knxproj(tmp_path / "copy.knxproj", tmp_path / "copy.xknx")
        project.open(tmp_path / "copy.xknx")
        assert project.device_binary_data(project.devices[0], NAME) == changed
        device = project.devices[0]
        cursor = project.cursor
        xml = ET.fromstring(project.export_e64pro(device))
        xml.set("IndividualAddress", "15.15.255")
        xml.set("Project", "Source project")
        scene = xml.find("scenes/Scene[@index='0']")
        assert scene is not None
        scene.set("description", "Prüfszene")
        with pytest.raises(ValueError, match="complete"):
            project.import_e64pro(device, changed, b'<device type="e64pro" />')
        with pytest.raises(ValueError, match="changed"):
            project.import_e64pro(device, PAYLOAD, ET.tostring(xml, encoding="utf-8"))
        assert project.cursor == cursor
        imported = project.import_e64pro(
            device, changed, ET.tostring(xml, encoding="utf-8")
        )
        assert project.cursor == cursor + 1
        backup = e64pro.document(imported)
        assert backup.get("IndividualAddress") == device.individual_address
        assert backup.get("Project") != "Source project"
        scene = backup.find("scenes/Scene[@index='0']")
        assert scene is not None and scene.get("description") == "Prüfszene"
        assert ET.tostring(backup) == ET.tostring(
            ET.fromstring(project.export_e64pro(project.devices[0]))
        )
        project.undo()
        assert project.device_binary_data(project.devices[0], NAME) == changed
    finally:
        project.close()


@pytest.mark.parametrize(
    ("function", "value"),
    [
        ("ColourTemp", "3250"),
        ("ColourXY", "0.3450;0.3500;0.7500"),
        ("ColourRGB", "255;127;155;0"),
        ("ColourRGBW", "255;120;57;121"),
        ("ColourHSV", "0.7843;0.3686;0.7490"),
        ("ColourHSVW", "0.1194;0.8400;0.7600;255"),
    ],
)
def test_time_control_colour_edit_preserves_other_command_fields(function, value):
    data = b"""<device type="e64pro"><templates><Tpl index="0"><Commands>
      <TplCmd func="SetValue" value="20" fadeIn="VFadeIn5" time="07:15"
              weekdays="0111111"/>
    </Commands></Tpl></templates></device>"""
    converted = e64pro.apply(
        data, e64pro.Edit("templates", "0", "set", 0, "func", function)
    )
    edit = e64pro.Edit("templates", "0", "set", 0, "value", value)
    result = e64pro.document(e64pro.apply(converted, edit))
    row = result.find("templates/Tpl/Commands/TplCmd")
    assert row is not None
    assert row.attrib == {
        "func": function,
        "value": value,
        "fadeIn": "CFadeIn1",
        "time": "07:15",
        "weekdays": "0111111",
        "scaleMode": "ScaleNormal",
    }
    with pytest.raises(ValueError):
        e64pro.apply(converted, replace(edit, value="bad"))
    if function == "ColourTemp":
        legacy = converted.decode("utf-16le").replace(
            'value="3000"', 'value="3000;0;0;0"'
        )
        updated = e64pro.document(e64pro.apply(legacy.encode("utf-16le"), edit))
        row = updated.find("templates/Tpl/Commands/TplCmd")
        assert row is not None and row.get("value") == "3250;0;0;0"


def test_xml_buttons_export_and_confirm_import(tmp_path, monkeypatch):
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.display_size = imgui.ImVec2(850, 650)
        device = SimpleNamespace(
            node_id=52, individual_address="1.0.52", display_name="Test gateway"
        )
        path = tmp_path / "test.xml"
        exchange = XmlExchange()
        xml = e64pro.export_configuration(PAYLOAD)
        imports = []
        project = SimpleNamespace(
            export_e64pro=lambda _: xml,
            import_e64pro=lambda *args: imports.append(args),
        )
        monkeypatch.setattr(
            e64pro_ui.file_dialogs,
            "open_file",
            lambda *args, **kwargs: SimpleNamespace(
                ready=lambda: True, result=lambda: [str(path)]
            ),
        )
        monkeypatch.setattr(
            e64pro_ui.file_dialogs,
            "save_file",
            lambda *args, **kwargs: SimpleNamespace(
                ready=lambda: True, result=lambda: str(path)
            ),
        )
        original_button = imgui.button
        click = ""
        confirm = False

        def button(label, *args):
            result = original_button(label, *args)
            return result or label == click

        monkeypatch.setattr(imgui, "button", button)
        monkeypatch.setattr(
            e64pro_ui, "dialog_buttons", lambda *_: 0 if confirm else None
        )

        def frame():
            imgui.new_frame()
            imgui.set_next_window_size(imgui.ImVec2(600, 400))
            imgui.begin("DCA")
            exchange.render(device, PAYLOAD, project)
            imgui.end()
            imgui.render()

        click = S.E64_EXPORT_XML
        frame()
        click = ""
        frame()
        assert path.read_bytes() == xml
        click = S.E64_IMPORT_XML
        frame()
        click = ""
        frame()
        assert not imports
        confirm = True
        frame()
        assert imports == [(device, PAYLOAD, xml)]
    finally:
        imgui.destroy_context(context)


@pytest.mark.parametrize("browser", [False, True])
def test_render_all_views_at_normal_entry(tmp_path, monkeypatch, browser):
    ctx = imgui.create_context()
    try:
        io = imgui.get_io()
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.display_size = imgui.ImVec2(1500, 900)
        if browser:
            monkeypatch.setattr(web, "active_hub", WebHub(tmp_path))
        device = SimpleNamespace(
            node_id=52,
            app=SimpleNamespace(id="M-00EF_A-DA11-22-A545"),
            binary_data=[SimpleNamespace(name=NAME, data=PAYLOAD)],
        )
        panel = PluginParamsPanel(
            _on_edit=lambda *_: pytest.fail("unexpected edit"),
            _mapping_for=lambda _: SimpleNamespace(
                blob_reader="dali_backup", provider="", title="e64"
            ),
            _sections_for=lambda *_: pytest.fail("DCA must render only backup fields"),
            _e64_write=lambda *_: pytest.fail("unexpected write"),
        )
        workspace = Workspace()
        panel._e64_views[52] = workspace
        begin_tab_item = imgui.begin_tab_item
        for view in ("ecgs", "inputs", "scenes", "effects", "templates", "raw"):

            def select_view(label, target=view):
                flags = (
                    imgui.TabItemFlags_.set_selected
                    if label.endswith(f"##{target}")
                    else 0
                )
                return begin_tab_item(label, flags=flags)

            monkeypatch.setattr(imgui, "begin_tab_item", select_view)
            for frame in range(3):
                if view == "scenes" and frame == 2:
                    mode, raw = (
                        ("8", "255;120;57;121") if browser else ("4", "255;127;155;0")
                    )
                    colour = Colour.parse(mode, raw)
                    assert colour is not None
                    workspace.colour_origin = ("scenes", 0, colour)
                    workspace.colour_draft = colour
                    workspace.colour_modes = (mode,)
                    workspace.open_colour = True
                imgui.new_frame()
                imgui.set_next_window_size(imgui.ImVec2(650 if browser else 1400, 800))
                imgui.begin("DCA")
                panel.render(device)
                imgui.end()
                imgui.render()
            assert imgui.get_draw_data().total_vtx_count > 0
            if view == "scenes":
                assert workspace.colour_draft is not None
    finally:
        imgui.destroy_context(ctx)


@pytest.mark.parametrize("confirm", [False, True])
def test_colour_popup_writes_only_after_apply(monkeypatch, confirm):
    from editor_gui.plugins.project.ui import e64pro as panel_module

    ctx = imgui.create_context()
    try:
        io = imgui.get_io()
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.display_size = imgui.ImVec2(850, 650)
        workspace = Workspace()
        writes = []
        keys = {"open": False, "close": False}
        original_tab = imgui.begin_tab_item
        original_button = imgui.button

        def tab(label):
            flags = (
                imgui.TabItemFlags_.set_selected if label.endswith("##scenes") else 0
            )
            return original_tab(label, flags=flags)

        def button(label, *args):
            result = original_button(label, *args)
            if label.endswith("##colour") and keys["open"]:
                keys["open"] = False
                return True
            if keys["close"] and label == ("Apply" if confirm else "Cancel"):
                keys["close"] = False
                return True
            return result

        monkeypatch.setattr(imgui, "begin_tab_item", tab)
        monkeypatch.setattr(imgui, "button", button)
        monkeypatch.setattr(
            panel_module, "edit_colour", lambda colour: colour.with_component(0, "2850")
        )
        root = e64pro.document(PAYLOAD)
        device = SimpleNamespace(node_id=52)
        for frame in range(8):
            keys["open"] = frame == 3
            keys["close"] = frame == 6
            imgui.new_frame()
            imgui.set_next_window_size(imgui.ImVec2(760, 590))
            imgui.begin("DCA")
            workspace.render(
                device, PAYLOAD, root, lambda _device, _data, edit: writes.append(edit)
            )
            imgui.end()
            imgui.render()
            if frame < 6:
                assert not writes
        assert not keys["close"]
        assert writes == (
            [e64pro.Edit("scenes", "0", "colour", 0, "2", "2850")] if confirm else []
        )
    finally:
        imgui.destroy_context(ctx)
