"""A module-based device's com-object instances must keep their identity across import/export.

For a module-based application, ``ComObjectInstanceRef/@RefId`` carries the module instance the
object belongs to (``MD-1_M-1_MI-2_O-2-0_R-0``). xknxproject's ``com_object_ref_id`` is the
application-program *definition* it resolves to, which is deliberately module-instance-stripped
(``..._MD-1_O-2-0_R-0``) and is therefore shared by every instance of the same module.

Storing only the definition collapses the instances onto one another: on a real Atios KNX bridge,
106 module com-objects on one device share just 7 definitions. Re-emitting that on export produces
an archive whose refs no longer resolve to a ModuleInstance.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from xml.etree import ElementTree as ET

from sqlalchemy.orm import Session

from xknxeditor.proj.core.events import SyncDeviceComObjects, UpdateDeviceApplication
from xknxeditor.proj.core.identity import qualified_com_object_ref
from xknxeditor.proj.core.knxproj_export import export_knxproj
from xknxeditor.proj.core.knxproj_import import _build_com_object
from xknxeditor.proj.core.service import ProjectService
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import ComObject, ComObjectLink, Device, ModuleInstance

_APP = "M-02DC_A-0000-10-DB14"


def _coir(ref_id: str, com_object_ref_id: str | None):
    """A stand-in for xknxproject's ComObjectInstanceRef (only the fields the builder reads)."""
    return SimpleNamespace(
        ref_id=ref_id,
        com_object_ref_id=com_object_ref_id,
        channel=None,
        read_flag=None,
        write_flag=None,
        communication_flag=None,
        transmit_flag=None,
        update_flag=None,
        read_on_init_flag=None,
    )


def test_import_keeps_the_module_instance_ref() -> None:
    """The stripped definition id is still stored in ref_id; the instance id must survive too."""
    row = _build_com_object(
        cast(
            "object",
            _coir("MD-1_M-1_MI-2_O-2-0_R-0", f"{_APP}_MD-1_O-2-0_R-0"),
        )  # type: ignore[arg-type]
    )
    assert row.ref_id == f"{_APP}_MD-1_O-2-0_R-0"  # definition, unchanged behaviour
    assert row.instance_ref_id == "MD-1_M-1_MI-2_O-2-0_R-0"  # instance, previously lost


def test_import_distinguishes_instances_that_share_a_definition() -> None:
    """Two instances of the same module resolve to one definition - they must stay distinct."""
    a = _build_com_object(
        cast("object", _coir("MD-1_M-1_MI-2_O-2-0_R-0", f"{_APP}_MD-1_O-2-0_R-0"))  # type: ignore[arg-type]
    )
    b = _build_com_object(
        cast("object", _coir("MD-1_M-1_MI-10_O-2-0_R-0", f"{_APP}_MD-1_O-2-0_R-0"))  # type: ignore[arg-type]
    )
    assert a.ref_id == b.ref_id  # the collapse that used to be all we stored
    assert a.instance_ref_id != b.instance_ref_id


def test_non_module_object_is_unaffected() -> None:
    row = _build_com_object(
        cast("object", _coir("O-3_R-4", f"{_APP}_O-3_R-4"))  # type: ignore[arg-type]
    )
    assert row.ref_id == f"{_APP}_O-3_R-4"
    assert row.instance_ref_id == "O-3_R-4"


def _export_com_object_refs(
    tmp_path: Path,
    rows: list[tuple[str, str]],
    instances: tuple[str, ...] = (),
) -> list[str]:
    """Build a one-device project carrying ``(ref_id, instance_ref_id)`` objects and the module
    instances ``instances`` (relative ids); return emitted RefIds."""
    src = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MOD")
    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        if area.id == area_id
        for line in area.lines
        if line.id == line_id
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        "M-02DC_H-1-3_P-ADE",
        address=1,
        name="Bridge",
        hardware2program_ref_id="M-02DC_H-1-3_HP-0000-10-DB14",
    )
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        device = s.get(Device, device_id)
        assert device is not None
        for ref_id, instance_ref_id in rows:
            s.add(
                ComObject(
                    device_id=device.id,
                    ref_id=ref_id,
                    instance_ref_id=instance_ref_id,
                )
            )
        for instance_id in instances:
            s.add(
                ModuleInstance(
                    device_id=device.id,
                    instance_id=instance_id,
                    ref_id=instance_id.rsplit("_MI-", 1)[0],
                    repeat_index="2x1",
                )
            )
        s.commit()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as z:
        name = next(n for n in z.namelist() if n.endswith("/0.xml"))
        root = ET.fromstring(z.read(name))
    return [
        el.get("RefId") or ""
        for el in root.iter()
        if el.tag.split("}")[-1] == "ComObjectInstanceRef"
    ]


