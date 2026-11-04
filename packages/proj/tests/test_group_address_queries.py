"""All group addresses are read with their links and groups in a few queries, however many there
are: the Group Monitor and the views read them right after a project opened."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event

from xknxeditor.proj import ProjectService


def _project(tmp_path: Path, count: int) -> tuple[ProjectService, str, dict[int, int]]:
    """``count`` group addresses in main group 1, every other one linked to one group object
    (sending); the main group forces Security off. Returns the address ids by address."""
    svc = ProjectService()
    pid = svc.create(tmp_path / f"p{count}.xknx", f"P-{count}")
    area = svc.create_area(pid, 0, 2, "A")
    line = svc.create_line(pid, area, 1, "L")
    segment = next(
        ln.segments[0].id
        for a in svc.topology(pid, 0).areas
        for ln in a.lines
        if ln.id == line
    )
    device = svc.add_device(
        pid,
        segment,
        "M-1_H-1_P-1",
        address=1,
        hardware2program_ref_id="M-1_H-1_HP-1",
        com_objects=[("M-1_A-1_O-1_R-1", None)],
    )
    com_object = next(
        co.id for d in svc.devices(pid) if d.id == device for co in d.com_objects
    )
    ids = {
        0x0800 + i: svc.create_group_address(pid, 0, 0x0800 + i, f"GA {i}")
        for i in range(1, count + 1)
    }
    for i, ga_id in enumerate(ids.values()):
        if i % 2 == 0:
            svc.link_com_object(pid, com_object, ga_id, sending=True)
    main = next(r for r in svc.group_ranges(pid, 0) if r.range_start == 0x0800)
    svc.set_group_range_security(pid, main.id, "Off")
    return svc, pid, ids


def _read(svc: ProjectService, pid: str) -> tuple[int, list[object]]:
    """The group addresses read with nothing loaded yet, and the queries that took."""
    state = svc._state(pid)  # pyright: ignore[reportPrivateUsage]
    state.session.expire_all()
    queries: list[str] = []

    def record(*args: object) -> None:
        queries.append(str(args[2]))

    event.listen(state.engine, "after_cursor_execute", record)
    try:
        gas = svc.group_addresses(pid)
    finally:
        event.remove(state.engine, "after_cursor_execute", record)
    return len(queries), list(gas)


def test_group_addresses_take_the_same_queries_however_many(tmp_path: Path) -> None:
    few_svc, few_pid, _ = _project(tmp_path, 4)
    many_svc, many_pid, ids = _project(tmp_path, 40)
    few, _ = _read(few_svc, few_pid)
    many, gas = _read(many_svc, many_pid)
    assert many == few
    by_id = {ga.id: ga for ga in gas}  # type: ignore[attr-defined]
    linked = [by_id[ga_id] for i, ga_id in enumerate(ids.values()) if i % 2 == 0]
    unlinked = [by_id[ga_id] for i, ga_id in enumerate(ids.values()) if i % 2 == 1]
    assert all(len(ga.links) == 1 for ga in linked)
    assert all(ga.links == [] for ga in unlinked)
    assert all(ga.effective_security_mode == "Off" for ga in gas)  # type: ignore[attr-defined]
    few_svc.close(few_pid)
    many_svc.close(many_pid)
