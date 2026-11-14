"""All supplied product profiles through the shared native service."""

import pytest

from editor_gui.plugin_params import collect_from_device, mapping_for_device
from editor_gui.plugin_providers.powertool import PROVIDER
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from packages.vendor.tests.powertool_fixtures import open_archive
from xknxeditor.prod import parse_application_xml
from xknxeditor.vendor.powertool import BLOB_NAME
from xknxeditor.vendor.powertool.family_codec import PROFILES

from .test_powertool import assert_current_blob, current
from .test_powertool_blob import host_blob

CASES = [
    ("DI-3", "ObjektRTR"),
    ("DI-4", "PraesenzStandard"),
    ("DI-5", "PraesenzKomfort"),
    ("DI-11", "ObjektRTRunified"),
    ("DI-12", "SoloRTR3"),
    ("DI-13", "SoloRTR3oBE"),
]


def family_project(tmp_path, monkeypatch, case, profile, service=None):
    number, main, pei, _ = PROFILES[profile]
    with open_archive("host-products.zip") as archive:
        apps = [
            parse_application_xml(
                archive.read(
                    next(
                        n
                        for n in archive.namelist()
                        if n.startswith(f"M-0007/M-0007_A-{number:04X}-{version:02X}-")
                    )
                ),
                "M-0007",
            )[0]
            for version in (main, pei)
        ]
    project = service or ProjectService(
        CatalogService(tmp_path / "catalog.xknxcatalog")
    )
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(
        project, "_second_application_source", lambda _: lambda: apps[1]
    )
    monkeypatch.setattr(project, "_resolve_app", lambda *args: apps[0])
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    node_id = project.add_device("P-1", "HP-1", profile, apps[0])
    assert node_id is not None
    project.persist_device_blob(
        current(project, node_id), "PT2EtsPlugin", host_blob(case)
    )
    return project, node_id


@pytest.mark.parametrize(("case", "profile"), CASES)
def test_saved_family_edit_undo_and_reopen(tmp_path, monkeypatch, case, profile):
    project, node_id = family_project(tmp_path, monkeypatch, case, profile)
    device = current(project, node_id)
    before = project.cursor
    capabilities = PROVIDER.capabilities(device)
    assert capabilities.supported and capabilities.preflight, capabilities.reason
    assert capabilities.programming_error is None
    mapping = mapping_for_device(device)
    assert mapping is not None
    rows = [p for s in collect_from_device(device, mapping) for p in s.parameters]
    assert any(p.writable for p in rows)
    row = next(
        p
        for p in rows
        if p.writable
        and p.choices
        and len(p.choices) > 1
        and "/application/" in p.ref_id
    )
    option = next(value for value, _ in row.choices if value != row.value)
    assert project.prepared_download(device)
    assert project.cursor == before
    assert project.device_binary_data(device, BLOB_NAME) is None
    assert project.device_binary_data(device, "PT2EtsPlugin") == host_blob(case)
    project.edit_plugin_parameter(device, row.ref_id, option)
    assert project.cursor == before + 1
    device = current(project, node_id)
    assert not device.native_error
    blob = project.device_binary_data(device, BLOB_NAME)
    assert blob
    active = assert_current_blob(project, node_id)
    assert project.prepared_download(device)
    assert device.get_visible_com_objects()
    device.release_dynamic_ui()
    assert device.get_visible_com_objects() and device.get_com_object_modules()
    project.undo()
    assert project.device_binary_data(current(project, node_id), BLOB_NAME) is None
    assert project.device_binary_data(
        current(project, node_id), "PT2EtsPlugin"
    ) == host_blob(case)
    project.redo()
    assert project.device_binary_data(current(project, node_id), BLOB_NAME) == blob
    project.close()
    project.open(tmp_path / "project.xknx")
    assert project.device_binary_data(current(project, node_id), BLOB_NAME) == blob
    assert (
        project.device_binary_data(current(project, node_id), "PT2EtsPlugin") == active
    )
    assert_current_blob(project, node_id)
    assert project.prepared_download(current(project, node_id))
    project.close()


