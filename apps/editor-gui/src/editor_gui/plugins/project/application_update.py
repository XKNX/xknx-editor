"""Values an application update carries from the old application to the new one.

Parameters are matched by their ParameterRef number and group objects by their ComObjectRef
number, each within the same module instance. A group object keeps its flags, priority,
datapoint type and links only when the new object has the same size. Every parameter of the old
device, with its value or, when it holds none, its old default, is set on the new device one
after another, as an edit sets it (see :func:`carry_values`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xknxeditor.prod.script import CalculationError

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI
    from xknxeditor.proj.models import ComObject as ComObjectRow

_PARAMETER = re.compile(r"(?:(.+)_)?U?P-\d+_R-(\d+)")
_OBJECT = re.compile(r"(?:(.+)_)?O-[\d-]+_R-(\d+)")
# The instance part of a module-scoped ref, a submodule instance's with the instance it is in
# (``_M-1_MI-2`` of ``MD-1_M-1_MI-2``, ``_M-1_MI-2_SM-1_M-1_MI-2-1`` of
# ``MD-1_M-1_MI-2_SM-1_M-1_MI-2-1_SM-1``).
_INSTANCE = re.compile(r"_M-\d+_MI-[\d-]+(?:_SM-[^_]+_M-\d+_MI-[\d-]+)*")
# The parameter part of a ref: what is left is the application, or the module instance.
_TAIL = re.compile(r"_U?P-\d+_R-\d+$")
# How often the values of the application, or of one module instance, are set at most: again
# while setting one of them stopped an Assign applying.
_MAX_PASSES = 3

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


def with_argument_values(
    rows: Iterable[tuple[str, str]], ui: DynamicUI
) -> list[tuple[str, str]]:
    """``rows`` (parameters of ``ui``'s device with their values), each parameter of a module
    instance that takes the value of one of its arguments (see
    :meth:`~xknxeditor.prod.parser_v2.dynamic.DynamicUI.argument_value`) with that value: the
    value the parameter has."""
    result: list[tuple[str, str]] = []
    for ref, value in rows:
        argument = ui.argument_value(ref) if _INSTANCE.search(ref) else None
        result.append((ref, value if argument is None else argument))
    return result


def module_instances(
    refs: Iterable[str], old_app_id: str, new_app_id: str
) -> list[str]:
    """The module instances the parameter refs ``refs`` of a device of the application
    ``old_app_id`` are in, in their order, named as in the application ``new_app_id`` (as
    :func:`carry_values` takes them)."""
    found: dict[str, None] = {}
    for ref in refs:
        if _INSTANCE.search(ref) is not None and ref.startswith(f"{old_app_id}_"):
            found[new_app_id + _TAIL.sub("", ref)[len(old_app_id) :]] = None
    return list(found)


@dataclass
class CarriedValues:
    # The values to store, in the order they were given, then those the update wrote.
    values: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    # The refs of the values the new device did not take: refused (by the parameter's type or
    # one of its validations) when they were set last, or of a module instance it lacks.
    not_taken: set[str] = field(default_factory=set[str])


def carry_values(
    ui: DynamicUI,
    values: Sequence[tuple[str, str]],
    stored: Collection[str],
    instances: Sequence[str] = (),
    refused: Callable[[str, str, str], None] | None = None,
    failed: Callable[[str, str, str], None] | None = None,
) -> CarriedValues:
    """Set the values ``values`` on ``ui``'s new device, of the new application, which holds
    its defaults: the outcome of the update, the values to store.

    ``values`` are every parameter of the old device in the old application's order (see
    :func:`in_application_order`), matched to the new application: the values it holds (the
    refs ``stored`` names) and the old defaults of those it holds none for. The values of the
    application come first, then those of each module instance (those of the instances first,
    in the order ``instances`` names them, see :func:`module_instances`, then those of the
    submodule instances), each only while the new device has that instance.

    Each value is set as an edit sets it (see
    :meth:`~xknxeditor.prod.parser_v2.dynamic.DynamicUI.carry_value`): one the parameter's type
    or one of its validations refuses is left out, and ``refused(ref, value, message)`` is
    told; one the parameter has by then changes nothing; any other is set and runs the
    parameter's calculations. So a value set later wins over what a calculation of an earlier
    one wrote, and a setting left unchanged keeps the old version's default, also where the
    new version has another one. A value whose calculations fail stays set, with the outputs
    written before the failure, and ``failed(ref, value, message)`` is told.

    Each change, of the value and of each output its calculations write, is evaluated on its
    own: whether it stopped an Assign applying (its target keeps what the Assign wrote). When,
    after a value is set (neither refused nor failed), the last change made by then had, the
    values of the application or of the instance are all set again once they are through, at
    most three times in all.

    The values to store are the stored ones the device took, as they end up, and every other
    value the update set or wrote (a calculation, an Assign)."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for ref, value in values:
        key = _TAIL.sub("", ref) if _INSTANCE.search(ref) is not None else ""
        groups.setdefault(key, []).append((ref, value))
    rank = {key: n for n, key in enumerate(instances)}
    modules = sorted(
        (key for key in groups if key),
        key=lambda key: ("_SM-" in key, rank.get(key, len(rank))),
    )
    before = ui.explicit_values()
    active = ui.active_assigns() if ui.has_assigns else frozenset[tuple[str, int]]()
    not_taken: set[str] = set()
    # Whether the last change stopped an Assign applying.
    deactivated = False

    def written(ref: str) -> None:
        nonlocal deactivated, active
        deactivated, active = _assigns_after(ui, ref, active)

    for key in ["", *modules]:
        group = groups.get(key, [])
        if key and group and not ui.has_parameter(group[0][0]):
            not_taken.update(ref for ref, _ in group)
            continue
        for _ in range(_MAX_PASSES):
            again = False
            for ref, value in group:
                try:
                    ui.carry_value(ref, value, written)
                except CalculationError as exc:
                    # Set: taken, but not checked.
                    not_taken.discard(ref)
                    if failed is not None:
                        failed(ref, value, str(exc))
                    continue
                except ValueError as exc:
                    not_taken.add(ref)
                    if refused is not None:
                        refused(ref, value, str(exc))
                    continue
                not_taken.discard(ref)
                again = again or deactivated
            if not again:
                break
    after = ui.explicit_values()
    result: dict[str, str] = {
        ref: after.get(ref, value)
        for ref, value in values
        if ref in stored and ref not in not_taken
    }
    for ref, value in after.items():
        if before.get(ref) != value:
            result[ref] = value
    return CarriedValues(list(result.items()), not_taken)


def _assigns_after(
    ui: DynamicUI, ref: str, active: frozenset[tuple[str, int]]
) -> tuple[bool, frozenset[tuple[str, int]]]:
    """Whether the change of the value of ``ref`` on ``ui``'s device stopped one of the
    Assigns ``active`` (those that applied before it) applying, and the Assigns that apply
    after it. Only a value that can change what the tree shows can change which Assigns
    apply."""
    if not ui.has_assigns or ui.local_ref_id(ref) not in ui.selector_param_refs:
        return False, active
    now = ui.active_assigns()
    return bool(active - now), now


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
