"""Native plugin configuration accompanies exported objects and links."""

import pytest

from editor_gui.plugins.project.plugin_export import binary_data_for_export
from xknxeditor.proj import export_knxproj, import_knxproj
from xknxeditor.vendor.powertool import BLOB_NAME
from xknxeditor.vendor.powertool.native import compile_native, from_original

from .test_powertool import assert_current_blob, current, native_project
from .test_powertool_blob import stored_linked_project


def test_archive_contains_current_plugin_and_links(tmp_path, monkeypatch):
    project, nid, original = stored_linked_project(tmp_path, monkeypatch)
    project.import_stored_plugin_config(current(project, nid))
    project.edit_plugin_parameter(
        current(project, nid), "powertool:0:function", "dim_rocker"
    )
    before = project.prepared_download(current(project, nid))
    native = project.export_plugin_config(current(project, nid))
    links = project.group_communication_for(current(project, nid)).links
    row = project._svc.devices(project._pid)[0]
    assert binary_data_for_export(row) == {}
    expected_blob = project.device_binary_data(current(project, nid), "PT2EtsPlugin")
    cursor = project.cursor
    export_knxproj(
        project.working_path,
        tmp_path / "updated.knxproj",
        binary_data_transform=binary_data_for_export,
    )
    assert project.cursor == cursor
    assert (
        project.device_binary_data(current(project, nid), "PT2EtsPlugin")
        == expected_blob
    )
    project.close()
    import_knxproj(tmp_path / "updated.knxproj", tmp_path / "updated.xknx")
    project.open(tmp_path / "updated.xknx")
    device = project.devices[0]
    assert project.device_binary_data(device, "PowerTool-Original") is None
    assert expected_blob != original
    assert project.device_binary_data(device, BLOB_NAME) == native
    active = project.device_binary_data(device, "PT2EtsPlugin")
    assert active == expected_blob
    assert project.prepared_download(device).image == before.image
    assert [
        link.group_address for link in project.group_communication_for(device).links
    ] == [link.group_address for link in links]
    exported_again = binary_data_for_export(project._svc.devices(project._pid)[0])
    assert exported_again == {}
    project.close()


def test_export_new_configuration_and_flags(tmp_path, monkeypatch):
    project, nid = native_project(tmp_path, monkeypatch)
    project.setup_plugin(current(project, nid))
    reference = current(project, nid).get_visible_com_objects()[0].id
    project.set_flag(current(project, nid), reference, "read_on_init", True)
    project.set_flag(current(project, nid), reference, "transmit", False)
    project.set_com_object_priority(current(project, nid), reference, "High")
    row = project._svc.devices(project._pid)[0]
    assert binary_data_for_export(row) == {}
    image = compile_native(
        from_original(project.device_binary_data(current(project, nid), "PT2EtsPlugin"))
    )
    assert image.objects[0].flags & 32
    assert not image.objects[0].flags & 64
    assert image.objects[0].flags & 3 == 1
    project.close()


def test_export_invalid_native_aborts_without_replacing_destination(
    tmp_path, monkeypatch
):
    project, nid = native_project(tmp_path, monkeypatch)
    project.persist_device_blob(current(project, nid), BLOB_NAME, b"invalid")
    target = tmp_path / "existing.knxproj"
    target.write_bytes(b"existing archive")
    with pytest.raises(ValueError):
        export_knxproj(
            project.working_path, target, binary_data_transform=binary_data_for_export
        )
    assert target.read_bytes() == b"existing archive"
    project.close()


