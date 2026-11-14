"""A background store export keeps plugin data and links from one database state."""

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy.orm import Session

from editor_gui import export_snapshot, main
from xknxeditor.proj.core.project_store import StoreFormat
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import (
    Area,
    ComObject,
    ComObjectLink,
    Device,
    DeviceBinaryData,
    GroupAddress,
    GroupRange,
    Installation,
    Line,
    Project,
    Segment,
)


@pytest.mark.parametrize(
    ("store_format", "fail"),
    [(StoreFormat.ETS5, False), (StoreFormat.ETS6, False), (StoreFormat.ETS6, True)],
)
def test_store_export_uses_snapshot_during_live_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store_format: StoreFormat,
    fail: bool,
) -> None:
    source = tmp_path / "live.xknx"
    engine = make_engine(url_for(source))
    with Session(engine) as session:
        address = GroupAddress(address=1, name="Switch")
        device = Device(
            address=1,
            product_ref_id="M-0007_H-1_P-1",
            com_objects=[
                ComObject(
                    ref_id="O-1_R-1",
                    links=[ComObjectLink(group_address=address, is_sending=True)],
                )
            ],
            binary_data=[DeviceBinaryData(name="Plugin", data=b"original")],
        )
        session.add_all(
            [
                Project(
                    id="P-9001",
                    name="Snapshot",
                    guid="17727eba-16e9-4445-a058-4b22c580963f",
                ),
                Installation(
                    index=0,
                    areas=[
                        Area(
                            address=1,
                            lines=[
                                Line(
                                    address=1,
                                    segments=[
                                        Segment(medium_type="MT-0", devices=[device])
                                    ],
                                )
                            ],
                        )
                    ],
                    group_ranges=[
                        GroupRange(
                            range_start=1, range_end=255, group_addresses=[address]
                        )
                    ],
                ),
            ]
        )
        session.commit()

    app = SimpleNamespace(
        _project_service=SimpleNamespace(
            working_path=source,
            program_refs=lambda: {},
            module_arguments=lambda: {},
            group_object_trees=lambda: {},
            project_password=None,
        ),
        _catalog_service=SimpleNamespace(io_lock=threading.RLock()),
        _projectstore_format=store_format,
        _myknx_thread=None,
        _log=Mock(),
    )
    monkeypatch.setattr(
        main, "collect_manufacturer_bundle", lambda *_: SimpleNamespace(extra_files={})
    )
    snapshot_paths = []
    snapshot = export_snapshot.snapshot_project

    def capture_snapshot(source, target):
        snapshot_paths.append(target)
        snapshot(source, target)

    monkeypatch.setattr(export_snapshot, "snapshot_project", capture_snapshot)
    ready, edited = threading.Event(), threading.Event()
    write = main.write_project_store

    def write_while_editing(*args, **kwargs):
        session = Session.object_session(args[1])
        assert session is not None
        row = session.query(Device).one()
        assert [co.ref_id for co in row.com_objects] == ["O-1_R-1"]
        assert row.com_objects[0].links
        ready.set()
        assert edited.wait(timeout=10)
        if fail:
            raise ValueError("Export rejected")
        return write(*args, **kwargs)

    monkeypatch.setattr(main, "write_project_store", write_while_editing)
    try:
        main.KnxGuiApp._export_to_ets6_projectstore(
            app,
            tmp_path / "store",
            "8084aff1-f6d2-4368-b7f8-186ae9dadcaa",
            create_copy=False,
        )
        assert ready.wait(timeout=10)
        with Session(engine) as session:
            row = session.query(Device).one()
            row.binary_data[0].data = b"edited"
            row.com_objects[0].ref_id = "O-2_R-2"
            row.com_objects[0].links.clear()
            session.commit()
        edited.set()
        app._myknx_thread.join(timeout=10)
        assert not app._myknx_thread.is_alive()
        assert len(snapshot_paths) == 1
        assert not snapshot_paths[0].parent.exists()
        target = tmp_path / "store/P-9001"
        if fail:
            app._log.error.assert_called_once()
            assert not target.exists()
        else:
            app._log.error.assert_not_called()
            app._log.info.assert_called_once()
            area = ET.fromstring((target / "P-9001-0_A-1").read_bytes())
            instance = area.find(".//CI")
            assert instance is not None
            assert instance.get("RefId") == "O-1_R-1"
            assert instance.get("Links")
            payload = next((target / "BinaryData").glob("*.dat"))
            assert payload.read_bytes() == b"original"
    finally:
        edited.set()
        if app._myknx_thread is not None:
            app._myknx_thread.join(timeout=10)
        engine.dispose()


