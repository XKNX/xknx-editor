"""The edits of the topology, the backbone, building functions and trades are kept in the history:
after the project was closed and opened again they are undone and redone like before."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.medium import MEDIUM_RF, MEDIUM_TP, MediumError
from xknxeditor.proj.core.service import TradeInfo


def _state(svc: ProjectService, pid: str) -> dict[str, Any]:
    segments = {
        s.id: (s.name, s.medium_type, s.domain_address, s.bus_access)
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        for s in ln.segments
    }

    def trades(items: list[TradeInfo]) -> list[Any]:
        return [
            (
                t.name,
                t.number,
                t.description,
                trades(t.children),
                [d.device.id for d in t.devices],
            )
            for t in items
        ]

    functions = {
        f.id: (f.name, f.description)
        for space in svc.space_tree(pid, 0)
        for f in space.functions
    }
    return {
        "installation": svc.installation_attributes(pid),
        "segments": segments,
        "trades": trades(svc.trade_tree(pid, 0)),
        "functions": functions,
    }


def test_edits_undo_and_redo_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-0C01")
    area = next(a for a in svc.topology(pid, 0).areas if a.address == 1)
    line = next(ln for ln in area.lines if ln.address == 1)
    device = svc.add_device(
        pid, line.segments[0].id, "M-1_H-1_P-1", address=5, name="Dimmer"
    )
    building = svc.space_tree(pid, 0)[0].id
    function = svc.create_function(pid, building, "FT-1", "Light")
    before = _state(svc, pid)
    count = len(svc.history(pid))

    svc.set_ip_routing(pid, latency=4000, multicast_address="239.1.2.3")
    segment = svc.add_segment(pid, line.id, medium_type=MEDIUM_RF, name="Radio")
    svc.rename_segment(pid, segment, "Radio 1")
    svc.set_domain_address(pid, segment)
    svc.set_segment_bus_access(
        pid, line.segments[0].id, "Type=IpTunneling;HostAddress=10.0.0.9"
    )
    svc.set_function_description(pid, function, "All lights")
    light = svc.create_trade(pid, 0, "Lighting")
    heating = svc.create_trade(pid, 0, "Heating")
    svc.set_trade_field(pid, light, "number", "01")
    svc.move_trade(pid, heating, light)
    svc.add_trade_device(pid, heating, device)
    svc.remove_trade_device(pid, heating, device)
    svc.add_trade_device(pid, light, device)
    svc.remove_trade(pid, heating)
    edited = _state(svc, pid)
    steps = len(svc.history(pid)) - count
    assert edited != before
    svc.close(pid)

    svc = ProjectService()
    pid = svc.open(path)
    assert _state(svc, pid) == edited
    for _ in range(steps):
        assert svc.undo(pid)
    assert _state(svc, pid) == before
    for _ in range(steps):
        assert svc.redo(pid)
    assert _state(svc, pid) == edited
    svc.close(pid)


def test_edits_of_unknown_or_unchanged_elements(tmp_path: Path) -> None:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = next(a for a in svc.topology(pid, 0).areas if a.address == 1)
    line = next(ln for ln in area.lines if ln.address == 1)
    main = line.segments[0].id
    count = len(svc.history(pid))
    for edit in (
        lambda: svc.add_segment(pid, 999),
        lambda: svc.set_segment_medium(pid, 999, MEDIUM_TP),
        lambda: svc.set_segment_bus_access(pid, 999, "x"),
        lambda: svc.move_trade(pid, 999, None),
        lambda: svc.add_trade_device(pid, 999, 1),
        lambda: svc.device_bus_current(pid, 999, None),
        lambda: svc.check_coupler(pid, 999, None, "x"),  # type: ignore[arg-type]
    ):
        with pytest.raises(KeyError):
            edit()
    with pytest.raises(MediumError) as error:
        svc.set_segment_medium(pid, main, "MT-9")
    assert error.value.reason == "unknown_medium"
    # Setting what is already set is no step.
    svc.set_segment_medium(pid, main, MEDIUM_TP)
    svc.set_segment_bus_access(pid, main, None)
    trade = svc.create_trade(pid, 0, "Lighting")
    inner = svc.create_trade(pid, 0, "Inside", parent_id=trade)
    with pytest.raises(KeyError):
        svc.move_trade(pid, trade, 999)
    with pytest.raises(KeyError):
        svc.add_trade_device(pid, trade, 999)
    for target in (trade, inner):
        with pytest.raises(ValueError, match="into itself"):
            svc.move_trade(pid, trade, target)
    svc.move_trade(pid, inner, trade)
    assert len(svc.history(pid)) == count + 2