def test_export_emits_the_module_qualified_ref(tmp_path: Path) -> None:
    """Each instance keeps its own RefId instead of collapsing onto the shared definition."""
    emitted = _export_com_object_refs(
        tmp_path,
        [
            (f"{_APP}_MD-1_O-2-0_R-0", "MD-1_M-1_MI-2_O-2-0_R-0"),
            (f"{_APP}_MD-1_O-2-0_R-0", "MD-1_M-1_MI-10_O-2-0_R-0"),
        ],
        instances=("MD-1_M-1_MI-2", "MD-1_M-1_MI-10"),
    )
    assert sorted(emitted) == [
        "MD-1_M-1_MI-10_O-2-0_R-0",
        "MD-1_M-1_MI-2_O-2-0_R-0",
    ]
    assert len(set(emitted)) == 2  # the collapse would have produced one distinct id


def test_export_leaves_out_the_objects_of_a_module_instance_the_device_has_not(
    tmp_path: Path,
) -> None:
    """An object left from a module instance an earlier configuration had is not written: every
    ComObjectInstanceRef of a module instance belongs to a ModuleInstance of the device, as
    importers require. Objects of the instances the device has, and of the application, stay."""
    emitted = _export_com_object_refs(
        tmp_path,
        [
            (f"{_APP}_MD-1_O-2-0_R-0", "MD-1_M-200_MI-1_O-2-0_R-1"),
            (f"{_APP}_MD-1_O-2-0_R-0", "MD-1_M-100_MI-1_O-2-0_R-1"),
            (f"{_APP}_MD-2_O-2-0_R-0", "MD-2_M-250_MI-1_SM-1_M-1_MI-1-1-1_O-2-0_R-1"),
            (f"{_APP}_O-3_R-4", ""),
        ],
        instances=("MD-1_M-100_MI-1",),
    )
    assert sorted(emitted) == ["MD-1_M-100_MI-1_O-2-0_R-1", "O-3_R-4"]


def test_export_falls_back_to_ref_id_without_an_instance_id(tmp_path: Path) -> None:
    """Objects created in the editor (not imported) carry no instance id; behaviour is unchanged."""
    emitted = _export_com_object_refs(tmp_path, [(f"{_APP}_O-3_R-4", "")])
    assert emitted == ["O-3_R-4"]


def _module_device(tmp_path: Path) -> tuple[Path, int, int]:
    """A one-device project with a linkable group address; returns ``(src, device_id, ga_id)``."""
    src = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-MOD")
    area_id = svc.create_area(pid, 0, 2, "Area 1")
    line_id = svc.create_line(pid, area_id, 1, "Line 1")
    segment_id = next(
        line.segments[0].id
        for area in svc.topology(pid, 0).areas
        if area.id == area_id
        for line in area.lines
        if line.id == line_id
    )
    device_id = svc.add_device(
        pid,
        segment_id,
        "M-02DC_H-1-3_P-ADE",
        address=1,
        name="Bridge",
        hardware2program_ref_id="M-02DC_H-1-3_HP-0000-10-DB14",
    )
    ga_id = svc.create_group_address(pid, 0, 1, "GA")
    svc.close(pid)
    return src, device_id, ga_id


def test_reconcile_keeps_module_instance_survivor(tmp_path: Path) -> None:
    """Reconcile matches on the qualified per-instance ref, so a survivor keeps its row identity,
    flag override and links instead of being deleted and recreated (the module-instance collapse).

    Two instances share one stripped definition. The target (qualified namespace) keeps instance 2
    and adds instance 3; instance 1 drops. Pre-fix, ``existing`` collapsed both onto the single
    definition and matched nothing in ``target`` -> every module object was deleted and recreated.
    """
    src, device_id, ga_id = _module_device(tmp_path)
    defn = f"{_APP}_MD-1_O-2-0_R-0"
    keep, drop = "MD-1_M-1_MI-2_O-2-0_R-0", "MD-1_M-1_MI-1_O-2-0_R-0"
    add = "MD-1_M-1_MI-3_O-2-0_R-0"

    with Session(make_engine(url_for(src))) as s:
        co_keep = ComObject(
            device_id=device_id, ref_id=defn, instance_ref_id=keep, read_flag=True
        )
        co_drop = ComObject(device_id=device_id, ref_id=defn, instance_ref_id=drop)
        s.add_all([co_keep, co_drop])
        s.flush()
        s.add(
            ComObjectLink(
                com_object_id=co_keep.id, group_address_id=ga_id, is_sending=True
            )
        )
        s.commit()
        keep_id = co_keep.id

        target: list[list[str | None]] = [
            [f"{_APP}_{keep}", None],
            [f"{_APP}_{add}", None],
        ]
        event = SyncDeviceComObjects(
            device_id=device_id, target=target, app_program_id=_APP
        )
        event.apply(s)
        s.commit()

        device = s.get(Device, device_id)
        assert device is not None
        by_qual = {
            qualified_com_object_ref(c.ref_id, c.instance_ref_id, _APP): c
            for c in device.com_objects
        }
        assert set(by_qual) == {f"{_APP}_{keep}", f"{_APP}_{add}"}
        survivor = by_qual[f"{_APP}_{keep}"]
        assert survivor.id == keep_id  # NOT deleted and recreated
        assert survivor.read_flag is True  # flag override preserved
        assert [link.group_address_id for link in survivor.links] == [ga_id]

        # Undo restores the dropped instance WITH its per-instance identity (not the stripped
        # definition), so it resolves again instead of collapsing to zero visible objects.
        event.revert(s)
        s.commit()
        device = s.get(Device, device_id)
        assert device is not None
        by_qual = {
            qualified_com_object_ref(c.ref_id, c.instance_ref_id, _APP): c
            for c in device.com_objects
        }
        assert set(by_qual) == {f"{_APP}_{keep}", f"{_APP}_{drop}"}
        assert by_qual[f"{_APP}_{drop}"].instance_ref_id == drop