def _native_app(tmp_path, monkeypatch):
    from editor_gui.plugins.project.tests.test_powertool import current, native_project

    project, nid = native_project(tmp_path, monkeypatch)
    project.setup_plugin(current(project, nid))
    return SimpleNamespace(
        _project_service=project,
        _catalog_service=SimpleNamespace(io_lock=project._io_lock),
        _projectstore_format=StoreFormat.ETS6,
        _myknx_thread=None,
        _export_remove_unused=False,
        _export_schema="23",
        _ets_is5=False,
        _signing_warning_acknowledged=True,
        _log=Mock(),
        _begin_progress=Mock(),
        set_progress=Mock(),
        _show_ets_result=Mock(),
        _show_export_failure=Mock(),
    ), nid


def _start_export(app, mode, tmp_path):
    if mode == "store":
        main.KnxGuiApp._export_to_ets6_projectstore(
            app,
            tmp_path / "store",
            "8084aff1-f6d2-4368-b7f8-186ae9dadcaa",
            create_copy=False,
        )
    elif mode == "archive":
        main.KnxGuiApp._do_export_knxproj(app, str(tmp_path / "archive.knxproj"))
    else:
        main.KnxGuiApp._do_export_via_ets(
            app, str(tmp_path / "signed.knxproj"), accept_untested=True
        )


@pytest.mark.parametrize("mode", ["store", "archive", "local"])
@pytest.mark.parametrize("fail", [False, True])
def test_export_keeps_tree_and_blob_when_edit_precedes_worker(
    tmp_path, monkeypatch, mode, fail
):
    from editor_gui.plugins.project.tests.test_powertool import current
    from xknxeditor.proj.core.knxproj_export import (
        group_object_tree_instances,
        relidref,
    )
    from xknxeditor.proj.core.project_store_serialize import build_store_streams
    from xknxeditor.vendor.powertool import BLOB_NAME

    app, nid = _native_app(tmp_path, monkeypatch)
    project = app._project_service
    before = project.export_plugin_config(current(project, nid))
    tree = project.group_object_trees()[nid]
    wanted_refs = group_object_tree_instances(tree)
    worker_thread = threading.Thread
    did_edit = False

    def delayed_start(*, target, daemon):
        def edit_then_start():
            nonlocal did_edit
            project.edit_plugin_parameter(
                current(project, nid), "powertool:0:function", "dim_rocker"
            )
            did_edit = True
            return worker_thread(target=target, daemon=daemon)

        worker = None

        def start():
            nonlocal worker
            worker = edit_then_start()
            worker.start()

        return SimpleNamespace(
            start=start,
            join=lambda **kwargs: worker.join(**kwargs),
            is_alive=lambda: worker is not None and worker.is_alive(),
        )

    monkeypatch.setattr(main.threading, "Thread", delayed_start)
    monkeypatch.setattr(
        main,
        "collect_manufacturer_bundle",
        lambda *_: SimpleNamespace(
            extra_files={}, master_xml=None, resolved_manufacturers=(), skipped_refs=()
        ),
    )
    output = []
    snapshots = []
    snapshot = export_snapshot.snapshot_project

    def capture_snapshot(source, target):
        snapshots.append(target)
        snapshot(source, target)

    monkeypatch.setattr(export_snapshot, "snapshot_project", capture_snapshot)

    def check_row(row, trees):
        assert did_edit
        assert (
            next(blob.data for blob in row.binary_data if blob.name == BLOB_NAME)
            == before
        )
        assert {relidref(co.ref_id) for co in row.com_objects} == wanted_refs
        assert trees[nid] == tree

    if mode == "store":
        write = main.write_project_store

        def write_store(*args, **kwargs):
            session = Session.object_session(args[1])
            check_row(session.query(Device).one(), kwargs["group_object_trees"])
            output.append(write(*args, **kwargs))
            if fail:
                raise ValueError("Export failed")
            return output[-1]

        monkeypatch.setattr(main, "write_project_store", write_store)
    else:

        def write_archive(source, _destination, **kwargs):
            engine = make_engine(url_for(source))
            try:
                with Session(engine) as session:
                    row = session.query(Device).one()
                    check_row(row, kwargs["group_object_trees"])
                    output.append(
                        build_store_streams(
                            session.query(Project).one(),
                            session.query(Installation).first(),
                            group_object_trees=kwargs["group_object_trees"],
                            binary_data_transform=kwargs["binary_data_transform"],
                        )
                    )
            finally:
                engine.dispose()
            if fail:
                raise ValueError("Export failed")
            return SimpleNamespace(
                schema="23",
                missing_references=[],
                import_notes="",
                product_prune=None,
                unverifiable_folders=[],
                schema_incompatible_products=[],
            )

        monkeypatch.setattr(main, "export_knxproj", write_archive)
        monkeypatch.setattr(
            main,
            "ets_local_sign",
            lambda *_, **__: SimpleNamespace(
                certificate_written=True,
                validation_ok=True,
                license_name="Owner",
                licensed_devices=1,
                notes=(),
            ),
        )
    try:
        _start_export(app, mode, tmp_path)
        app._myknx_thread.join(timeout=10)
        assert not app._myknx_thread.is_alive()
        if fail:
            app._log.error.assert_called_once()
        else:
            app._log.error.assert_not_called()
        assert len(output) == 1
        assert project.export_plugin_config(current(project, nid)) != before
        device = next(
            node
            for data in output[0].streams.values()
            if data and (node := ET.fromstring(data).find(".//DI")) is not None
        )
        assert device.get("IsActivityCalculated") == "true"
        assert (
            set(device.find("GrpObjTree").get("GroupObjectInstances").split())
            == wanted_refs
        )
        assert len(snapshots) == 1
        assert not snapshots[0].parent.exists()
    finally:
        project.close()


