"""Values an application update carries from the old application to the new one.

Parameters are matched by their ParameterRef number and group objects by their ComObjectRef
number, each within the same module instance. A group object keeps its flags, priority,
datapoint type and links only when the new object has the same size. A parameter the old device
holds no value for carries its old default where the new device has another value (see
:func:`carry_values`). The values of parameters in a calculation are set one after another,
each running its calculations (see :func:`replay_calculated`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xknxeditor.namespaces.intermediate.parameter_type_t_type_float import (
    ParameterTypeTypeFloat,
)
from xknxeditor.prod.script.values import check_value

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI
    from xknxeditor.proj.models import ComObject as ComObjectRow

_PARAMETER = re.compile(r"(?:(.+)_)?U?P-\d+_R-(\d+)")
_OBJECT = re.compile(r"(?:(.+)_)?O-[\d-]+_R-(\d+)")
# The instance part of a module-scoped ref, a submodule instance's with the instance it is in
# (``_M-1_MI-2`` of ``MD-1_M-1_MI-2``, ``_M-1_MI-2_SM-1_M-1_MI-2-1`` of
# ``MD-1_M-1_MI-2_SM-1_M-1_MI-2-1_SM-1``).
_INSTANCE = re.compile(r"_M-\d+_MI-[\d-]+(?:_SM-[^_]+_M-\d+_MI-[\d-]+)*")

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


def unstored_defaults(
    refs: Iterable[str],
    stored: Iterable[str],
    app_id: str,
    indexer: ApplicationIndexer,
) -> list[tuple[str, str]]:
    """The parameters of ``refs`` (every ParameterRef of a device of ``indexer``'s application
    ``app_id``, qualified) the device holds no value for, with their defaults: those no ref of
    ``stored`` (the refs of its values) names by number in the same module instance. Refs with
    no access count as well."""
    held = {
        key for ref in stored if (key := _split(ref, app_id, _PARAMETER)) is not None
    }
    result: list[tuple[str, str]] = []
    for ref in refs:
        key = _split(ref, app_id, _PARAMETER)
        if key is None or key in held:
            continue
        value = indexer.default_value(_INSTANCE.sub("", ref))
        if value is not None:
            result.append((ref, value))
    return result


def _same_value(value: str, current: str, tc: object, text_encoding: str) -> bool:
    """Whether ``value`` (as the type ``tc`` writes it) is the value ``current``."""
    try:
        current = check_value(current, tc, text_encoding=text_encoding)
    except ValueError:
        return False
    if isinstance(tc, ParameterTypeTypeFloat):
        return float(value) == float(current)
    return value == current


def carried_default(ui: DynamicUI, ref: str, value: str) -> str | None:
    """The old default ``value`` of the parameter ``ref``, to set on ``ui``'s device: as the
    parameter's type writes it, or ``None`` when the device has no such parameter, its type
    refuses the value or it has that value already."""
    current = ui.get_value(ref)
    if current is None:
        return None
    tc = ui.indexer.type_of(_INSTANCE.sub("", ref))
    try:
        value = check_value(value, tc, text_encoding=ui.text_encoding)
    except ValueError:
        return None
    return None if _same_value(value, current, tc, ui.text_encoding) else value


def carry_defaults(
    ui: DynamicUI, values: Iterable[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Sets on ``ui``'s device the old defaults ``values`` (of parameters in no calculation the
    old device holds no value for) that differ from its values (see :func:`carried_default`):
    those of the application first, then those of the module instances, which the former may
    add. Returns the values set."""
    values = list(values)
    carried: list[tuple[str, str]] = []
    for in_module in (False, True):
        batch: dict[str, str] = {}
        for ref, value in values:
            if (_INSTANCE.search(ref) is not None) != in_module:
                continue
            new = carried_default(ui, ref, value)
            if new is not None:
                batch[ref] = new
        if batch:
            ui.apply_parameter_values(batch)
            carried.extend(batch.items())
    return carried


def carry_values(
    ui: DynamicUI, values: Sequence[tuple[str, str]], unstored: Collection[str]
) -> list[tuple[str, str]]:
    """The values to store for a device updated to ``ui``'s application.

    ``values`` are the old device's parameters in the old application's order (see
    :func:`in_application_order`), matched to the new application: its values, and for the
    refs ``unstored`` names the old defaults of those it holds no value for. ``ui`` holds the
    new application's defaults and the values in no calculation. An old default in no
    calculation is set where it differs from the new device's value (:func:`carry_defaults`);
    then the parameters in a calculation are set one after another (:func:`replay_calculated`).
    So a setting left unchanged keeps the old version's default, also where the new version
    has another one, and a value stored for a parameter wins over every default.
    """
    indexer = ui.indexer
    defaults = carry_defaults(
        ui,
        (
            (ref, value)
            for ref, value in values
            if ref in unstored and not in_calculation(indexer, ref)
        ),
    )
    return [*replay_calculated(ui, values, unstored), *defaults]


def in_application_order(
    rows: Iterable[tuple[str, str]], indexer: ApplicationIndexer
) -> list[tuple[str, str]]:
    """``rows`` (refs of the application ``indexer`` indexes, with their values) in the order
    the application lists its ParameterRefs, those of a module definition in its own order; refs
    it does not list last, as given."""
    order = {ref: n for n, ref in enumerate(indexer.parameter_refs)}
    return sorted(
        rows, key=lambda row: order.get(_INSTANCE.sub("", row[0]), len(order))
    )


def in_calculation(indexer: ApplicationIndexer, ref: str) -> bool:
    """Whether the (possibly module-qualified) parameter ref ``ref`` is an input or an output of
    one of the application's parameter calculations."""
    local = _INSTANCE.sub("", ref)
    return bool(indexer.calculations_for_l(local) or indexer.calculations_for_r(local))


def replay_calculated(
    ui: DynamicUI,
    values: Sequence[tuple[str, str]],
    unstored: Collection[str] = (),
) -> list[tuple[str, str]]:
    """The values to store for a device updated to ``ui``'s application.

    ``ui`` holds the new application's defaults and the values of ``values`` that are in no
    calculation. The values of the parameters in a calculation are then set one after another,
    in the order of ``values`` (the old application's, see :func:`in_application_order`): each
    one that differs from the value its parameter has by then is set and runs the calculations
    of its parameter, as an edit does; one that equals it changes nothing. So a calculated
    setting that the inputs set before it already gave its value leaves them as they are, while
    one they did not give it writes over them (an input set after it then gets its own value
    back). The refs ``unstored`` names hold old defaults: one is set like the others, as its
    type writes it (see :func:`carried_default`), and kept only when it changed the value.
    Returns the other ``values`` with these, and every value a calculation wrote, as they end
    up.
    """
    indexer = ui.indexer
    stored = {ref: value for ref, value in values if ref not in unstored}
    replayed: list[str] = []
    for ref, value in values:
        if not in_calculation(indexer, ref):
            continue
        if ref in unstored:
            carried = carried_default(ui, ref, value)
            if carried is None:
                continue
            value = carried
        replayed.append(ref)
        if ui.get_value(ref) == value:
            continue
        ui.apply_parameter_values({ref: value})
        replayed.extend(ui.recalculate([ref]))
    for ref in dict.fromkeys(replayed):
        value = ui.get_value(ref)
        if value is not None:
            stored[ref] = value
    return list(stored.items())


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