@pytest.mark.parametrize("number", (0x3120, 0x3121, 0x3130, 0x3131, 0x3140))
def test_normal_presence_detector_parameters_use_standard_path(
    tmp_path, monkeypatch, number
):
    from editor_gui.device import collect_ui_parameters
    from editor_gui.plugin_providers import provider_for
    from xknxeditor.download.image import build_image

    with open_archive("host-products.zip") as archive:
        name = next(n for n in archive.namelist() if f"A-{number:04X}-30-" in n)
        app = parse_application_xml(archive.read(name), "M-0007")[0]
    project = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    node_id = project.add_device("P-1", "HP-1", "Presence detector", app)
    device = current(project, node_id)
    assert provider_for(device) is None
    ui = device.dynamic_ui
    assert ui is not None
    rows = collect_ui_parameters(ui.ui())
    assert len(rows) > 10
    before = build_image(app, ui=ui)
    assert before.application_segments
    row = next(r for r in rows if r.label == "Application")
    cursor = project.cursor
    project.set_param(device, row.ref_id, "1")
    assert project.cursor == cursor + 1
    device = current(project, node_id)
    assert device.get_param_value(row.ref_id) == "1"
    assert build_image(app, ui=device.dynamic_ui) != before
    project.undo()
    assert current(project, node_id).get_param_value(row.ref_id) == row.value
    project.close()


@pytest.mark.parametrize(("case", "profile"), CASES)
def test_family_noop_and_invalid_configuration_block(
    tmp_path, monkeypatch, case, profile
):
    from editor_gui.plugins.connection.service import ConnectionService

    project, node_id = family_project(tmp_path, monkeypatch, case, profile)
    device = current(project, node_id)
    mapping = mapping_for_device(device)
    assert mapping is not None
    row = next(
        p
        for s in collect_from_device(device, mapping)
        for p in s.parameters
        if p.writable and "/application/" in p.ref_id
    )
    cursor = project.cursor
    project.edit_plugin_parameter(device, row.ref_id, row.value)
    assert project.cursor == cursor
    assert project.prepared_download(device).programming_allowed
    project.persist_device_blob(device, BLOB_NAME, b"invalid")
    device = current(project, node_id)
    connection = ConnectionService()

    def forbidden(*args, **kwargs):
        raise AssertionError("Bus accessed")

    monkeypatch.setattr(connection, "connect_for_operation", forbidden)
    with pytest.raises(ValueError):
        connection.program_device(device=device, without_plugin=True)
    assert project.device_binary_data(device, BLOB_NAME) == b"invalid"
    project.close()


def test_family_role_shift_loss_and_project_archive_roundtrip(tmp_path, monkeypatch):
    from xknxeditor.proj import export_knxproj, import_knxproj
    from xknxeditor.vendor.powertool.family import FamilyEvaluation
    from xknxeditor.vendor.powertool.native import load

    project, node_id = family_project(tmp_path, monkeypatch, "DI-12", "SoloRTR3")
    project.setup_plugin(current(project, node_id))
    device = current(project, node_id)
    config = load(project.export_plugin_config(device))
    state = FamilyEvaluation(config)
    channel = next(c for c in state.channels if c.key.endswith("+Channel_Wippe1"))
    role = next(o for o in device.com_objects if o.number > 15)
    ga = project.create_group_address("1/2/3", "Role")
    assert ga is not None and role.db_id is not None and project._pid is not None
    project._svc.link_com_object(project._pid, role.db_id, ga)
    project._bump(structural=False)
    before = project.export_plugin_config(current(project, node_id))
    option = channel.spec["applications"].index("App_DimmenWippe")
    lost = project.edit_plugin_parameter(
        current(project, node_id), "powertool:family:" + channel.path, str(option)
    )
    after = project.export_plugin_config(current(project, node_id))
    assert after != before
    project.undo()
    assert project.export_plugin_config(current(project, node_id)) == before
    assert project._svc.com_object_links(project._pid, role.db_id)
    project.redo()
    assert project.export_plugin_config(current(project, node_id)) == after
    device = current(project, node_id)
    assert not device.native_error
    original = project.device_binary_data(device, "PT2EtsPlugin")
    project.close()

    export_knxproj(tmp_path / "project.xknx", tmp_path / "export.knxproj", schema="23")
    import_knxproj(tmp_path / "export.knxproj", tmp_path / "import.xknx")
    project.open(tmp_path / "import.xknx")
    device = project.devices[0]
    assert project.export_plugin_config(device) == after
    assert project.device_binary_data(device, "PT2EtsPlugin") == original
    assert project.prepared_download(device)
    assert isinstance(lost, list)
    project.close()