@pytest.mark.parametrize("mode", ["store", "archive", "local"])
def test_export_cleans_snapshot_when_thread_start_fails(tmp_path, monkeypatch, mode):
    app, _ = _native_app(tmp_path, monkeypatch)
    capture = main.capture_export
    prepared = []

    def capture_once(*args):
        result = capture(*args)
        prepared.append(result)
        return result

    monkeypatch.setattr(main, "capture_export", capture_once)
    monkeypatch.setattr(
        main.threading,
        "Thread",
        lambda **_: SimpleNamespace(start=Mock(side_effect=RuntimeError("No thread"))),
    )
    try:
        _start_export(app, mode, tmp_path)
        assert len(prepared) == 1
        assert not prepared[0].source.parent.exists()
        app._log.error.assert_called_once()
        if mode != "store":
            assert not app._progress_running
    finally:
        app._project_service.close()


def test_snapshot_preparation_failure_cleans_partial_copy(tmp_path, monkeypatch):
    app, _ = _native_app(tmp_path, monkeypatch)
    paths = []

    def fail(source, target):
        target.write_bytes(b"partial")
        paths.append(target)
        raise OSError("Snapshot failed")

    monkeypatch.setattr(export_snapshot, "snapshot_project", fail)
    try:
        with pytest.raises(OSError, match="Snapshot failed"):
            export_snapshot.capture_export(
                app._project_service, app._catalog_service.io_lock
            )
        assert len(paths) == 1
        assert not paths[0].parent.exists()
    finally:
        app._project_service.close()


@pytest.mark.parametrize("password", ["project-password", "wrong-password", None])
def test_protected_store_uses_captured_password_and_cleans_snapshot(
    tmp_path, monkeypatch, password
):
    from xknxeditor.proj.core.project_secrets import ProjectCipher, new_salt

    secret = "00112233445566778899aabbccddeeff"
    source = tmp_path / "protected.xknx"
    salt = new_salt()
    cipher = ProjectCipher.derive("project-password", salt)
    sealed = cipher.seal(secret)
    engine = make_engine(url_for(source))
    with Session(engine) as session:
        session.add_all(
            [
                Project(
                    id="P-9001",
                    name="Protected",
                    guid="17727eba-16e9-4445-a058-4b22c580963f",
                    password_salt=salt,
                    password_check=cipher.check_value(),
                ),
                Installation(
                    index=0,
                    group_ranges=[
                        GroupRange(
                            range_start=1,
                            range_end=255,
                            group_addresses=[
                                GroupAddress(
                                    address=1, name="Secure", security_key=sealed
                                )
                            ],
                        )
                    ],
                ),
            ]
        )
        session.commit()
    app = SimpleNamespace(
        _project_service=SimpleNamespace(
            working_path=source,
            program_refs=lambda: set(),
            module_arguments=lambda: {},
            group_object_trees=lambda: {},
            project_password=password,
        ),
        _catalog_service=SimpleNamespace(io_lock=threading.RLock()),
        _projectstore_format=StoreFormat.ETS6,
        _myknx_thread=None,
        _log=Mock(),
    )
    capture = main.capture_export
    snapshots = []

    def capture_password(*args):
        result = capture(*args)
        snapshots.append(result)
        return result

    monkeypatch.setattr(main, "capture_export", capture_password)
    monkeypatch.setattr(
        main, "collect_manufacturer_bundle", lambda *_: SimpleNamespace(extra_files={})
    )
    write = Mock(wraps=main.write_project_store)
    monkeypatch.setattr(main, "write_project_store", write)
    try:
        _start_export(app, "store", tmp_path)
        app._myknx_thread.join(timeout=10)
        assert not app._myknx_thread.is_alive()
        assert len(snapshots) == 1
        assert not snapshots[0].source.parent.exists()
        target = tmp_path / "store"
        if password == "project-password":
            app._log.error.assert_not_called()
            write.assert_called_once()
            data = (target / "P-9001/G").read_bytes()
            exported = ET.fromstring(data).find(".//GA")
            assert exported.get("Key") == secret
            assert b"enc1:" not in data
            with Session(engine) as session:
                assert session.query(GroupAddress).one().security_key == sealed
        else:
            write.assert_not_called()
            app._log.error.assert_called_once()
            error = app._log.error.call_args.kwargs["error"]
            assert (
                "InvalidProjectPassword" if password else "ProjectPasswordRequired"
            ) in error
            assert not target.exists()
    finally:
        engine.dispose()
