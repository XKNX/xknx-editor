"""Binary attachment references, payloads and integrity survive store export."""

from pathlib import Path

import pytest
from test_project_store_serialize import (
    GUID,
    INSTALLER_GUID,
    PID,
    _di,
    _project,
    _session,
)

from xknxeditor.proj.core.project_store import StoreFormat, ets5hash, project_mask
from xknxeditor.proj.core.project_store_serialize import (
    build_store_streams,
    write_project_store,
)
from xknxeditor.proj.models import DeviceBinaryData


@pytest.mark.parametrize("store_format", [StoreFormat.ETS5, StoreFormat.ETS6])
def test_binary_data_reference_payload_and_hash(
    tmp_path: Path, store_format: StoreFormat
) -> None:
    with _session() as session:
        project, installation = _project(session)
        device = installation.areas[1].lines[0].segments[0].devices[0]
        device.binary_data = [
            DeviceBinaryData(
                name="Plugin-Config",
                data=b"\x00original\xff",
                ref_id="BD-1",
                do_not_copy=True,
            ),
            DeviceBinaryData(name="Empty", data=b""),
        ]
        session.flush()
        target = tmp_path / PID
        built = write_project_store(
            target, project, installation, INSTALLER_GUID, store_format=store_format
        )
    rows = _di(built.streams, f"{PID}-0_A-2").findall("BD/BD")
    copy_name = "AutoCopy" if store_format is StoreFormat.ETS5 else "DoNotCopy"
    assert rows[0].attrib == {
        "Id": f"{PID}-0_DI-1_Plugin.2DConfig",
        "Name": "Plugin-Config",
        "RefId": "BD-1",
        copy_name: "true",
    }
    folder = target / "BinaryData"
    name = rows[0].get("Id") + ".dat"
    assert (folder / name).read_bytes() == b"\x00original\xff"
    assert (folder / (name + ".ets5hash")).read_text() == ets5hash(
        b"\x00original\xff", project_mask(GUID, PID)
    )
    empty = rows[1].get("Id") + ".dat"
    assert (folder / empty).read_bytes() == b""
    assert (folder / (empty + ".ets5hash")).read_text() == ets5hash(
        b"", project_mask(GUID, PID)
    )


def test_transform_adds_backup_and_preserves_source_and_other_files(
    tmp_path: Path,
) -> None:
    with _session() as session:
        project, installation = _project(session)
        device = installation.areas[1].lines[0].segments[0].devices[0]
        device.binary_data = [
            DeviceBinaryData(name="Plugin", data=b"original", do_not_copy=True)
        ]
        session.flush()
        target = tmp_path / PID
        write_project_store(target, project, installation, INSTALLER_GUID)
        extra = target / "BinaryData/unrelated.dat"
        extra.write_bytes(b"unrelated")
        built = write_project_store(
            target,
            project,
            installation,
            INSTALLER_GUID,
            binary_data_transform=lambda _: {"Plugin": b"", "Backup": b"original"},
        )
        assert device.binary_data[0].data == b"original"
    folder = target / "BinaryData"
    assert (folder / f"{PID}-0_DI-1_Plugin.dat").read_bytes() == b""
    assert (folder / f"{PID}-0_DI-1_Backup.dat").read_bytes() == b"original"
    assert extra.read_bytes() == b"unrelated"
    assert len(_di(built.streams, f"{PID}-0_A-2").findall("BD/BD")) == 2


def test_invalid_transform_does_not_write_destination(tmp_path: Path) -> None:
    def reject(_):
        raise ValueError("Invalid plugin configuration")

    with _session() as session:
        project, installation = _project(session)
        with pytest.raises(ValueError, match="Invalid plugin"):
            write_project_store(
                tmp_path / PID,
                project,
                installation,
                INSTALLER_GUID,
                binary_data_transform=reject,
            )
    assert not (tmp_path / PID).exists()


def test_in_memory_builder_exposes_binary_payloads() -> None:
    with _session() as session:
        project, installation = _project(session)
        built = build_store_streams(
            project, installation, binary_data_transform=lambda _: {"Plugin": b"native"}
        )
    assert built.binary_data == {f"{PID}-0_DI-1_Plugin.dat": b"native"}