@pytest.mark.parametrize("format_name", ["ETS5", "ETS6"])
def test_direct_store_contains_current_plugin_without_backup(
    tmp_path, monkeypatch, format_name
):
    from xknxeditor.proj.core.project_store import StoreFormat, ets5hash, project_mask
    from xknxeditor.proj.core.project_store_serialize import write_project_store

    project, nid, original = stored_linked_project(tmp_path, monkeypatch)
    project.edit_plugin_parameter(
        current(project, nid), "powertool:0:function", "dim_rocker"
    )
    device_row = project._svc.devices(project._pid)[0]
    assert binary_data_for_export(device_row) == {}
    expected = {row.name: row.data for row in device_row.binary_data}
    assert "PowerTool-Original" not in expected
    row = project._svc.project(project._pid)
    destination = tmp_path / "store"
    cursor = project.cursor
    built = write_project_store(
        destination,
        row,
        project._svc.installations(project._pid)[0],
        "00000000-0000-0000-0000-000000000001",
        store_format=StoreFormat[format_name],
        binary_data_transform=binary_data_for_export,
    )
    from xknxeditor.proj.core.knxproj_export import encode_binary_name

    for name, payload in expected.items():
        path = next(
            (destination / "BinaryData").glob(f"*_{encode_binary_name(name)}.dat")
        )
        exported = path.read_bytes()
        assert exported == payload
        assert path.with_suffix(".dat.ets5hash").read_text() == ets5hash(
            exported, project_mask(built.guid, built.pid)
        )
    assert len(list((destination / "BinaryData").glob("*.dat"))) == len(expected)
    assert expected["PT2EtsPlugin"] != original
    assert project.cursor == cursor
    assert (
        project.device_binary_data(current(project, nid), "PT2EtsPlugin")
        == expected["PT2EtsPlugin"]
    )
    project.close()


def test_export_reads_one_snapshot_during_live_edit(tmp_path, monkeypatch):
    project, nid = native_project(tmp_path, monkeypatch)
    project.setup_plugin(current(project, nid))
    before = project.export_plugin_config(current(project, nid))
    did_edit = False

    def transform(row):
        nonlocal did_edit
        project.edit_plugin_parameter(
            current(project, nid), "powertool:0:function", "dim_rocker"
        )
        did_edit = True
        return binary_data_for_export(row)

    output = tmp_path / "snapshot.knxproj"
    export_knxproj(project.working_path, output, binary_data_transform=transform)
    assert did_edit
    assert project.export_plugin_config(current(project, nid)) != before
    import zipfile

    from xknxeditor.vendor.powertool.native import load

    with zipfile.ZipFile(output) as archive:
        blob = next(n for n in archive.namelist() if n.endswith("_PT2EtsPlugin.dat"))
        assert (
            compile_native(from_original(archive.read(blob))).pid
            == compile_native(load(before)).pid
        )
    project.close()


def test_flag_and_priority_edits_update_blob_and_undo_together(tmp_path, monkeypatch):
    project, nid, original = stored_linked_project(tmp_path, monkeypatch)
    project.import_stored_plugin_config(current(project, nid))
    reference = current(project, nid).get_visible_com_objects()[0].id
    before = assert_current_blob(project, nid)
    cursor = project.cursor
    project.set_flag(current(project, nid), reference, "read_on_init", True)
    changed = assert_current_blob(project, nid)
    assert changed != before
    assert project.cursor == cursor + 1
    project.undo()
    assert assert_current_blob(project, nid) == before
    project.redo()
    assert assert_current_blob(project, nid) == changed
    project.set_com_object_priority(current(project, nid), reference, "High")
    assert (
        compile_native(from_original(assert_current_blob(project, nid)))
        .objects[0]
        .flags
        & 3
        == 1
    )
    project.set_com_object_priority(current(project, nid), reference, None)
    assert (
        compile_native(from_original(assert_current_blob(project, nid)))
        .objects[0]
        .flags
        & 3
        == 3
    )
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") != original
    project.close()


