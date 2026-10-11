"""Tests for round-tripping per-device ``<ModuleInstances>`` and ``<BinaryData>``.

``xknxproject`` surfaces neither the module ``Arguments``/``RepeatIndex`` nor a DeviceInstance's
``<BinaryData>`` (where DCAs persist state, e.g. the MDT DALI ``DaliGC16-Backup-Store``). The
importer captures both from the raw project XML and the exporter re-emits them, so module-based
devices (and DCA state) survive an import -> export cycle across all manufacturers.

ETS stores a BinaryData payload as a raw file ``P-XXXX/BinaryData/{Id}.dat`` (not inline ``<Data>``),
with the ``0.xml`` element carrying only ``Id``/``Name``/``RefId``/``DoNotCopy``; the ``.dat`` files
are covered by the project-folder signature. These tests exercise the reader (inline and external),
the writer, the signature coverage, and a full export -> reimport round-trip.
"""

import base64
import io
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core import knxproj_import
from xknxeditor.proj.core.knxproj_export import _encode_binary_name
from xknxeditor.proj.core.knxproj_signing import verify_directory_signature
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import Device, DeviceBinaryData, ModuleInstance, Project

_NS = "http://knx.org/xml/project/23"
_BLOB = b"<device/>\x00\xff"  # arbitrary bytes incl. a NUL and a high byte, to prove byte fidelity


def _zero_xml(binary: str) -> str:
    """A minimal installation XML with one DeviceInstance: a module instance (args + repeat index)
    and a ``<BinaryData>`` block whose entry is provided as ``binary`` (an inline-``<Data>`` entry or
    a self-closing entry that points at an external ``.dat`` file)."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="{_NS}">
  <Project Id="P-01"><Installations><Installation><Topology><Area Address="1"><Line Address="1">
    <DeviceInstance Id="P-01-0_DI-1" Address="1">
      <ModuleInstances>
        <ModuleInstance Id="MD-1_M-2_MI-1" RefId="MD-1_M-2" RepeatIndex="215x1 216x1">
          <Arguments>
            <Argument RefId="MD-1_A-1" Value="46"/>
            <Argument RefId="MD-1_A-2" Value="71"/>
          </Arguments>
        </ModuleInstance>
      </ModuleInstances>
      <BinaryData>{binary}</BinaryData>
    </DeviceInstance>
  </Line></Area></Topology></Installation></Installations></Project>
</KNX>
"""


class _FakeContents:
    """Duck-types the bits of xknxproject's ``KNXProjContents`` the reader uses: ``open_project_0``
    plus the private ``_project_archive`` / ``_project_relative_path`` used for external files."""

    def __init__(self, zero_xml: bytes, files: dict[str, bytes] | None = None) -> None:
        self._zero = zero_xml
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("P-01/0.xml", zero_xml)
            for rel, data in (files or {}).items():
                zf.writestr(f"P-01/{rel}", data)
        buf.seek(0)
        self._project_archive = zipfile.ZipFile(buf)
        self._project_relative_path = "P-01/"

    def open_project_0(self) -> io.BytesIO:
        return io.BytesIO(self._zero)


def test_encode_binary_name_matches_ets() -> None:
    # Id encoding: letters/digits kept, else ".XX" (uppercase hex) per UTF-8 byte.
    assert _encode_binary_name("DaliGC16-Backup-Store") == "DaliGC16.2DBackup.2DStore"
    assert _encode_binary_name("a b") == "a.20b"  # space 0x20
    assert _encode_binary_name("Kanäle-1") == "Kanäle.2D1"  # umlaut is a letter -> kept


def test_read_device_extras_parses_modules_and_inline_binary() -> None:
    inline = (
        '<BinaryData Id="P-01-0_DI-1_X" Name="DaliGC16-Backup-Store">'
        f"<Data>{base64.b64encode(_BLOB).decode()}</Data></BinaryData>"
    )
    extras = knxproj_import._read_device_extras(
        _FakeContents(_zero_xml(inline).encode())
    )

    mods = extras.module_args["P-01-0_DI-1"]
    assert mods["MD-1_M-2_MI-1"] == (
        "215x1 216x1",
        [{"ref_id": "MD-1_A-1", "value": "46"}, {"ref_id": "MD-1_A-2", "value": "71"}],
    )
    (entry,) = extras.binary_data["P-01-0_DI-1"]
    assert (entry.name, entry.data) == ("DaliGC16-Backup-Store", _BLOB)


def test_read_binary_data_from_external_dat_file() -> None:
    # ETS 6.4 form: <BinaryData> has no <Data>; the payload lives in BinaryData/{Id}.dat.
    entry_id = "P-01-0_DI-1_DaliGC16.2DBackup.2DStore"
    elem = (
        f'<BinaryData Id="{entry_id}" Name="DaliGC16-Backup-Store" DoNotCopy="false"/>'
    )
    contents = _FakeContents(
        _zero_xml(elem).encode(),
        files={f"BinaryData/{entry_id}.dat": _BLOB},
    )
    extras = knxproj_import._read_device_extras(contents)
    (entry,) = extras.binary_data["P-01-0_DI-1"]
    assert entry.name == "DaliGC16-Backup-Store"
    assert entry.data == _BLOB  # read from the external .dat file
    assert entry.do_not_copy is False


