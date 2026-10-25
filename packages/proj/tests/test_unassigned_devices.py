"""Devices on no line ("Unlink" in the topology): no segment and no individual address, undone in
one step, back on a line with the line's next free address, and kept through export and import
(``<UnassignedDevices>``)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj


def _project(tmp_path: Path) -> tuple[ProjectService, str, int]:
    """A new project with a device 1.1.1 on the line 1.1; returns the device's id."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    segment = next(
        s.id
        for a in svc.topology(pid, 0).areas
        if a.address == 1
        for ln in a.lines
        if ln.address == 1
        for s in ln.segments
    )
    device = svc.add_device(pid, segment, "M-1_H-1_P-1", address=1, name="Switch")
    return svc, pid, device


def test_a_device_is_unlinked_and_back_on_a_line(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    svc.unlink_device(pid, device)
    placement = svc.device_placement(pid, device)
    assert (placement.segment_id, placement.line_id, placement.address) == (
        None,
        None,
        None,
    )
    assert svc.individual_address(pid, device) is None
    assert device not in svc.device_segments(pid)
    # Unlinking again changes nothing.
    entries = len(svc.history(pid))
    svc.unlink_device(pid, device)
    assert len(svc.history(pid)) == entries
    # One undo puts it back; redo takes it off again.
    assert svc.undo(pid)
    assert svc.individual_address(pid, device) == "1.1.1"
    assert svc.redo(pid)
    assert svc.individual_address(pid, device) is None
    # An address puts it on that line again.
    svc.set_individual_address(pid, device, "1.1.5")
    assert svc.individual_address(pid, device) == "1.1.5"
    assert svc.undo(pid)
    assert svc.individual_address(pid, device) is None


def test_devices_on_no_line_are_exported_and_imported(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    svc.unlink_device(pid, device)
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out, schema="23")
    with zipfile.ZipFile(out) as archive:
        name = next(n for n in archive.namelist() if n.endswith("/0.xml"))
        root = ET.fromstring(archive.read(name))
    container = next(e for e in root.iter() if e.tag.endswith("}UnassignedDevices"))
    (exported,) = [e for e in container if e.tag.endswith("}DeviceInstance")]
    assert exported.get("Name") == "Switch"
    assert exported.get("Address") is None

    imported = tmp_path / "imported.xknx"
    import_knxproj(out, imported)
    svc2 = ProjectService()
    pid2 = svc2.open(imported)
    (row,) = [d for d in svc2.devices(pid2) if d.name == "Switch"]
    assert (row.segment_id, row.address) == (None, None)
    svc2.close(pid2)