@pytest.mark.parametrize("failure", ["writer", "persistence"])
def test_write_failure_keeps_blobs_links_history_and_redo(
    tmp_path, monkeypatch, failure
):
    project, nid, original = stored_linked_project(tmp_path, monkeypatch)
    project.edit_plugin_parameter(
        current(project, nid), "powertool:0:function", "dim_rocker"
    )
    project.undo()
    cursor = project.cursor
    links = project.group_communication_for(current(project, nid)).links

    if failure == "writer":

        def reject(*_):
            raise ValueError("Cannot persist plugin state")

        monkeypatch.setattr(
            "editor_gui.plugin_providers.powertool.write_project_blob", reject
        )
    else:
        from xknxeditor.proj.core.events import UpsertDeviceBinaryData

        apply = UpsertDeviceBinaryData.apply

        def reject_apply(event, session):
            apply(event, session)
            if event.name == "PT2EtsPlugin":
                raise ValueError("Cannot persist plugin state")

        monkeypatch.setattr(UpsertDeviceBinaryData, "apply", reject_apply)
    with pytest.raises(ValueError, match="Cannot persist"):
        project.edit_plugin_parameter(
            current(project, nid), "powertool:0:function", "dim_rocker"
        )
    assert project.cursor == cursor
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == original
    assert project.device_binary_data(current(project, nid), BLOB_NAME) is None
    assert project.group_communication_for(current(project, nid)).links == links
    if failure == "persistence":
        monkeypatch.setattr(UpsertDeviceBinaryData, "apply", apply)
    project.redo()
    assert_current_blob(project, nid)
    project.close()


@pytest.mark.parametrize("missing", [False, True])
def test_native_import_repairs_older_blob_and_preserves_links(
    tmp_path, monkeypatch, missing
):
    from sqlalchemy.orm import object_session

    project, nid, original = stored_linked_project(tmp_path, monkeypatch)
    project.edit_plugin_parameter(
        current(project, nid), "powertool:0:function", "dim_rocker"
    )
    native = project.export_plugin_config(current(project, nid))
    row = project._svc.devices(project._pid)[0]
    blob = next(b for b in row.binary_data if b.name == "PT2EtsPlugin")
    session = object_session(row)
    if missing:
        session.delete(blob)
    else:
        blob.data = original
        blob.ref_id = "M-0007_BD-7"
        blob.do_not_copy = True
    session.commit()
    project._refresh_device(nid)
    with pytest.raises(ValueError, match=r"missing|out of date"):
        binary_data_for_export(project._svc.devices(project._pid)[0])
    links = project.group_communication_for(current(project, nid)).links
    cursor = project.cursor
    assert project.import_plugin_config(current(project, nid), native) == []
    assert project.cursor == cursor + 1
    assert_current_blob(project, nid)
    assert project.group_communication_for(current(project, nid)).links == links
    row = project._svc.devices(project._pid)[0]
    assert binary_data_for_export(row) == {}
    if not missing:
        blob = next(b for b in row.binary_data if b.name == "PT2EtsPlugin")
        assert blob.ref_id == "M-0007_BD-7" and blob.do_not_copy
    project.undo()
    assert project.device_binary_data(current(project, nid), "PT2EtsPlugin") == (
        None if missing else original
    )
    project.close()


def test_legacy_family_export_keeps_current_blob_without_migration(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from xknxeditor.vendor.powertool.native import dump, load

    from .test_powertool_family import family_project

    project, nid = family_project(tmp_path, monkeypatch, "DI-3", "ObjektRTR")
    project.setup_plugin(current(project, nid))
    active = assert_current_blob(project, nid)
    config = load(project.export_plugin_config(current(project, nid)))
    legacy = dump(
        replace(
            config,
            database_code=None,
            products={},
            channel_counts={},
            channel_ids={},
            program_ids=None,
        )
    )
    project.persist_device_blob(current(project, nid), BLOB_NAME, legacy)
    cursor = project.cursor
    assert binary_data_for_export(project._svc.devices(project._pid)[0]) == {}
    output = tmp_path / "legacy.knxproj"
    export_knxproj(
        project.working_path, output, binary_data_transform=binary_data_for_export
    )
    assert project.cursor == cursor
    project.close()
    import_knxproj(output, tmp_path / "legacy.xknx")
    project.open(tmp_path / "legacy.xknx")
    device = project.devices[0]
    assert project.device_binary_data(device, BLOB_NAME) == legacy
    assert project.device_binary_data(device, "PT2EtsPlugin") == active
    project.close()