def _project_with_module_and_binary(tmp_path: Path) -> Path:
    """Build a project via the public API, then attach a module instance + a binary blob directly
    (there is no public API for those), and return the ``.xknx`` path."""
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-BM")
    seg = svc.create_line(pid, svc.create_area(pid, 0, 2, "A"), 1, "L")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        for line in area.lines
        if line.id == seg
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        "M-9_H-1_P-1",
        address=5,
        name="Dev",
        hardware2program_ref_id="M-9_H-1_HP-1",
    )
    svc.close(pid)

    with Session(make_engine(url_for(src))) as session:
        device = session.get(Device, device_id)
        assert device is not None
        device.module_instances.append(
            ModuleInstance(
                instance_id="M-9_A-1_MD-1_M-2_MI-1",
                ref_id="M-9_A-1_MD-1",
                repeat_index="215x1 216x1",
                arguments=[
                    {"ref_id": "MD-1_A-1", "value": "46"},
                    {"ref_id": "MD-1_A-2", "value": "71"},
                ],
            )
        )
        device.binary_data.append(
            DeviceBinaryData(name="DaliGC16-Backup-Store", data=_BLOB)
        )
        session.commit()
    return src


def test_export_emits_external_binary_data_and_signs_it(tmp_path: Path) -> None:
    src = _project_with_module_and_binary(tmp_path)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        pid = next(n for n in names if n.endswith("/0.xml")).split("/")[0]
        xml = zf.read(f"{pid}/0.xml").decode("utf-8")
        # ETS id form: {DeviceInstanceId}_{encoded Name}; '-' -> '.2D'. No inline <Data>.
        expected_id = f"{pid}-0_DI-1_DaliGC16.2DBackup.2DStore"
        assert 'Name="DaliGC16-Backup-Store"' in xml
        # project/20 spells the copy flag "AutoCopy" (ETS treats it as DoNotCopy); /23 uses DoNotCopy.
        assert 'AutoCopy="false"' in xml
        assert "<Data>" not in xml
        dat = f"{pid}/BinaryData/{expected_id}.dat"
        assert dat in names
        assert zf.read(dat) == _BLOB  # payload is the raw bytes

        # The folder signature must cover the .dat file (ETS signs the folder recursively).
        folder = {
            n[len(pid) + 1 :]: zf.read(n) for n in names if n.startswith(pid + "/")
        }
        assert f"BinaryData/{expected_id}.dat" in folder
        assert verify_directory_signature(folder, zf.read(f"{pid}.signature"))


def test_binary_data_and_modules_survive_full_roundtrip(tmp_path: Path) -> None:
    src = _project_with_module_and_binary(tmp_path)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    round_path = tmp_path / "round.xknx"
    import_knxproj(out, round_path)

    with Session(make_engine(url_for(round_path))) as session:
        device = session.query(Device).one()
        (blob,) = device.binary_data
        assert (blob.name, blob.data) == ("DaliGC16-Backup-Store", _BLOB)
        (mi,) = device.module_instances
        assert mi.repeat_index == "215x1 216x1"
        assert mi.arguments == [
            {"ref_id": "MD-1_A-1", "value": "46"},
            {"ref_id": "MD-1_A-2", "value": "71"},
        ]


_KEPT_FILES = {
    "UserFiles/plan.pdf": b"%PDF-1.4 plan",
    "AddinData/0B5C/settings.bin": b"\x01\x02\x03",
}


def test_capture_project_files_skips_generated_files() -> None:
    files = {
        **_KEPT_FILES,
        "project.xml": b"<KNX/>",
        "1.xml": b"<KNX/>",
        "BinaryData/P-01-0_DI-1_X.dat": _BLOB,
    }
    blob = knxproj_import._capture_project_files(  # pyright: ignore[reportPrivateUsage]
        _FakeContents(_zero_xml("").encode(), files)
    )
    assert blob is not None
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert {name: zf.read(name) for name in zf.namelist()} == _KEPT_FILES


def test_export_writes_kept_project_files_and_signs_them(tmp_path: Path) -> None:
    src = _project_with_module_and_binary(tmp_path)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for rel, data in _KEPT_FILES.items():
            zf.writestr(rel, data)
    with Session(make_engine(url_for(src))) as session:
        project = session.query(Project).one()
        project.imported_project_files = buffer.getvalue()
        session.commit()
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        pid = next(n for n in names if n.endswith("/0.xml")).split("/")[0]
        for rel, data in _KEPT_FILES.items():
            assert zf.read(f"{pid}/{rel}") == data
        folder = {
            n[len(pid) + 1 :]: zf.read(n) for n in names if n.startswith(pid + "/")
        }
        assert verify_directory_signature(folder, zf.read(f"{pid}.signature"))
