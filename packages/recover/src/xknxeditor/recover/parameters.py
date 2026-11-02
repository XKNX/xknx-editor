"""Best-effort recovery of parameter values from a device's memory.

Parameter memory has no self-describing structure, so recovery is driven by the
application: :func:`xknxeditor.prod...decode_memory_parameters` re-reads each
memory-backed parameter's field using the application's layout and decodes the
reliable types (integers and enumerations) directly, reusing the forward encoder
as the oracle for enumerations. Lossy types (text, float, colour, date, IP, raw
data) cannot be reconstructed from bytes and are reported as unknown.

The decoder is keyed by parameter id; a project stores values per parameter
*reference*. Since every reference of a parameter shares the same memory cell, a
recovered value is assigned to each of that parameter's references.

Some settings are not on the device at all: a parameter without memory or property
(e.g. "Channel A: active") only decides which channels, parameters and group objects
the application shows. Their values are inferred from the group objects the device's
tables link (see :func:`_infer_unstored`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xknxeditor.prod import Application
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI


@dataclass(frozen=True, slots=True)
class RecoveredParameters:
    """Recovered parameter reference values plus the parameters left unknown."""

    # parameter reference id -> recovered value.
    values: dict[str, str]
    # parameter ids whose value could not be reconstructed from bytes.
    unknown: list[str]


# Decoding rounds at most: each one can only change which alternatives own memory.
_DECODE_ROUNDS = 8
# Inference rounds at most: each one can only show settings nested one level deeper (a
# channel first, then the settings inside it), or take one setting from the memory.
_INFER_ROUNDS = 16
# Values tried for the memory per round at most, and values tried together with the
# settings they show.
_MEMORY_TRIES = 24
_LOOK_AHEAD_TRIES = 12

_ValueMap = dict[str, str]


def _parameter_to_refs(application: Application) -> dict[str, list[str]]:
    """Map each parameter id to the reference ids that point at it."""
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    indexer = ApplicationIndexer(application.program)
    refs: dict[str, list[str]] = {}
    for ref_id, ref in indexer.parameter_refs.items():
        refs.setdefault(ref.ref_id, []).append(ref_id)
    return refs


def recover_parameters(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None = None,
    project_code_page: str | None = None,
    linked_objects: Collection[int] = (),
    object_sizes: Mapping[int, str] | None = None,
) -> RecoveredParameters:
    """Recover parameter reference values from a device's memory and properties.

    ``parameter_segments`` is ``{segment_id: bytes}`` and ``property_values`` is
    ``{(object_index, property_id, occurrence): bytes}``, both as read off the
    device (see :mod:`xknxeditor.recover.read_config`). ``project_code_page`` is the
    ``CodePage`` of the project the device goes into (``None``: the system language),
    which some applications encode their text parameters with. ``linked_objects`` are
    the numbers of the group objects the device's tables link, ``object_sizes`` their
    object sizes as far as the tables give them: settings the device keeps no copy of
    take the values that show these objects, or that make the memory fit the union
    members they choose (see :func:`_infer_from_memory`). Returns the recovered value for every
    parameter reference whose type is reconstructable, and the parameter ids whose
    value is unknown (lossy types / absent data).
    """
    if application.dynamic_ui() is None:
        return RecoveredParameters(values={}, unknown=[])
    linked = set(linked_objects)
    sizes = dict(object_sizes or {})
    inferred: _ValueMap = {}
    dead: set[tuple[str, str]] = set()
    values, unknown = _decode(
        application, parameter_segments, property_values, project_code_page, inferred
    )
    for _ in range(_INFER_ROUNDS):
        chosen = (
            _infer_unstored(
                application, values, linked, sizes, project_code_page, dead=dead
            )
            if linked
            else {}
        ) or _infer_from_memory(
            application,
            parameter_segments,
            property_values,
            project_code_page,
            values,
            unknown,
            linked,
            sizes,
        )
        if not chosen:
            break
        inferred.update(chosen)
        # A channel shown now has its own settings in memory, and an inferred setting can
        # pick another alternative of a union: decode again with them.
        values, unknown = _decode(
            application,
            parameter_segments,
            property_values,
            project_code_page,
            inferred,
        )
    return RecoveredParameters(values=values, unknown=unknown)


def _decode(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None,
    project_code_page: str | None,
    inferred: Mapping[str, str],
) -> tuple[_ValueMap, list[str]]:
    """Decode the memory and properties against a UI seeded with ``inferred``, returning the
    values (``inferred`` included) and the parameters left unknown."""
    from .recover import seed_dynamic_ui

    ui = (
        seed_dynamic_ui(application, inferred, project_code_page)
        if inferred
        else application.dynamic_ui()
    )
    assert ui is not None
    ui.project_code_page = project_code_page
    parameter_to_refs = _parameter_to_refs(application)
    values: _ValueMap = {}
    unknown: list[str] = []
    # Which parameters own a memory field depends on the values (the active alternative of
    # a union): decode again with a UI seeded with what was read until nothing changes.
    for _ in range(_DECODE_ROUNDS):
        decoded = dict(ui.decode_memory_parameters(parameter_segments))
        if property_values:
            # Memory and property parameters are disjoint, so a plain merge is safe.
            decoded.update(ui.decode_property_parameters(property_values))
        read: _ValueMap = {}
        unknown = []
        for parameter_id, value in decoded.items():
            if value is None:
                unknown.append(parameter_id)
                continue
            for ref_id in parameter_to_refs.get(parameter_id, []):
                read[ref_id] = value
        if read == values:
            break
        values = read
        seeded_ui = seed_dynamic_ui(
            application, {**inferred, **values}, project_code_page
        )
        if seeded_ui is None:
            break
        ui = seeded_ui

    # Module-instance parameters: decode against a UI seeded with the just-recovered
    # top-level values so the module instances match the device. The module decoder
    # already returns instance-qualified reference ids, which is what a project
    # stores, so they merge straight into values.
    values = _with_calculated(application, {**inferred, **values}, project_code_page)
    seeded = seed_dynamic_ui(application, values, project_code_page)
    if seeded is not None:
        for ref_id, value in seeded.decode_module_parameters(
            parameter_segments, property_values or {}
        ).items():
            if value is None:
                unknown.append(ref_id)
            else:
                values[ref_id] = value
    return _with_calculated(application, values, project_code_page), unknown


def _with_calculated(
    application: Application, values: Mapping[str, str], project_code_page: str | None
) -> _ValueMap:
    """``values`` plus what the application's calculations compute from them (a shown setting
    the device keeps no copy of, computed from a stored one). A read value is kept."""
    from .recover import seed_dynamic_ui

    result = dict(values)
    ui = seed_dynamic_ui(application, values, project_code_page)
    if ui is None:
        return result
    for ref_id, (_old, new) in ui.recalculate(list(values), stored=True).items():
        if new is not None:
            result.setdefault(ref_id, new)
    return result


def _shown_objects(ui: DynamicUI, sizes: Mapping[int, str] | None = None) -> set[int]:
    """The numbers of the group objects a UI shows; with ``sizes`` (number -> object size the
    device's table gives) not those of another size (the same object with another datapoint
    type)."""
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab

    numbers: set[int] = set()
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            size = sizes.get(node.number) if sizes else None
            if size is None or node.object_size == size:
                numbers.add(node.number)
        elif isinstance(node, UiTab | UiParameterBlock):
            stack.extend(node.children)
    return numbers


def _is_unstored(ui: DynamicUI, ref_id: str) -> bool:
    """Whether the device keeps no copy of a parameter: no memory, no property, not in a
    union. One that a calculation computes from the values read is in the values already."""
    from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_parameter import (
        ApplicationProgramStaticParametersParameter,
    )
    from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_parameter import (
        ModuleDefStaticParametersParameter,
    )

    indexer = ui.indexer
    ref = indexer.parameter_refs.get(ui.local_ref_id(ref_id))
    parameter = indexer.parameters.get(ref.ref_id) if ref is not None else None
    return (
        isinstance(
            parameter,
            ApplicationProgramStaticParametersParameter
            | ModuleDefStaticParametersParameter,
        )
        and parameter.choice is None
    )


def _infer_unstored(
    application: Application,
    values: Mapping[str, str],
    linked: set[int],
    sizes: Mapping[int, str],
    project_code_page: str | None,
    *,
    known: frozenset[str] = frozenset(),
    look_ahead: bool = True,
    dead: set[tuple[str, str]] | None = None,
) -> _ValueMap:
    """Values for shown settings the device keeps no copy of, chosen to show more of the
    ``linked`` group objects (in their ``sizes``) than ``values`` alone.

    Every other value of each such setting (but those in ``known``) is tried; a setting takes
    the one that shows the most linked objects more (net of the ones it hides), of those the one
    that shows the fewest objects that are not linked. Best first, each is taken that shows
    more together with those taken before it. When no value shows more, a value that shows
    other such settings is taken together with theirs if those show more (``look_ahead``: a
    channel whose own objects are not linked, but one behind a setting inside it; the first
    ``_LOOK_AHEAD_TRIES`` such values). Values
    that show no more linked objects and no other setting are added to ``dead`` and not
    tried again. Empty when nothing shows more.
    """
    from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try

    from .recover import seed_dynamic_ui

    ui = seed_dynamic_ui(application, values, project_code_page)
    assert ui is not None
    base = _shown_objects(ui, sizes) & linked
    if base == linked:
        return {}
    shown_settings = shown_parameters(ui.ui())
    # A trial value only adds settings of the instances it makes, which have no values yet:
    # seeding with the instances there are now is enough.
    instances = ui.get_module_instances()

    def shown(trial: Mapping[str, str]) -> tuple[set[int], int, bool]:
        """The linked objects ``values`` with ``trial`` show, how many others, and whether
        they show such settings that are not shown now."""
        seeded = seed_dynamic_ui(
            application,
            {**values, **trial},
            project_code_page,
            module_instances=instances,
        )
        assert seeded is not None
        objects = _shown_objects(seeded, sizes)
        reveals = any(
            ref_id not in shown_settings
            and ref_id not in known
            and seeded.local_ref_id(ref_id) in seeded.object_param_refs
            and _is_unstored(seeded, ref_id)
            for ref_id in shown_parameters(seeded.ui())
        )
        return objects & linked, len(objects - linked), reveals

    best: list[tuple[tuple[int, int], str, str]] = []
    neutral: list[tuple[str, str]] = []
    for ref_id, parameter in shown_settings.items():
        if (
            ref_id in values
            or ref_id in known
            or ui.local_ref_id(ref_id) not in ui.object_param_refs
            or not _is_unstored(ui, ref_id)
        ):
            continue
        current = ui.get_value(ref_id) or parameter.default_value
        score, value = (0, 0), ""
        for candidate in values_to_try(parameter, current):
            if dead is not None and (ref_id, candidate) in dead:
                continue
            objects, unlinked, reveals = shown({ref_id: candidate})
            gain = len(objects - base) - len(base - objects)
            if gain > 0 and (gain, -unlinked) > score:
                score, value = (gain, -unlinked), candidate
            elif objects == base and reveals:
                neutral.append((ref_id, candidate))
            elif gain <= 0 and dead is not None:
                dead.add((ref_id, candidate))
        if value:
            best.append((score, ref_id, value))
    if best:
        # Settings that show the same objects (several channels each showing a shared
        # object) are not all taken: only the one that shows them first.
        best.sort(key=lambda entry: entry[0], reverse=True)
        (gain, _), first_ref, first_value = best[0]
        chosen: _ValueMap = {first_ref: first_value}
        covered = len(base) + gain
        for _, ref_id, value in best[1:]:
            count = len(shown({**chosen, ref_id: value})[0])
            if count > covered:
                chosen[ref_id] = value
                covered = count
        return chosen
    if not look_ahead:
        return {}
    # Only the settings a neutral value shows are tried behind it.
    seen = known | shown_settings.keys()
    found: _ValueMap = {}
    found_count = len(base)
    for ref_id, candidate in neutral[:_LOOK_AHEAD_TRIES]:
        nested = _infer_unstored(
            application,
            {**values, ref_id: candidate},
            linked,
            sizes,
            project_code_page,
            known=seen,
            look_ahead=False,
        )
        if nested:
            trial = {ref_id: candidate, **nested}
            count = len(shown(trial)[0])
            if count > found_count:
                found, found_count = trial, count
    return found


def _decode_once(
    ui: DynamicUI,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None,
) -> dict[str, str | None]:
    """One decode pass over the memory and properties against ``ui``: top-level parameters
    by id, module parameters by their instance's ref, ``None`` where the bytes are no value
    of the type."""
    decoded = dict(ui.decode_memory_parameters(parameter_segments))
    if property_values:
        decoded.update(ui.decode_property_parameters(property_values))
    decoded.update(
        ui.decode_module_parameters(parameter_segments, property_values or {})
    )
    return decoded


def _union_fit(
    ui: DynamicUI, decoded: Mapping[str, str | None]
) -> tuple[int, list[str]]:
    """How many members of unions a decode pass read a value of, and the (local) param-refs
    of those whose bytes it found no value of their type in."""
    from xknxeditor.namespaces.intermediate.union_parameter_t import UnionParameter

    indexer = ui.indexer

    def local_refs(key: str) -> list[str]:
        # A top-level parameter is reported by its id, a module parameter by its ref.
        if isinstance(indexer.parameters.get(key), UnionParameter):
            return [r for r, ref in indexer.parameter_refs.items() if ref.ref_id == key]
        local = ui.local_ref_id(key)
        ref = indexer.parameter_refs.get(local)
        parameter = indexer.parameters.get(ref.ref_id) if ref is not None else None
        return [local] if isinstance(parameter, UnionParameter) else []

    fitting = 0
    misfits: list[str] = []
    for key, value in decoded.items():
        refs = local_refs(key)
        if refs and value is None:
            misfits.extend(refs)
        elif refs:
            fitting += 1
    return fitting, misfits


def _infer_from_memory(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None,
    project_code_page: str | None,
    values: Mapping[str, str],
    unknown: Collection[str],
    linked: set[int],
    sizes: Mapping[int, str],
) -> _ValueMap:
    """A value for a shown setting the device keeps no copy of that leads to a member of a
    union whose bytes are no value of its type, chosen so fewer union members do so (and no
    smaller share fits) without hiding a linked object: an inactive channel, whose memory
    holds what an inactive channel writes, is not taken for an active one. The settings that
    lead to the most such members are tried first, ``_MEMORY_TRIES`` values at most. Empty
    when no value does better, or when every union member's bytes fit.
    """
    from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try

    from .recover import seed_dynamic_ui

    if not unknown:
        return {}
    ui = seed_dynamic_ui(application, values, project_code_page)
    assert ui is not None
    fitting, misfits = _union_fit(
        ui, _decode_once(ui, parameter_segments, property_values)
    )
    if not misfits:
        return {}
    weight = Counter(gate for local in misfits for gate in ui.parameter_gates(local))
    candidates: list[tuple[int, str, str]] = []
    for ref_id, parameter in shown_parameters(ui.ui()).items():
        local = ui.local_ref_id(ref_id)
        if ref_id in values or local not in weight or not _is_unstored(ui, ref_id):
            continue
        current = ui.get_value(ref_id) or parameter.default_value
        candidates += [
            (weight[local], ref_id, candidate)
            for candidate in values_to_try(parameter, current)
        ]
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    base = _shown_objects(ui, sizes) & linked
    best: _ValueMap = {}
    fewest = len(misfits)
    share = fitting / (fitting + fewest)
    for _, ref_id, candidate in candidates[:_MEMORY_TRIES]:
        seeded = seed_dynamic_ui(
            application, {**values, ref_id: candidate}, project_code_page
        )
        assert seeded is not None
        if not base <= _shown_objects(seeded, sizes):
            continue
        trial_fitting, trial_misfits = _union_fit(
            seeded, _decode_once(seeded, parameter_segments, property_values)
        )
        total = trial_fitting + len(trial_misfits)
        if len(trial_misfits) < fewest and (
            not total or trial_fitting / total >= share
        ):
            best, fewest = {ref_id: candidate}, len(trial_misfits)
    return best
