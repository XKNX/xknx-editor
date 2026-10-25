"""Trades: devices grouped by discipline in a tree of trades, edited, undone, exported and
imported again."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService, export_knxproj, import_knxproj
from xknxeditor.proj.core.service import TradeInfo


def _project(tmp_path: Path) -> tuple[ProjectService, str, int]:
    """A new project with one device on line 1.1."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = next(a for a in svc.topology(pid, 0).areas if a.address == 1)
    segment = next(ln for ln in area.lines if ln.address == 1).segments[0].id
    device = svc.add_device(pid, segment, "M-1_H-1_P-1", address=5, name="Dimmer")
    return svc, pid, device


def _names(trades: list[TradeInfo]) -> list[object]:
    return [
        (t.name, _names(t.children), [d.device.name for d in t.devices])
        if t.children or t.devices
        else t.name
        for t in trades
    ]


def test_trades_nest_and_hold_devices(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    light = svc.create_trade(pid, 0, "Lighting")
    svc.create_trade(pid, 0, "Heating")
    inside = svc.create_trade(pid, 0, "Inside", parent_id=light)
    svc.add_trade_device(pid, inside, device)
    svc.add_trade_device(pid, inside, device)  # already in it: nothing
    svc.add_trade_device(pid, light, device)  # a device can be in several trades
    assert _names(svc.trade_tree(pid, 0)) == [
        ("Lighting", [("Inside", [], ["Dimmer"])], ["Dimmer"]),
        "Heating",
    ]
    svc.set_trade_field(pid, light, "number", "01")
    svc.set_trade_field(pid, light, "description", "All lights")
    svc.set_trade_field(pid, light, "name", "Light")
    svc.set_completion_status(pid, "trade", light, "Tested")
    svc.set_comment(pid, "trade", light, "Note")
    top = svc.trade_tree(pid, 0)[0]
    assert (top.name, top.number, top.description) == ("Light", "01", "All lights")
    assert svc.completion_status(pid, "trade", light) == "Tested"
    assert svc.comment(pid, "trade", light) == "Note"
    with pytest.raises(ValueError):
        svc.set_trade_field(pid, light, "comment", "x")
    with pytest.raises(KeyError):
        svc.set_trade_field(pid, 999, "name", "x")
    with pytest.raises(KeyError):
        svc.create_trade(pid, 0, "x", parent_id=999)
    with pytest.raises(KeyError):
        svc.add_trade_device(pid, light, 999)


def test_moving_and_removing_trades_is_undone(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    light = svc.create_trade(pid, 0, "Lighting")
    heat = svc.create_trade(pid, 0, "Heating")
    inside = svc.create_trade(pid, 0, "Inside", parent_id=light)
    svc.add_trade_device(pid, inside, device)
    with pytest.raises(ValueError):
        svc.move_trade(pid, light, inside)
    with pytest.raises(ValueError):
        svc.move_trade(pid, light, light)
    svc.move_trade(pid, inside, heat)
    assert _names(svc.trade_tree(pid, 0)) == [
        "Lighting",
        ("Heating", [("Inside", [], ["Dimmer"])], []),
    ]
    svc.undo(pid)
    svc.remove_trade(pid, light)
    assert _names(svc.trade_tree(pid, 0)) == ["Heating"]
    svc.undo(pid)
    assert _names(svc.trade_tree(pid, 0)) == [
        ("Lighting", [("Inside", [], ["Dimmer"])], []),
        "Heating",
    ]
    svc.remove_trade_device(pid, inside, device)
    assert _names(svc.trade_tree(pid, 0)) == [("Lighting", ["Inside"], []), "Heating"]
    svc.undo(pid)
    svc.remove_device(pid, device)
    assert _names(svc.trade_tree(pid, 0)) == [("Lighting", ["Inside"], []), "Heating"]
    svc.undo(pid)
    assert svc.trade_tree(pid, 0)[0].children[0].devices[0].device.name == "Dimmer"


def _zero(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(
            zf.read(next(n for n in zf.namelist() if n.endswith("/0.xml")))
        )


def test_export_and_import_keep_the_trades(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    light = svc.create_trade(pid, 0, "Lighting")
    inside = svc.create_trade(pid, 0, "Inside", parent_id=light)
    svc.add_trade_device(pid, inside, device)
    svc.set_trade_field(pid, light, "number", "01")
    svc.set_trade_field(pid, light, "description", "All lights")
    svc.set_completion_status(pid, "trade", light, "Tested")
    svc.close(pid)

    out = tmp_path / "out.knxproj"
    export_knxproj(tmp_path / "p.xknx", out, schema="23")
    trades = [e for e in _zero(out).iter() if e.tag.rsplit("}", 1)[-1] == "Trade"]
    exported = next(e for e in trades if e.get("Name") == "Lighting")
    assert (exported.get("Number"), exported.get("Description")) == ("01", "All lights")
    assert exported.get("CompletionStatus") == "Tested"

    imported = tmp_path / "imported.xknx"
    import_knxproj(out, imported)
    svc2 = ProjectService()
    pid2 = svc2.open(imported)
    tree = svc2.trade_tree(pid2, 0)
    assert _names(tree) == [("Lighting", [("Inside", [], ["Dimmer"])], [])]
    assert (tree[0].number, tree[0].description) == ("01", "All lights")
    assert svc2.completion_status(pid2, "trade", tree[0].id) == "Tested"
    svc2.close(pid2)
