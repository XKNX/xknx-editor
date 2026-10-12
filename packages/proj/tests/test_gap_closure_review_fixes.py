"""Regression tests for two round-trip bugs the gap-closure Codex review flagged.

A. Function ``@Comment``/``@Description`` were lost on a full export -> re-import cycle. xknxproject
   derives ``XMLFunction.identifier`` by stripping the leading project-prefix segment of the
   ``@Id`` (``Id.split("_", 1)[1]``); the raw metadata reader keyed by the full ``@Id``, so the
   builder's ``function_meta`` lookup never matched and the metadata silently defaulted to empty.

B. Removing a device dropped its ``TradeDevice`` memberships (the FK is ``ON DELETE CASCADE``), but
   the undo snapshot only walked ORM ``delete-orphan`` children, so undo could not restore them. The
   snapshot now also captures rows behind an ``ON DELETE CASCADE`` FK.

C. Removing a device carrying ``DeviceBinaryData`` (DCA/backup blobs) raised
   ``TypeError: Object of type bytes is not JSON serializable`` because the undo snapshot copied the
   ``LargeBinary`` column verbatim into the event's JSON payload. Bytes are now base64-encoded in the
   snapshot and decoded on restore.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import (
    Device,
    DeviceBinaryData,
    Function,
    Installation,
    Space,
    Trade,
    TradeDevice,
)


def _project_with_device(tmp_path: Path) -> tuple[Path, int]:
    src = tmp_path / "src.xknx"
    svc = ProjectService()
    pid = svc.create(src, "P-GAP")
    area_id = svc.create_area(pid, 0, 2, "A1")
    line_id = svc.create_line(pid, area_id, 2, "L2")
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
        "M-1_H-1_P-1",
        address=5,
        name="Switch",
        hardware2program_ref_id="M-1_H-1_HP-1",
    )
    svc.close(pid)
    return src, device_id


def test_function_comment_description_survive_full_reimport(tmp_path: Path) -> None:
    src, _ = _project_with_device(tmp_path)
    with Session(make_engine(url_for(src))) as s:
        installation = s.query(Installation).one()
        space = Space(space_type="Room", name="Room 1", order=0)
        space.functions.append(
            Function(name="Light", comment="fn-comment", description="fn-desc", order=0)
        )
        installation.spaces.append(space)
        s.commit()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23")
    round_path = tmp_path / "round.xknx"
    import_knxproj(out, round_path)

    with Session(make_engine(url_for(round_path))) as s:
        functions = s.query(Function).all()
        assert [(f.name, f.comment, f.description) for f in functions] == [
            ("Light", "fn-comment", "fn-desc")
        ]


def test_undo_remove_device_restores_trade_membership(tmp_path: Path) -> None:
    src, device_id = _project_with_device(tmp_path)
    with Session(make_engine(url_for(src))) as s:
        device = s.get(Device, device_id)
        assert device is not None
        installation = device.segment.line.area.installation
        trade = Trade(name="Electrical", number="1", order=0)
        trade.devices.append(TradeDevice(device=device, order=0))
        installation.trades.append(trade)
        s.commit()

    svc = ProjectService()
    pid = svc.open(src)
    svc.remove_device(pid, device_id)
    # The DB drops the membership via ON DELETE CASCADE; undo must bring it back.
    svc.undo(pid)
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        links = s.query(TradeDevice).all()
        assert [(link.device_id, link.order) for link in links] == [(device_id, 0)]


def test_undo_remove_device_restores_binary_data(tmp_path: Path) -> None:
    src, device_id = _project_with_device(tmp_path)
    blob = b"\x00\x01\x02backup\xff"
    with Session(make_engine(url_for(src))) as s:
        device = s.get(Device, device_id)
        assert device is not None
        device.binary_data.append(DeviceBinaryData(name="DaliGC16-Backup", data=blob))
        s.commit()

    svc = ProjectService()
    pid = svc.open(src)
    # Must not raise TypeError while serializing the undo snapshot to the events JSON column.
    svc.remove_device(pid, device_id)
    svc.undo(pid)
    svc.close(pid)

    with Session(make_engine(url_for(src))) as s:
        rows = s.query(DeviceBinaryData).all()
        assert [(r.name, r.data) for r in rows] == [("DaliGC16-Backup", blob)]


def test_trade_ref_to_unassigned_device_survives_roundtrip(tmp_path: Path) -> None:
    """A Trade/DeviceInstanceRef to an unassigned device (resolving to no TradeDevice row) is kept
    verbatim and re-emitted on export, not silently dropped (#10)."""
    src, device_id = _project_with_device(tmp_path)
    with Session(make_engine(url_for(src))) as s:
        device = s.get(Device, device_id)
        assert device is not None
        installation = device.segment.line.area.installation
        trade = Trade(name="Electrical", number="1", order=0)
        trade.devices.append(TradeDevice(device=device, order=0))
        trade.unresolved_device_refs = ["P-GAP-0_DI-99"]
        installation.trades.append(trade)
        s.commit()

    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, schema="23")
    round_path = tmp_path / "round.xknx"
    import_knxproj(out, round_path)

    with Session(make_engine(url_for(round_path))) as s:
        trades = s.query(Trade).all()
        assert len(trades) == 1
        assert trades[0].unresolved_device_refs == ["P-GAP-0_DI-99"]