@pytest.mark.parametrize(("case", "profile"), CASES)
async def test_family_uses_normal_programming_path(
    tmp_path, monkeypatch, case, profile
):
    from unittest.mock import AsyncMock

    from editor_gui import programming
    from editor_gui.device_plugins import download_needs_plugin_confirmation

    project, node_id = family_project(tmp_path, monkeypatch, case, profile)
    device = current(project, node_id)
    device.download_plugin_required = True
    assert not download_needs_plugin_confirmation(device)
    download = AsyncMock(return_value=object())
    route = AsyncMock(return_value=None)
    monkeypatch.setattr(programming, "download", download)
    monkeypatch.setattr(programming, "_route_limited", route)
    await programming.download_device(None, device)
    prepared = download.call_args.kwargs["prepared"]
    assert prepared.programming_allowed
    assert download.call_args.kwargs["image"] is prepared.image
    route.assert_awaited_once()
    download.assert_awaited_once()
    assert project.device_binary_data(device, BLOB_NAME) is None
    project.close()


def test_native_scope_selector_defaults_to_full(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from imgui_bundle import imgui

    from editor_gui.plugins.project.ui import configure
    from xknxeditor.download.scope import DownloadScope

    project, node_id = family_project(tmp_path, monkeypatch, "DI-4", "PraesenzStandard")
    panel = SimpleNamespace(
        _download_scope=DownloadScope.PARTIAL, _scope_combo_width=lambda: 100
    )
    choices = []
    monkeypatch.setattr(
        configure, "combo", lambda _, i, labels: (choices.extend(labels) or False, i)
    )
    for name in (
        "align_text_to_frame_padding",
        "text_disabled",
        "same_line",
        "set_next_item_width",
    ):
        monkeypatch.setattr(imgui, name, lambda *_: None)
    configure.ConfigurePanel._render_scope_selector(panel, current(project, node_id))
    assert panel._download_scope is DownloadScope.FULL
    assert choices == [configure.S.SCOPE_FULL]
    project.close()


@pytest.mark.parametrize("keyword", [False, True])
@pytest.mark.parametrize("overwrite", [False, True])
def test_native_address_programming_needs_no_image(
    tmp_path, monkeypatch, keyword, overwrite
):
    from unittest.mock import Mock

    from editor_gui.plugins.connection.service import ConnectionService
    from xknxeditor.download.scope import DownloadScope

    project, node_id = family_project(tmp_path, monkeypatch, "DI-3", "ObjektRTR")
    project.persist_device_blob(current(project, node_id), BLOB_NAME, b"invalid")
    device = current(project, node_id)
    connection = ConnectionService()
    address_write = Mock(return_value="address-only")
    monkeypatch.setattr(connection, "not_connected", lambda *_: False)
    monkeypatch.setattr(connection, "_download_individual_address", address_write)
    scope = (
        DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS
        if overwrite
        else DownloadScope.INDIVIDUAL_ADDRESS
    )
    if keyword:
        result = connection.program_device(
            device=device, scope=scope, current_address="1.1.2"
        )
    else:
        result = connection.program_device(device, scope, current_address="1.1.2")
    assert result == "address-only"
    address_write.assert_called_once()
    project.close()
