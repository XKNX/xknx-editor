"""Values an application update carries from the old application to the new one.

Parameters are matched by their ParameterRef number and group objects by their ComObjectRef
number, each within the same module instance. A group object keeps its flags, priority,
datapoint type and links only when the new object has the same size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
    from xknxeditor.proj.models import ComObject as ComObjectRow

_PARAMETER = re.compile(r"(?:(.+)_)?U?P-\d+_R-(\d+)")
_OBJECT = re.compile(r"(?:(.+)_)?O-[\d-]+_R-(\d+)")
_INSTANCE = re.compile(r"_M-\d+_MI-\d+")

_CARRIED_FIELDS = (
    "read_flag",
    "write_flag",
    "communication_flag",
    "transmit_flag",
    "update_flag",
    "read_on_init_flag",
    "read_flag_override",
    "write_flag_override",
    "communication_flag_override",
    "transmit_flag_override",
    "update_flag_override",
    "read_on_init_flag_override",
    "priority",
    "datapoint_type_override",
)


def _split(ref: str, app_id: str, pattern: re.Pattern[str]) -> tuple[str, int] | None:
    """``(scope, number)`` of an application-prefixed ref; the scope names the module instance
    (empty at application level)."""
    prefix = f"{app_id}_"
    if not ref.startswith(prefix):
        return None
    match = pattern.fullmatch(ref[len(prefix) :])
    if match is None:
        return None
    return match.group(1) or "", int(match.group(2))


@dataclass
class CarriedParameters:
    values: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    dropped: int = 0


def carry_parameters(
    rows: Iterable[tuple[str, str]],
    old_app_id: str,
    new_app_id: str,
    indexer: ApplicationIndexer,
) -> CarriedParameters:
    """The new application's refs for the device's parameter values, matched by number."""
    carried = CarriedParameters()
    for ref, value in rows:
        key = _split(ref, old_app_id, _PARAMETER)
        if key is None:
            carried.dropped += 1
            continue
        scope, number = key
        owner = f"{new_app_id}_{_INSTANCE.sub('', scope)}" if scope else None
        definition = indexer.refs_by_number.get(owner, {}).get(number)
        if definition is None or type(indexer.type_of(definition)) is object:
            carried.dropped += 1
            continue
        tail = definition[len(owner or new_app_id) + 1 :]
        new_ref = f"{new_app_id}_{scope}_{tail}" if scope else f"{new_app_id}_{tail}"
        carried.values.append((new_ref, value))
    return carried


@dataclass
class CarriedComObjects:
    # Per new group object: the settings and links to keep, or None.
    data: list[dict[str, Any] | None] = field(
        default_factory=list[dict[str, Any] | None]
    )
    kept: int = 0
    dropped: int = 0


def carry_com_objects(
    rows: Iterable[ComObjectRow],
    old_sizes: Mapping[str, str],
    old_app_id: str,
    new_objects: Iterable[tuple[str, str]],
    new_app_id: str,
) -> CarriedComObjects:
    """Settings and links of the device's active group objects for each new group object.

    ``old_sizes`` maps the qualified ref of each active old group object to its size,
    ``new_objects`` lists the new application's qualified refs with their sizes.
    """
    from xknxeditor.proj.core.identity import qualified_com_object_ref

    old: dict[tuple[str, int], tuple[ComObjectRow, str]] = {}
    for row in rows:
        ref = qualified_com_object_ref(row.ref_id, row.instance_ref_id, old_app_id)
        size = old_sizes.get(ref)
        key = _split(ref, old_app_id, _OBJECT)
        if size is not None and key is not None:
            old[key] = (row, size)
    carried = CarriedComObjects()
    matched: set[tuple[str, int]] = set()
    for ref, size in new_objects:
        key = _split(ref, new_app_id, _OBJECT)
        entry = old.get(key) if key is not None else None
        if key is None or entry is None or entry[1] != size:
            carried.data.append(None)
            continue
        row = entry[0]
        matched.add(key)
        links = sorted(row.links, key=lambda link: not link.is_sending)
        carried.data.append(
            {
                "fields": {name: getattr(row, name) for name in _CARRIED_FIELDS},
                "links": [[link.group_address_id, link.is_sending] for link in links],
            }
        )
    carried.kept = len(matched)
    carried.dropped = len(old) - len(matched)
    return carried
