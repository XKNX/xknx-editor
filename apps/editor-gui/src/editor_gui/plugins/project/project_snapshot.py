"""Read what a project comparison compares (see ``ui.project_diff``) from a project, the open one
or another file opened read-only, so both sides are read alike."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from editor_gui.plugins.project.ui.project_diff import (
    DeviceSnap,
    GaSnap,
    ProjectSnapshot,
)

if TYPE_CHECKING:
    from xknxeditor.proj import ProjectService

# A group-object link as compared: the object's database id, the group address and sending.
_Link = tuple[int, str, bool]


def read_project_snapshot(svc: ProjectService, pid: str, name: str) -> ProjectSnapshot:
    """The devices and group addresses of the project ``pid`` as compared."""
    gas = svc.group_addresses(pid)
    address = {g.id: g.text for g in gas}
    links: dict[int, list[_Link]] = {}
    for link in svc.device_links(pid):
        links.setdefault(link.device_id, []).append(
            (
                link.com_object_id,
                address.get(link.group_address_id, ""),
                link.is_sending,
            )
        )
    devices = tuple(
        _device(svc, pid, row, links.get(row.id, []))
        for row in svc.devices(pid, with_contents=True)
    )
    return ProjectSnapshot(
        name=name,
        devices=devices,
        group_addresses={
            g.text: GaSnap(address=g.text, name=g.name, dpt=g.datapoint_type or "")
            for g in gas
        },
        project_id=svc.project(pid).id,
    )


def _device(svc: ProjectService, pid: str, row: Any, links: list[_Link]) -> DeviceSnap:
    return DeviceSnap(
        ia=svc.individual_address(pid, row.id) or "",
        name=row.name,
        product=row.product_name or "",
        order=row.order_number or "",
        id=row.id,
        source_id=row.source_id or "",
        application=row.application_program_ref_id or "",
        location=location_text(row.space),
        inputs=_inputs(row, links),
    )


def location_text(space: Any) -> str:
    """Where a device is placed: its space and the spaces above it ("House > Floor > Room")."""
    names: list[str] = []
    while space is not None:
        names.append(space.name or space.number)
        space = space.parent
    return " > ".join(reversed(names))


def _inputs(row: Any, links: list[_Link]) -> str:
    """The stored settings a device is built from, in a fixed order: its parameters, module
    instances and group objects (with their overrides) and the links of its group objects."""
    refs: dict[int, str] = {co.id: co.ref_id for co in row.com_objects}
    return json.dumps(
        {
            "parameters": sorted(_columns(p) for p in row.parameters),
            "modules": sorted(_columns(m) for m in row.module_instances),
            "group_objects": sorted(_columns(co) for co in row.com_objects),
            "links": sorted(
                (refs.get(co_id, ""), ga, sending) for co_id, ga, sending in links
            ),
        },
        sort_keys=True,
        default=str,
    )


def _columns(row: Any) -> str:
    """A row's stored values except its own and its device's id, as a sortable text."""
    values = {
        column.key: getattr(row, column.key)
        for column in row.__table__.columns
        if column.key not in ("id", "device_id")
    }
    return json.dumps(values, sort_keys=True, default=str)