def test_app_upgrade_reprefixes_fully_prefixed_instance_ref(tmp_path: Path) -> None:
    """ETS4 can store a fully app-prefixed instance_ref_id. An application upgrade must re-prefix it
    together with ref_id, or the qualified lookup double-prefixes it (NEWAPP_OLDAPP_...)."""
    src, device_id, _ = _module_device(tmp_path)
    old_app = "M-02DC_A-0000-10-DB14"
    new_app = "M-02DC_A-0000-11-ABCD"
    old_ref = f"{old_app}_O-1_R-1"

    with Session(make_engine(url_for(src))) as s:
        co = ComObject(device_id=device_id, ref_id=old_ref, instance_ref_id=old_ref)
        s.add(co)
        s.commit()
        co_id = co.id

        event = UpdateDeviceApplication(
            device_id=device_id,
            new_product_ref_id="M-02DC_H-1-3_P-NEW",
            new_hardware2program_ref_id="M-02DC_H-1-3_HP-0000-11-ABCD",
            old_app_id=old_app,
            new_app_id=new_app,
            valid_ref_ids=[f"{new_app}_O-1_R-1"],
        )
        event.apply(s)
        s.commit()
        co = s.get(ComObject, co_id)
        assert co is not None
        assert co.ref_id == f"{new_app}_O-1_R-1"
        assert co.instance_ref_id == f"{new_app}_O-1_R-1"  # re-prefixed, not doubled
        # The qualified lookup now resolves to a single, correctly-prefixed ref.
        assert (
            qualified_com_object_ref(co.ref_id, co.instance_ref_id, new_app)
            == f"{new_app}_O-1_R-1"
        )

        event.revert(s)
        s.commit()
        co = s.get(ComObject, co_id)
        assert co is not None
        assert co.ref_id == old_ref
        assert co.instance_ref_id == old_ref  # instance identity restored on undo


def test_reconcile_legacy_event_without_app_program_id_keeps_survivor(
    tmp_path: Path,
) -> None:
    """A ``SyncDeviceComObjects`` serialized before ``app_program_id`` was threaded deserializes to
    ``""``. The resolver must then fall back to the stored ``ref_id`` (the pre-resolver match), not
    build a bare ``"_"``-prefixed key that matches nothing and deletes the surviving row's flags and
    links. Uses an imported base object, whose stored ``ref_id`` already equals the qualified target.
    """
    src, device_id, ga_id = _module_device(tmp_path)
    ref = f"{_APP}_O-1_R-1"

    with Session(make_engine(url_for(src))) as s:
        co_keep = ComObject(
            device_id=device_id, ref_id=ref, instance_ref_id="O-1_R-1", read_flag=True
        )
        s.add(co_keep)
        s.flush()
        s.add(
            ComObjectLink(
                com_object_id=co_keep.id, group_address_id=ga_id, is_sending=True
            )
        )
        s.commit()
        keep_id = co_keep.id

        # Legacy payload: no app_program_id -> from_dict yields "".
        event = SyncDeviceComObjects.from_dict(
            {
                "device_id": device_id,
                "target": [[ref, None], [f"{_APP}_O-2_R-2", None]],
                "added_ids": [],
                "removed": [],
            }
        )
        assert event.app_program_id == ""
        event.apply(s)
        s.commit()

        device = s.get(Device, device_id)
        assert device is not None
        survivor = next(c for c in device.com_objects if c.ref_id == ref)
        assert survivor.id == keep_id  # kept, not deleted and recreated
        assert survivor.read_flag is True  # flag override preserved
        assert [link.group_address_id for link in survivor.links] == [ga_id]
