from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from editor_gui.plugins.project.tests.test_mdt_dca import mdt_project
from xknxeditor.proj import export_knxproj

__all__ = ["mdt_project"]


def without_attached_app(project):
    row = project._svc.project(project._pid)
    output = BytesIO()
    with (
        ZipFile(BytesIO(row.imported_project_files)) as source,
        ZipFile(output, "w") as target,
    ):
        raw = source.read("UserFiles/M0083-A0041.etsapp")
        for name in source.namelist():
            if not name.endswith("M0083-A0041.etsapp"):
                target.writestr(name, source.read(name))
    row.imported_project_files = output.getvalue()
    project._svc._state(project._pid).session.commit()
    project._bump(structural=False)
    return raw


def test_resource_import_preserves_configuration_and_survives_export(
    mdt_project, tmp_path
):
    project = mdt_project
    device = project.devices[2]
    node = device.node_id
    before = project.mdt_symbols(node)
    attachments = project._svc.device_addin_data(project._pid, node, before["app_id"])
    links = project._capture_links(device)
    raw = without_attached_app(project)
    assert project.mdt_symbols(node)["defaults"] == []
    cursor = project.cursor
    project.import_mdt_default_library(node, raw)
    after = project.mdt_symbols(node)
    assert after == before
    assert (
        project._svc.device_addin_data(project._pid, node, before["app_id"])
        == attachments
    )
    assert project._capture_links(device) == links
    resource = project._svc.project_user_file(project._pid, "M0083-A0041-symbols.zip")
    with ZipFile(BytesIO(resource)) as z:
        assert all(
            name == "AddInManifest.xml" or name.endswith(".data")
            for name in z.namelist()
        )
    assert project.undo() and project.cursor == cursor
    assert project.mdt_symbols(node)["defaults"] == []
    assert project.redo()
    cursor = project.cursor
    project.import_mdt_default_library(node, raw)
    assert project.cursor == cursor
    project.close()
    project.open(tmp_path / "project.xknx")
    assert project.mdt_symbols(node) == before
    export_knxproj(
        tmp_path / "project.xknx", tmp_path / "with-symbols.knxproj", schema="20"
    )
    project.close()
    project.import_knxproj(
        tmp_path / "with-symbols.knxproj", tmp_path / "roundtrip.xknx"
    )
    current = project.find_device_by_address(device.individual_address)
    assert project.mdt_symbols(current.node_id) == before


def test_missing_and_wrong_app_import_leave_state_unchanged(mdt_project):
    project = mdt_project
    node = project.devices[2].node_id
    raw = without_attached_app(project)
    before = project.mdt_symbols(node)
    cursor = project.cursor
    with pytest.raises(ValueError):
        project.import_mdt_default_library(node, b"invalid")
    assert project.cursor == cursor and project.mdt_symbols(node) == before
    with pytest.raises(ValueError):
        project.import_mdt_default_library(project.devices[0].node_id, raw)
    assert project.cursor == cursor


def test_central_complete_custom_library_keeps_defaults_available(
    mdt_project, monkeypatch
):
    from xknxeditor.vendor.mdt import encode_symbol
    from xknxeditor.vendor.mdt.symbols import recovered_symbol_entries

    source = (
        Path(__file__).resolve().parents[7]
        / ".references/mdtdca/MDT_DCA_BE_01_Central_Operation_Unit_Smart_v13.etsapp"
    )
    if not source.exists():
        pytest.skip("Optional central symbol app not installed")
    project = mdt_project
    node = project.devices[2].node_id
    device = SimpleNamespace(
        node_id=node,
        name="Central",
        display_name="Central",
        individual_address="1.1.50",
        order_number="BE-BZS86.01",
        app=SimpleNamespace(id="M-0083_A-0054-14-0D63"),
    )
    monkeypatch.setattr(project, "find_device_by_node_id", lambda _: device)
    profile = project.mdt_dca_profile(device)
    project.import_mdt_default_library(node, source.read_bytes())
    defaults = project.mdt_symbols(node)["defaults"]
    entries = recovered_symbol_entries(
        {slot: encode_symbol(bytes(4096)) for slot in range(20)}, profile
    )
    project._svc.set_device_addin_data(project._pid, node, profile.app_id, entries)
    project._bump(structural=False)
    view = project.mdt_symbols(node)
    assert len(view["symbols"]) == 20
    assert view["defaults"] == defaults and len(defaults) == 20
    assert view["new_library"] is False
    project.restore_mdt_symbol(node, 0)
    restored = project.mdt_symbols(node)["symbols"][0]
    assert restored["data_hex"] == defaults[0]["data_hex"]
    assert "Lib" not in project._svc.device_addin_data(
        project._pid, node, profile.app_id
    )
    assert project.undo() and project.mdt_symbols(node) == view


def test_all_devices_share_one_library_in_user_project(tmp_path):
    import sqlite3

    from editor_gui.plugins.base import Logger
    from editor_gui.plugins.catalog.service import CatalogService
    from editor_gui.plugins.logger.service import LogService
    from editor_gui.plugins.project.service import ProjectService

    root = Path(__file__).resolve().parents[7] / ".references"
    source = root / "Gebaude01.xknx"
    app = root / "mdtdca/MDT_DCA_BE_01_Push_Button_II_Smart_v4_5_2_0.etsapp"
    if not source.exists() or not app.exists():
        pytest.skip("Optional user project and symbol app not installed")
    target = tmp_path / "project.xknx"
    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original,
        sqlite3.connect(target) as copy,
    ):
        original.backup(copy)
    project = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.open(target)
    try:
        device = project.find_device_by_address("1.1.76")
        nodes = [
            d.node_id
            for d in project.devices
            if (p := project.mdt_dca_profile(d)) and p.app_id == "M0083-A0041"
        ]
        previous = {
            n: project._svc.device_addin_data(project._pid, n, "M0083-A0041")
            for n in nodes
        }
        project.import_mdt_default_library(device.node_id, app.read_bytes())
        for node in nodes:
            assert len(project.mdt_symbols(node)["defaults"]) == 36
            assert (
                project._svc.device_addin_data(project._pid, node, "M0083-A0041")
                == previous[node]
            )
        with ZipFile(
            BytesIO(project._svc.project(project._pid).imported_project_files)
        ) as z:
            assert z.namelist() == ["UserFiles/M0083-A0041-symbols.zip"]
    finally:
        project.close()
