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
tables link (see :func:`_infer_unstored`), and then corrected where the memory the result would
write, or the group objects it shows, still differ from the device (see :func:`_refine`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from typing import TYPE_CHECKING

from xknxeditor.prod.parser_v2.allocator import AllocatorOverflow
from xknxeditor.prod.script.errors import ScriptAborted
from xknxeditor.prod.script.sandbox import AbortToken

if TYPE_CHECKING:
    from xknxeditor.prod import Application
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI
    from xknxeditor.prod.parser_v2.ui import UiParameter


class RecoveryEffort(Enum):
    """How many values the search for the settings a device keeps no copy of tries (see
    :func:`recover_parameters`): more finds settings deep in channels and module instances of
    large applications, but takes longer. ``UNLIMITED`` goes on until no value fits better (or
    it is cancelled)."""

    FAST = "fast"
    MEDIUM = "medium"
    MAXIMUM = "maximum"
    UNLIMITED = "unlimited"


@dataclass(frozen=True, slots=True)
class RecoveredParameters:
    """Recovered parameter reference values plus the parameters left unknown."""

    # parameter reference id -> recovered value.
    values: dict[str, str]
    # parameter ids whose value could not be reconstructed from bytes.
    unknown: list[str]
    # Bytes that differ, and missing linked objects or shown objects with a different size.
    differing_bytes: int = 0
    differing_objects: int = 0
    # The search stopped at the limit of its effort, or was cancelled, with values left to try:
    # more effort may find a result that fits the device.
    limited: bool = False
    # Of ``unknown``, those whose type cannot give back a value from bytes at all (a lossy
    # encoding, such as an HSV colour or a date without its year).
    lossy: list[str] = field(default_factory=list[str])

    @property
    def complete(self) -> bool:
        """Whether the values match the memory, linked objects and observed object sizes."""
        return not (self.differing_bytes or self.differing_objects)

    @property
    def not_found(self) -> list[str]:
        """Of ``unknown``, those whose bytes are no value of their type while the result still
        differs from the device: read with another configuration, they may be (more effort)."""
        if self.complete:
            return []
        lossy = set(self.lossy)
        return [key for key in self.unknown if key not in lossy]

    @property
    def unrecoverable(self) -> list[str]:
        """Of ``unknown``, those the device does not hold a value of: a lossy type, or bytes
        that are no value of the type although the result fits the device."""
        not_found = set(self.not_found)
        return [key for key in self.unknown if key not in not_found]


# Decoding rounds at most, a safeguard only: each one can change which alternatives own memory
# and show the settings behind what it read (a chain of selectors 16 deep in a Siemens push
# button); the decoding ends when nothing changes, or when it comes back to what it read before.
_DECODE_ROUNDS = 256
# Inference rounds at most: each one can only show settings nested one level deeper (a
# channel first, then the settings inside it), or take one setting from the memory.
_INFER_ROUNDS = 16
# Values tried for the memory per round at most, and values tried together with the
# settings they show.
_MEMORY_TRIES = 24
_LOOK_AHEAD_TRIES = 12
# Corrections of the result at most, the values tried for one (each with the whole inference
# behind it), and how far up the selectors are followed from a byte or group object that differs.
_REFINE_ROUNDS = 4
_REFINE_TRIES = 12
_REFINE_DEPTH = 3
_PAIR_TRIES = 64
_PAIR_ROUNDS = 2
# Values that fit worse whose own corrections are searched when no value fits better (each the
# start of a search of its own).
_DEEP_STARTS = 2
# The corrections a deeper search makes from its start at most.
_DEEP_ROUNDS = 1
# A linked group object a result does not show weighs as many differing bytes.
_OBJECT_WEIGHT = 3
# As many group objects a result shows that are not linked weigh one differing byte.
_UNLINKED_PER_BYTE = 4
# How many times the corrections, the values tried for one and the deeper starts each effort
# allows (``None``: no limit).
_EFFORT_SCALE: dict[RecoveryEffort, int | None] = {
    RecoveryEffort.FAST: 1,
    RecoveryEffort.MEDIUM: 4,
    RecoveryEffort.MAXIMUM: 16,
    RecoveryEffort.UNLIMITED: None,
}

_ValueMap = dict[str, str]


class _Cancelled(Exception):
    """The search was cancelled (see ``recover_parameters``): it ends with what it has.
    ``partial`` carries the values decoded so far (the values, those left unknown and the
    inferred settings), when there are any: decoding again after a cancel can take as long as
    the search did."""

    partial: tuple[dict[str, str], list[str], dict[str, str]] | None = None


def _stopped() -> bool:
    """A cancel that has been given already."""
    return True


def _check(cancel: Callable[[], bool] | None) -> None:
    """Raise :class:`_Cancelled` when ``cancel`` says so."""
    if cancel is not None and cancel():
        raise _Cancelled


# A byte of a code segment: (segment id, offset).
_Cell = tuple[str, int]
# How far a result is from the device: the differing bytes, linked objects it does not show
# (each weighing _OBJECT_WEIGHT bytes) and objects it shows that are not linked (each weighing
# 1 / _UNLINKED_PER_BYTE byte), counted in such fractions; then only what contradicts the
# device, the bytes and the linked objects.
_Score = tuple[int, int]
# A state of the correction step: how far it is from the device (the score, the differing bytes,
# the group objects shown otherwise), the values, those left unknown, the inferred settings and
# the UI seeded with them.
_State = tuple[
    tuple[_Score, set[_Cell], set[int]],
    dict[str, str],
    list[str],
    "_ValueMap",
    "DynamicUI",
]


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
    effort: RecoveryEffort = RecoveryEffort.MEDIUM,
    cancel: Callable[[], bool] | None = None,
) -> RecoveredParameters:
    """Recover parameter reference values from a device's memory and properties.

    ``parameter_segments`` is ``{segment_id: bytes}`` and ``property_values`` is
    ``{(object_index, property_id, occurrence): bytes}``, both as read off the
    device (see :mod:`xknxeditor.recover.read_config`). ``project_code_page`` is the
    ``CodePage`` of the project the device goes into (``None``: the system language),
    which some applications encode their text parameters with. ``linked_objects`` are
    the numbers of the group objects the device's tables link. ``object_sizes`` holds sizes
    read from the table, including unlinked objects. A shown object of another size
    contradicts the device; an unlinked table entry alone does not require it to be shown.
    Settings the device keeps no copy of
    take the values that show these objects, or that make the memory fit the union
    members they choose (see :func:`_infer_from_memory`), corrected with as many values as
    ``effort`` allows (see :func:`_refine`), until ``cancel`` returns true. Returns the recovered value for every
    parameter reference whose type is reconstructable, the parameter ids whose
    value is unknown (lossy types / absent data), and how far the result still differs from
    the device.
    """
    if cancel is not None:
        requested = cancel
        stopped = False

        def latched_cancel() -> bool:
            nonlocal stopped
            stopped = stopped or requested()
            return stopped

        cancel = latched_cancel

    ui = application.dynamic_ui()
    if ui is None:
        return RecoveredParameters(values={}, unknown=[])
    linked = set(linked_objects)
    sizes = dict(object_sizes or {})
    fixed = _fixed_values(application, ui)
    dead: set[tuple[str, str]] = set()
    try:
        values, unknown, inferred = _infer_all(
            application,
            parameter_segments,
            property_values,
            project_code_page,
            linked,
            sizes,
            fixed,
            {},
            dead,
            cancel,
        )
    except _Cancelled as cancelled:
        # Cancelled while inferring: the values so far (else, cancelled at once, the values
        # as read), measured against the device by a correction step that stops at once.
        if cancelled.partial is not None:
            values, unknown, inferred = cancelled.partial
        else:
            values, unknown = _decode(
                application,
                parameter_segments,
                property_values,
                project_code_page,
                {},
                fixed=fixed,
            )
            inferred = {}
        cancel = _stopped

    result = _refine(
        application,
        parameter_segments,
        property_values,
        project_code_page,
        linked,
        sizes,
        fixed,
        values,
        unknown,
        inferred,
        dead,
        _EFFORT_SCALE[effort],
        cancel,
    )
    if effort is not RecoveryEffort.FAST and not result.complete:
        result = _refine_pairs(
            application,
            parameter_segments,
            property_values,
            project_code_page,
            linked,
            sizes,
            result,
            _EFFORT_SCALE[effort],
            cancel,
            fixed={},
        )
    return _refine_fixed(
        application,
        parameter_segments,
        property_values,
        project_code_page,
        linked,
        sizes,
        result,
        fixed,
        _EFFORT_SCALE[effort],
        cancel,
    )


def _infer_all(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None,
    project_code_page: str | None,
    linked: set[int],
    sizes: Mapping[int, str],
    fixed: _ValueMap,
    start: Mapping[str, str],
    dead: set[tuple[str, str]],
    cancel: Callable[[], bool] | None = None,
) -> tuple[dict[str, str], list[str], _ValueMap]:
    """The values decoded with the settings the device keeps no copy of inferred, starting with
    those of ``start`` (kept as they are): the values, the parameters left unknown and all the
    inferred settings. ``dead`` collects the values found to show nothing (see
    :func:`_infer_unstored`), and those in it are not tried. Raises :class:`_Cancelled` once
    ``cancel`` says so."""
    inferred: _ValueMap = dict(start)
    values, unknown = _decode(
        application,
        parameter_segments,
        property_values,
        project_code_page,
        inferred,
        cancel,
        fixed={},
    )
    try:
        return _infer_rounds(
            application,
            parameter_segments,
            property_values,
            project_code_page,
            linked,
            sizes,
            fixed,
            dead,
            cancel,
            values,
            unknown,
            inferred,
        )
    except _Cancelled as cancelled:
        if cancelled.partial is None:
            cancelled.partial = (values, unknown, dict(inferred))
        raise


def _infer_rounds(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None,
    project_code_page: str | None,
    linked: set[int],
    sizes: Mapping[int, str],
    fixed: _ValueMap,
    dead: set[tuple[str, str]],
    cancel: Callable[[], bool] | None,
    values: dict[str, str],
    unknown: list[str],
    inferred: _ValueMap,
) -> tuple[dict[str, str], list[str], _ValueMap]:
    """The inference rounds of :func:`_infer_all` from the values decoded first."""
    for _ in range(_INFER_ROUNDS):
        _check(cancel)
        chosen = (
            _infer_unstored(
                application,
                values,
                linked,
                sizes,
                project_code_page,
                dead=dead,
                fixed=fixed,
                segments=parameter_segments,
                cancel=cancel,
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
            cancel,
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
            cancel,
            fixed={},
        )
    return values, unknown, inferred


def _written_cells(ui: DynamicUI) -> set[_Cell]:
    """The bytes the active parameters of ``ui`` write, those of module instances too."""
    return {
        (segment_id, offset)
        for segment_id, cells in ui.memory_param_map().items()
        for offset in cells
    }


def _differing_cells(
    ui: DynamicUI, parameter_segments: Mapping[str, bytes]
) -> set[_Cell] | None:
    """The bytes ``ui`` writes otherwise than the device holds them; ``None`` when it cannot be
    encoded."""
    from xknxeditor.prod.errors import EncodingError

    try:
        encoded = ui.encode_to_memory()
    except (EncodingError, ValueError):
        return None
    return {
        (segment_id, offset)
        for segment_id, device in parameter_segments.items()
        for offset, (mine, theirs) in enumerate(
            zip(encoded.get(segment_id, b""), device, strict=False)
        )
        if mine != theirs
    }


def _gates_of(
    ui: DynamicUI,
    ref_id: str,
    gates_of: Callable[[str], frozenset[str]] | None = None,
) -> set[str]:
    """The selectors right on the way to the placements of the param-ref ``ref_id`` (with
    ``gates_of`` of another reference, e.g. a group object's), qualified like it: those of a
    module instance's reference with that instance. One shown wherever its instance is has
    the selectors on the way to the instance's module."""
    local = ui.local_ref_id(ref_id)
    gates = (gates_of or ui.parameter_gates)(local)
    instance = _instance_of(ref_id, local)
    if instance is not None and not gates and "_MI-" in instance[0]:
        return set(ui.module_gates(instance[0][: instance[0].rindex("_MI-")]))
    return _qualified(gates, instance)


def _calculation_inputs(ui: DynamicUI, ref_id: str) -> set[str]:
    """The param-refs a parameter calculation computes ``ref_id`` from, qualified like it: what
    decides a selector the application calculates."""
    local = ui.local_ref_id(ref_id)
    indexer = ui.indexer
    known: dict[str, set[str]] | None = next(
        (cache for seen, cache in _CALCULATION_INPUTS if seen is indexer), None
    )
    if known is None:
        known = {}
        _CALCULATION_INPUTS.insert(0, (indexer, known))
        del _CALCULATION_INPUTS[4:]
    inputs = known.get(local)
    if inputs is None:
        inputs = set[str]()
        for calculation in indexer.calculations_for_r(local):
            inputs |= {ref.ref_id for ref in calculation.lparameters.parameter_ref_ref}
        for calculation in indexer.calculations_for_l(local):
            inputs |= {ref.ref_id for ref in calculation.rparameters.parameter_ref_ref}
        inputs.discard(local)
        known[local] = inputs
    return _qualified(inputs, _instance_of(ref_id, local))


# The calculation inputs of each local param-ref, per indexer (an application's, shared by its
# UIs) of the last few applications searched.
_CALCULATION_INPUTS: list[tuple[object, dict[str, set[str]]]] = []


def _instance_of(ref_id: str, local: str) -> tuple[str, str] | None:
    """For the param-ref ``ref_id`` of a module instance (``local`` its definition's), the
    instance's part of the id and the definition's; None for one of the application."""
    if local == ref_id or "_MD-" not in local:
        return None
    definition = local[: local.index("_", local.rindex("_MD-") + 1)]
    return ref_id[: len(ref_id) - (len(local) - len(definition))], definition


def _qualified(refs: Collection[str], instance: tuple[str, str] | None) -> set[str]:
    """``refs`` (local to a module definition or the application) as the references of the
    module instance ``instance`` (see :func:`_instance_of`): the definition's part of each id
    swapped for the instance's."""
    if instance is None:
        return set(refs)
    part, definition = instance
    return {
        part + ref[len(definition) :] if ref.startswith(definition + "_") else ref
        for ref in refs
    }


def _objects_by_number(ui: DynamicUI) -> dict[int, list[str]]:
    """The group object references of each number: of the application, and of the module
    instances ``ui`` makes, qualified with the instance and numbered from its base."""
    indexer = ui.indexer
    numbered: list[tuple[str, int, str | None]] = []
    for ref_id, ref in indexer.com_object_refs.items():
        com_object = indexer.com_objects.get(ref.ref_id)
        number = getattr(com_object, "number", None)
        if isinstance(number, int):
            numbered.append((ref_id, number, getattr(com_object, "base_number", None)))
    definitions = tuple(f"{definition}_" for definition in indexer.module_defs)
    result: dict[int, list[str]] = {}
    for ref_id, number, _base in numbered:
        if not ref_id.startswith(definitions):
            result.setdefault(number, []).append(ref_id)
    arguments = ui.get_module_instance_arguments()
    for instance, definition in ui.get_module_instances():
        # Arguments are stored relative to the application, the base of an object in full.
        application_id = definition[: definition.rindex("_MD-")]
        values = {
            f"{application_id}_{argument}": value
            for argument, value in arguments.get(instance, [])
        }
        for ref_id, number, base in numbered:
            if not ref_id.startswith(f"{definition}_"):
                continue
            try:
                offset = int(values.get(base, "0")) if base else 0
            except ValueError:
                continue
            result.setdefault(number + offset, []).append(
                instance + ref_id[len(definition) :]
            )
    return result


def _count_selectors(
    ui: DynamicUI,
    gates: Collection[str],
    shown: Collection[str],
    weight: Counter[str],
) -> None:
    """Count the selectors ``gates`` in ``weight``, and for one not ``shown`` those on its own way,
    ``_REFINE_DEPTH`` selectors up; for one a parameter calculation sets, the parameters it is
    calculated from (shown or not: the selector follows them)."""
    frontier = set(gates)
    for _depth in range(_REFINE_DEPTH):
        above: set[str] = set()
        for gate in frontier:
            weight[gate] += 1
            if gate not in shown:
                above |= _gates_of(ui, gate)
            above |= _calculation_inputs(ui, gate)
        frontier = above


def _refine(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None,
    project_code_page: str | None,
    linked: set[int],
    sizes: Mapping[int, str],
    fixed: _ValueMap,
    values: dict[str, str],
    unknown: list[str],
    inferred: _ValueMap,
    dead: set[tuple[str, str]],
    scale: int | None,
    cancel: Callable[[], bool] | None = None,
) -> RecoveredParameters:
    """Correct settings the device keeps no copy of where the result still differs from the
    device: in the bytes it would write, or in the group objects it shows (with linked objects).
    Returns the result with how far it still differs.

    A setting deep in a channel only shows once the selector of the channel has the right value,
    and that value alone fits worse until the channel's own settings are inferred too. So each
    value tried is followed by the whole inference behind it, and taken when the result fits
    better (each linked group object it does not show weighing ``_OBJECT_WEIGHT`` differing
    bytes, ``_UNLINKED_PER_BYTE`` it shows that are not linked one; the search ends once no byte
    differs and every linked object shows). The
    values tried are those of the selectors on the way to the parameters stored at
    the differing bytes (of module instances too) and to the group objects shown otherwise
    (``_REFINE_DEPTH`` selectors up),
    those of the most such places first. In each module instance, and in the application's
    own settings, the first that fits better is taken; ``_REFINE_TRIES`` values per round,
    ``_REFINE_ROUNDS`` rounds at most, both ``scale`` times (``None``: until no value fits better).
    When no value fits better, the corrections behind the values that fit worse are searched,
    those that fit best first (``_DEEP_STARTS`` of them, ``scale`` times): a channel selector whose
    right value fits worse until the channel's own selectors are corrected as well. One that ends
    better than the result is taken, and the search goes on from there.
    The values the inference found to show nothing (``dead``) are not tried again behind them.
    When ``cancel`` returns true the search stops with the result so far."""
    from .recover import seed_dynamic_ui

    ui = seed_dynamic_ui(application, values, project_code_page)
    if ui is None:
        return RecoveredParameters(values=values, unknown=unknown)

    def misfit(seeded: DynamicUI) -> tuple[_Score, set[_Cell], set[int]] | None:
        return _misfit(seeded, parameter_segments, linked, sizes)

    def finish(
        fit: tuple[_Score, set[_Cell], set[int]] | None,
        seeded: DynamicUI,
        values: dict[str, str],
        unknown: list[str],
        *,
        limited: bool,
    ) -> RecoveredParameters:
        """The result, with the bytes it still writes otherwise counted only where a parameter
        is stored (a device can keep other data in its segments, which changes at run time),
        and objects that contradict the links or sizes read from the device."""
        if fit is not None and fit[1]:
            stored = seeded.stored_parameter_cells().keys() | _written_cells(seeded)
            fit = (fit[0], fit[1] & stored, fit[2])
        if fit is not None:
            fit = (fit[0], fit[1], _contradicting_objects(seeded, linked, sizes))
        return _with_misfit(values, unknown, fit, seeded, limited=limited)

    first = misfit(ui)
    if first is None or not first[0][1]:
        return finish(first, ui, values, unknown, limited=False)
    tries = None if scale is None else _REFINE_TRIES * scale
    rounds = None if scale is None else _REFINE_ROUNDS * scale
    starts = None if scale is None else _DEEP_STARTS * scale
    deep_rounds = None if scale is None else _DEEP_ROUNDS * scale

    def descend(
        state: _State,
        worse: list[tuple[_Score, str, _State]] | None,
        locked: str = "",
        rounds: int | None = rounds,
    ) -> tuple[_State, bool, bool]:
        """The corrections from ``state`` on, each taken when it fits better, until none does:
        the state reached, whether the effort's limit stopped it and whether ``cancel`` did. The
        values that fit worse go to ``worse`` (with their selector and the state they lead to),
        the starts of a deeper search; the selector ``locked`` keeps its value. ``rounds``
        bounds the rounds of corrections."""
        current, values, unknown, inferred, ui = state
        tried: set[tuple[str, str]] = set()
        limited = False
        for _ in count() if rounds is None else range(rounds):
            score, differing, wrong = current
            try:
                trials = [
                    trial
                    for trial in _refinement_trials(
                        ui,
                        differing,
                        wrong,
                        cancel,
                        hidden=_hidden_fixed_values(application.program, ui),
                    )
                    if trial[0] != locked and trial not in tried
                ]
            except _Cancelled:
                return (current, values, unknown, inferred, ui), True, True
            # A module instance's settings are independent of another's: a correction is
            # taken in each (and one of the application's own), the values of the others are
            # tried behind it.
            corrected: set[str] = set()
            evaluated = 0
            limited = False
            for gate, value in trials:
                scope = _scope_of(gate)
                if scope in corrected:
                    continue
                if evaluated == tries:
                    limited = True
                    break
                try:
                    _check(cancel)
                    evaluated += 1
                    tried.add((gate, value))
                    trial_values, trial_unknown, trial_inferred = _infer_all(
                        application,
                        parameter_segments,
                        property_values,
                        project_code_page,
                        linked,
                        sizes,
                        fixed,
                        {**inferred, gate: value},
                        set(dead),
                        cancel,
                    )
                except _Cancelled:
                    return (current, values, unknown, inferred, ui), True, True
                except AllocatorOverflow:
                    # A value that lays out more than the product has room for (more module
                    # instances than an allocator holds): no configuration of the device.
                    continue
                seeded = seed_dynamic_ui(application, trial_values, project_code_page)
                if seeded is None:
                    continue
                try:
                    result = misfit(seeded)
                except AllocatorOverflow:
                    continue
                if result is None:
                    continue
                trial = (result, trial_values, trial_unknown, trial_inferred, seeded)
                if result[0] < score:
                    current, values, unknown, inferred, ui = trial
                    score = result[0]
                    corrected.add(scope)
                    if not score[1]:
                        break
                elif worse is not None:
                    worse.append((result[0], gate, trial))
            if not corrected or not current[0][1]:
                break
        else:
            # Every correction was taken, and the result still differs.
            limited = True
        return (current, values, unknown, inferred, ui), limited, False

    state: _State = (first, values, unknown, inferred, ui)
    while True:
        worse: list[tuple[_Score, str, _State]] = []
        state, limited, stopped = descend(state, worse)
        if stopped or not state[0][0][1]:
            break
        # A selector whose value alone fits worse, as a channel that only fits once its own
        # settings are corrected too: the corrections behind the values that fit worse (the
        # selector keeping its value), those that fit best first, taken when they end better
        # than the result.
        worse.sort(key=lambda entry: entry[0])
        better = None
        # A selector a calculation computes from the values read is not guessed.
        worse = [
            entry for entry in worse if not _calculation_inputs(entry[2][4], entry[1])
        ]
        for n, (_score, gate, start) in enumerate(worse):
            if starts is not None and n == starts:
                limited = True
                break
            deep, _deep_limited, stopped = descend(start, None, gate, deep_rounds)
            if deep[0][0] < state[0][0]:
                better = deep
            if better is not None or stopped:
                break
        if better is not None:
            state = better
        if better is None or stopped:
            limited = limited or stopped
            break
    return finish(state[0], state[4], state[1], state[2], limited=limited)


def _misfit(
    seeded: DynamicUI,
    parameter_segments: Mapping[str, bytes],
    linked: set[int],
    sizes: Mapping[int, str],
) -> tuple[_Score, set[_Cell], set[int]] | None:
    differing = _differing_cells(seeded, parameter_segments)
    if differing is None:
        return None
    if differing:
        differing &= seeded.stored_parameter_cells().keys() | _written_cells(seeded)
    objects: set[int] = _shown_objects(seeded) if linked or sizes else set()
    matching = _shown_objects(seeded, sizes) if sizes else objects
    conflicts = objects - matching
    wrong = (linked ^ objects) | conflicts if linked else conflicts
    # Missing linked objects and size conflicts contradict the device. Extra unlinked
    # objects carry a smaller penalty to avoid enabling channels for a few matching bytes.
    contradicted = len((linked - objects) | conflicts)
    contradiction = _OBJECT_WEIGHT * contradicted + len(differing)
    unlinked = len(objects - linked) if linked else 0
    cost = _UNLINKED_PER_BYTE * contradiction + unlinked
    return (cost, contradiction), differing, wrong


def _refinement_trials(
    ui: DynamicUI,
    differing: set[_Cell],
    wrong: set[int],
    cancel: Callable[[], bool] | None = None,
    *,
    hidden: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Candidate selectors, ordered by how many mismatching cells and objects they gate; with
    ``hidden`` (local param-ref -> fixed value, see :func:`_hidden_fixed_values`) a hidden
    selector's fixed value too. Raises :class:`_Cancelled` once ``cancel`` says so."""
    from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try

    shown = shown_parameters(ui.ui())
    weight: Counter[str] = Counter()
    cells = ui.stored_parameter_cells()
    for cell in sorted(differing):
        _check(cancel)
        for ref_id in sorted(cells.get(cell, ())):
            _count_selectors(ui, _gates_of(ui, ref_id), shown, weight)
    objects_of = _objects_by_number(ui) if wrong else {}
    for number in sorted(wrong):
        _check(cancel)
        for ref_id in objects_of.get(number, ()):
            _count_selectors(ui, _gates_of(ui, ref_id, ui.object_gates), shown, weight)
    trials: list[tuple[str, str]] = []
    for gate, _count in weight.most_common():
        parameter = shown.get(gate)
        if parameter is None:
            # A hidden setting nobody edits keeps the value the product gives it, even where
            # the memory holds another's (a later member of its union).
            value = hidden.get(ui.local_ref_id(gate)) if hidden else None
            if value is not None and ui.get_value(gate) != value:
                trials.append((gate, value))
            continue
        if _is_unstored(ui, gate):
            now = ui.get_value(gate) or parameter.default_value
            trials.extend((gate, value) for value in values_to_try(parameter, now))
    return trials


def _refine_pairs(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None,
    project_code_page: str | None,
    linked: set[int],
    sizes: Mapping[int, str],
    recovered: RecoveredParameters,
    scale: int | None,
    cancel: Callable[[], bool] | None = None,
    *,
    fixed: Mapping[str, str] | None = None,
) -> RecoveredParameters:
    """Try selectors alone and with a dependent selector after the ordinary search.

    Only unstored, non-calculated selectors are guessed. Stored values are decoded again
    for each layout. A pair can improve the result even when its first change alone does
    not. Each round accepts the first improvement; attempts and rounds scale with the
    effort, and cancellation keeps the last accepted result.
    """
    from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try

    from .recover import seed_dynamic_ui

    ui = seed_dynamic_ui(application, recovered.values, project_code_page)
    if ui is None:
        return recovered
    current = _misfit(ui, parameter_segments, linked, sizes)
    if current is None:
        return recovered
    values, unknown = recovered.values, recovered.unknown
    if fixed is None:
        fixed = _fixed_values(application, ui)
    inferred = {key: value for key, value in values.items() if _is_unstored(ui, key)}
    limited = recovered.limited
    budget = None if scale is None else _PAIR_TRIES * scale

    def finish() -> RecoveredParameters:
        assert current is not None
        assert ui is not None
        stored = ui.stored_parameter_cells().keys() | _written_cells(ui)
        fit = (
            current[0],
            current[1] & stored,
            _contradicting_objects(ui, linked, sizes),
        )
        return _with_misfit(values, unknown, fit, ui, limited=limited)

    rounds = count() if scale is None else range(_PAIR_ROUNDS * scale)
    try:
        for _ in rounds:
            _check(cancel)
            evaluated = 0
            improved = False
            for gate, value in _refinement_trials(
                ui, current[1], _contradicting_objects(ui, linked, sizes)
            ):
                if _calculation_inputs(ui, gate):
                    continue
                _check(cancel)
                if evaluated == budget:
                    limited = True
                    return finish()
                evaluated += 1
                chosen = {**inferred, gate: value}
                first_values, first_unknown = _decode(
                    application,
                    parameter_segments,
                    property_values,
                    project_code_page,
                    chosen,
                    fixed=fixed,
                )
                first = seed_dynamic_ui(application, first_values, project_code_page)
                if first is None:
                    continue
                fit = _misfit(first, parameter_segments, linked, sizes)
                if fit is not None and fit[0] < current[0]:
                    current, ui, values, unknown, inferred = (
                        fit,
                        first,
                        first_values,
                        first_unknown,
                        chosen,
                    )
                    improved = True
                    break
                for child, parameter in shown_parameters(first.ui()).items():
                    if (
                        child == gate
                        or not _is_unstored(first, child)
                        or gate not in _gates_of(first, child)
                        or _calculation_inputs(first, child)
                    ):
                        continue
                    now = first.get_value(child) or parameter.default_value
                    for child_value in values_to_try(parameter, now):
                        _check(cancel)
                        if evaluated == budget:
                            limited = True
                            return finish()
                        evaluated += 1
                        chosen = {**inferred, gate: value, child: child_value}
                        trial_values, trial_unknown = _decode(
                            application,
                            parameter_segments,
                            property_values,
                            project_code_page,
                            chosen,
                            fixed=fixed,
                        )
                        seeded = seed_dynamic_ui(
                            application, trial_values, project_code_page
                        )
                        if seeded is None:
                            continue
                        fit = _misfit(seeded, parameter_segments, linked, sizes)
                        if fit is not None and fit[0] < current[0]:
                            current, ui, values, unknown, inferred = (
                                fit,
                                seeded,
                                trial_values,
                                trial_unknown,
                                chosen,
                            )
                            improved = True
                            break
                    if improved:
                        break
                if improved:
                    break
            if not improved or finish().complete:
                return finish()
        limited = True
    except _Cancelled:
        limited = True
    return finish()


def _refine_fixed(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None,
    project_code_page: str | None,
    linked: set[int],
    sizes: Mapping[int, str],
    recovered: RecoveredParameters,
    fixed: _ValueMap,
    scale: int | None,
    cancel: Callable[[], bool] | None,
) -> RecoveredParameters:
    """Validate a candidate without overwriting the application's immutable references.

    The initial search can decode shared fields into all their references. Only its
    unstored choices seed this pass: immutable markers keep their branch values, and
    their conflicting cells direct local corrections before the result is returned.
    """
    from .errors import RecoverError
    from .recover import seed_dynamic_ui

    if not fixed:
        return recovered
    ui = seed_dynamic_ui(application, recovered.values, project_code_page)
    if ui is None:
        return recovered
    inferred = {
        key: value for key, value in recovered.values.items() if _is_unstored(ui, key)
    }
    values, unknown = _decode(
        application,
        parameter_segments,
        property_values,
        project_code_page,
        inferred,
        fixed=fixed,
    )
    ui = seed_dynamic_ui(application, values, project_code_page)
    assert ui is not None
    current = _misfit(ui, parameter_segments, linked, sizes)
    if current is None:
        raise RecoverError("Cannot validate recovered parameters with fixed values")
    limited = recovered.limited
    budget = None if scale is None else _PAIR_TRIES * scale
    rounds = count() if scale is None else range(_PAIR_ROUNDS * scale)
    try:
        for _ in rounds:
            _check(cancel)
            if not current[0][1]:
                break
            differing = current[1] & _fixed_cells(ui, fixed)
            improved = False
            evaluated = 0
            for gate, value in _refinement_trials(ui, differing, set()):
                if _calculation_inputs(ui, gate):
                    continue
                _check(cancel)
                if evaluated == budget:
                    limited = True
                    break
                evaluated += 1
                chosen = {**inferred, gate: value}
                trial_values, trial_unknown = _decode(
                    application,
                    parameter_segments,
                    property_values,
                    project_code_page,
                    chosen,
                    fixed=fixed,
                )
                seeded = seed_dynamic_ui(application, trial_values, project_code_page)
                assert seeded is not None
                fit = _misfit(seeded, parameter_segments, linked, sizes)
                if fit is not None and fit[0] < current[0]:
                    values, unknown, inferred, ui, current = (
                        trial_values,
                        trial_unknown,
                        chosen,
                        seeded,
                        fit,
                    )
                    improved = True
            if not improved:
                break
        else:
            limited = True
    except _Cancelled:
        limited = True
    current = (current[0], current[1], _contradicting_objects(ui, linked, sizes))
    return _with_misfit(values, unknown, current, ui, limited=limited)


def _scope_of(ref_id: str) -> str:
    """The module instance a (qualified) param-ref belongs to, empty for the application's."""
    if "_MI-" not in ref_id:
        return ""
    end = ref_id.find("_", ref_id.rindex("_MI-") + 1)
    return ref_id if end < 0 else ref_id[:end]


def _with_misfit(
    values: dict[str, str],
    unknown: list[str],
    misfit: tuple[_Score, set[_Cell], set[int]] | None,
    seeded: DynamicUI,
    *,
    limited: bool,
) -> RecoveredParameters:
    """The result with how far it differs from the device (``misfit``: its score, the bytes it
    writes otherwise and the group objects it shows otherwise; ``None`` when it cannot be
    encoded, which tells nothing), and which of the unknown parameters ``seeded`` has a lossy
    type."""
    lossy = _lossy(seeded, unknown)
    if misfit is None:
        return RecoveredParameters(values=values, unknown=unknown, lossy=lossy)
    _score, differing, wrong = misfit
    return RecoveredParameters(
        values=values,
        unknown=unknown,
        lossy=lossy,
        differing_bytes=len(differing),
        differing_objects=len(wrong),
        limited=limited and bool(differing or wrong),
    )


def _lossy(ui: DynamicUI, unknown: Collection[str]) -> list[str]:
    """The parameters of ``unknown`` (top-level ones by id, those of module instances by their
    qualified param-ref) whose type cannot give back a value from bytes at all."""
    from xknxeditor.prod.parser_v2.encode import decodable_type, type_size_in_bit

    if not unknown:
        return []
    indexer = ui.indexer
    lossy: list[str] = []
    for key in unknown:
        parameter = indexer.parameters.get(key)
        if parameter is None:
            ref = indexer.parameter_refs.get(ui.local_ref_id(key))
            parameter = indexer.parameters.get(ref.ref_id) if ref is not None else None
        type_id = getattr(parameter, "parameter_type", None)
        parameter_type = indexer.parameter_types.get(type_id) if type_id else None
        if parameter_type is None or not decodable_type(
            parameter_type.choice, type_size_in_bit(parameter_type.choice) or 0
        ):
            lossy.append(key)
    return lossy


def _decode(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[tuple[int | None, int, int], bytes] | None,
    project_code_page: str | None,
    inferred: Mapping[str, str],
    cancel: Callable[[], bool] | None = None,
    *,
    fixed: Mapping[str, str] | None = None,
) -> tuple[_ValueMap, list[str]]:
    """Decode the memory and properties against a UI seeded with ``inferred``, returning the
    values (``inferred`` included) and the parameters left unknown. Raises :class:`_Cancelled`
    between rounds once ``cancel`` says so."""
    from .recover import seed_dynamic_ui

    ui = (
        seed_dynamic_ui(application, inferred, project_code_page)
        if inferred
        else application.dynamic_ui()
    )
    assert ui is not None
    ui.project_code_page = project_code_page
    if fixed is None:
        fixed = _fixed_values(application, ui)
    if fixed:
        clean = {
            key: value
            for key, value in inferred.items()
            if ui.local_ref_id(key) not in fixed
        }
        if clean != inferred:
            inferred = clean
            ui = seed_dynamic_ui(application, inferred, project_code_page)
            assert ui is not None
    parameter_to_refs = _parameter_to_refs(application)
    values: _ValueMap = {}
    unknown: list[str] = []
    seen: set[frozenset[tuple[str, str]]] = set()

    def calculated(decoded: Mapping[str, str]) -> _ValueMap:
        """``decoded`` with what the calculations compute from it; cancelled, with the
        values decoded so far."""
        try:
            return _with_calculated(application, decoded, project_code_page, cancel)
        except _Cancelled as cancelled:
            if cancelled.partial is None:
                cancelled.partial = ({**inferred, **values}, unknown, dict(inferred))
            raise

    # Which parameters own a memory field depends on the values (the active alternative of
    # a union): decode again with a UI seeded with what was read until nothing changes.
    for n in range(_DECODE_ROUNDS):
        try:
            _check(cancel)
        except _Cancelled as cancelled:
            if n:
                cancelled.partial = ({**inferred, **values}, unknown, dict(inferred))
            raise
        decoded = dict(ui.decode_memory_parameters(parameter_segments))
        if property_values:
            # Memory and property parameters are disjoint, so a plain merge is safe.
            decoded.update(ui.decode_property_parameters(property_values))
        read: _ValueMap = {}
        unknown = []
        # The value the memory holds is not that of a ref a hidden ref with a fixed value of
        # the same parameter is written after (see _shadowed), nor that of an inferred one.
        for parameter_id, value in decoded.items():
            if value is None:
                unknown.append(parameter_id)
                continue
            for ref_id in parameter_to_refs.get(parameter_id, []):
                if (
                    ref_id not in fixed
                    and ref_id not in inferred
                    and not _shadowed(ui, ref_id)
                ):
                    read[ref_id] = value
        written_over = _written_over_values(
            application,
            ui,
            parameter_segments,
            {**inferred, **read},
            unknown,
            project_code_page,
        )
        if written_over:
            read.update(written_over)
            resolved = {ui.indexer.parameter_refs[ref].ref_id for ref in written_over}
            unknown = [parameter for parameter in unknown if parameter not in resolved]
        read.update(_assigned_from(ui, read, inferred))
        if read == values:
            break
        state = frozenset(read.items())
        if state in seen:
            # Values that keep alternating (two alternatives each showing the other): the
            # last read, as with a round limit.
            values = read
            break
        seen.add(state)
        values = read
        # With what the calculations make of them too: a parameter shown behind a calculated
        # switch is decoded once the switch has its value.
        seeded_ui = seed_dynamic_ui(
            application, calculated({**inferred, **values}), project_code_page
        )
        if seeded_ui is None:
            break
        ui = seeded_ui

    # Module-instance parameters: decode against a UI seeded with the just-recovered
    # top-level values so the module instances match the device. The module decoder
    # already returns instance-qualified reference ids, which is what a project
    # stores, so they merge straight into values.
    values = calculated({**inferred, **values})
    seeded = seed_dynamic_ui(application, values, project_code_page)
    if seeded is not None:
        for ref_id, value in seeded.decode_module_parameters(
            parameter_segments, property_values or {}
        ).items():
            if seeded.local_ref_id(ref_id) in fixed:
                continue
            if value is None:
                unknown.append(ref_id)
            else:
                values[ref_id] = value
    return calculated(values), unknown


class _CancelToken(AbortToken):
    """The abort of the calculations the search runs, given once ``cancel`` says so: a
    calculation stops at its next script call (the calculations of a large application
    can take longer than the rest of a search)."""

    def __init__(self, cancel: Callable[[], bool]) -> None:
        super().__init__()
        self._cancel = cancel

    @property
    def requested(self) -> bool:
        return super().requested or self._cancel()


def _with_calculated(
    application: Application,
    values: Mapping[str, str],
    project_code_page: str | None,
    cancel: Callable[[], bool] | None = None,
) -> _ValueMap:
    """``values`` plus what the application's calculations compute from them (a shown setting
    the device keeps no copy of, computed from a stored one). A read value is kept. Raises
    :class:`_Cancelled` once ``cancel`` says so."""
    from .recover import seed_dynamic_ui

    result = dict(values)
    ui = seed_dynamic_ui(application, values, project_code_page)
    if ui is None:
        return result
    if cancel is not None:
        ui.script_abort = _CancelToken(cancel)
    try:
        changes = ui.recalculate(list(values), stored=True)
    except ScriptAborted:
        raise _Cancelled from None
    for ref_id, (_old, new) in changes.items():
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


def _contradicting_objects(
    ui: DynamicUI, linked: set[int], sizes: Mapping[int, str]
) -> set[int]:
    """Missing linked objects and shown objects whose size contradicts the device."""
    if not linked and not sizes:
        return set()
    shown = _shown_objects(ui)
    matching = _shown_objects(ui, sizes) if sizes else shown
    return (linked | shown) - matching


def _is_unstored(ui: DynamicUI, ref_id: str) -> bool:
    """Whether the device keeps no copy of a parameter: no memory, no property, not in a
    union; or of this ref of it, when another active ref sets its memory (see
    :func:`_shadowed`). One that a calculation computes from the values read is in the values
    already."""
    indexer = ui.indexer
    ref = indexer.parameter_refs.get(ui.local_ref_id(ref_id))
    parameter = indexer.parameters.get(ref.ref_id) if ref is not None else None
    return _keeps_no_copy(parameter) or _shadowed(ui, ref_id)


# The active refs of each parameter written after another active ref of it, per UI (the last
# few: a search asks again and again for the same UI).
_SHADOWED: list[tuple[DynamicUI, frozenset[str]]] = []


def _shadowed(ui: DynamicUI, ref_id: str) -> bool:
    """Whether an active ParameterRef of the application comes before an active hidden one with a
    fixed value (see :func:`_fixed_values`) of the same parameter in the order a download writes
    them (``parameter_ref_order``), or its union member before active ones of the union over
    all of its bits: those set the memory, so the device keeps no copy of this one's value,
    which can still decide what is shown."""
    for seen, shadowed in _SHADOWED:
        if seen is ui:
            return ref_id in shadowed
    from xknxeditor.prod.parser_v2.encode import parameter_ref_order

    indexer = ui.indexer
    by_parameter: dict[str, list[tuple[tuple[int, int], str]]] = {}
    for active in ui.active_parameter_ref_ids():
        ref = indexer.parameter_refs.get(active)
        if ref is not None and ref.ref_id:
            by_parameter.setdefault(ref.ref_id, []).append(
                (parameter_ref_order(active, ref.tag), active)
            )
    program = getattr(ui, "_app", None)
    hidden: frozenset[str] = (
        _hidden_fixed(program, ui) if program is not None else frozenset()
    )
    found: set[str] = set()
    for refs in by_parameter.values():
        ordered = [active for _order, active in sorted(refs)]
        for n, active in enumerate(ordered):
            if any(later in hidden for later in ordered[n + 1 :]):
                found.add(active)
    actives = ui.active_parameter_ref_ids()
    for refs in by_parameter.values():
        for _order, active in refs:
            # Bits no later member writes still hold this one's.
            later = ui.later_union_members(active) & actives
            if later and ui.union_member_covered(active, later):
                found.add(active)
    _SHADOWED.insert(0, (ui, frozenset(found)))
    del _SHADOWED[8:]
    return ref_id in found


def _assigned_from(
    ui: DynamicUI, read: Mapping[str, str], inferred: Mapping[str, str]
) -> _ValueMap:
    """The values of the settings the device keeps no copy of that an Assign of ``ui`` copies
    into stored settings read (a "virtual" setting shown in their place): the value read, for
    a source of the same type. Where the targets of a source were read with different values
    (one of them assigned in another branch on the device), the value most of them hold."""
    indexer = ui.indexer
    votes: dict[str, Counter[str]] = {}
    for target, source in ui.assigned_from().items():
        value = read.get(target)
        if value is None or source in read or source in inferred:
            continue
        target_ref = indexer.parameter_refs.get(target)
        source_ref = indexer.parameter_refs.get(source)
        if target_ref is None or source_ref is None:
            continue
        target_parameter = indexer.parameters.get(target_ref.ref_id)
        source_parameter = indexer.parameters.get(source_ref.ref_id)
        if _keeps_no_copy(source_parameter) and getattr(
            source_parameter, "parameter_type", None
        ) == getattr(target_parameter, "parameter_type", None):
            votes.setdefault(source, Counter())[value] += 1
    found: _ValueMap = {}
    for source, counted in votes.items():
        ranked = counted.most_common(2)
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            found[source] = ranked[0][0]
    return found


def _written_over_values(
    application: Application,
    ui: DynamicUI,
    segments: Mapping[str, bytes],
    values: Mapping[str, str],
    unknown: Collection[str],
    project_code_page: str | None,
) -> _ValueMap:
    """The values of the active shown union members that a later active member writes over
    in part (see :meth:`DynamicUI.later_union_members`), read as no value of theirs: the
    value whose bytes, written in ``ui`` with ``values``, are those of ``segments``."""
    if not unknown:
        return {}
    from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try

    from .recover import seed_dynamic_ui

    lost = set(unknown)
    actives = ui.active_parameter_ref_ids()
    shown: Mapping[str, UiParameter] | None = None
    cells: Mapping[_Cell, Collection[str]] = {}
    found: _ValueMap = {}
    for ref_id in actives:
        ref = ui.indexer.parameter_refs.get(ref_id)
        if ref is None or ref.ref_id not in lost:
            continue
        later = ui.later_union_members(ref_id) & actives
        if not later or ui.union_member_covered(ref_id, later):
            continue
        if shown is None:
            shown = shown_parameters(ui.ui())
            cells = ui.stored_parameter_cells()
        parameter = shown.get(ref_id)
        if parameter is None:
            continue
        own = [cell for cell, refs in cells.items() if ref_id in refs]
        for value in values_to_try(parameter, ""):
            seeded = seed_dynamic_ui(
                application, {**values, ref_id: value}, project_code_page
            )
            if seeded is None:
                continue
            memory = seeded.encode_to_memory()
            if all(
                memory.get(segment, b"")[offset : offset + 1]
                == segments.get(segment, b"")[offset : offset + 1]
                for segment, offset in own
            ):
                found[ref_id] = value
                break
    return found


def _keeps_no_copy(parameter: object) -> bool:
    """Whether a parameter has no memory, no property and is no member of a union."""
    from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_parameter import (
        ApplicationProgramStaticParametersParameter,
    )
    from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_parameter import (
        ModuleDefStaticParametersParameter,
    )

    return (
        isinstance(
            parameter,
            ApplicationProgramStaticParametersParameter
            | ModuleDefStaticParametersParameter,
        )
        and parameter.choice is None
    )


def _fixed_values(application: Application, ui: DynamicUI) -> _ValueMap:
    """The values the application fixes for settings the device stores but nobody edits
    (local param-ref -> value): a hidden setting whose references each carry their own value,
    which tells the device what a setting it keeps no copy of chose (e.g. the datapoint type
    of a status value, the kind of a button function). References an assign or a calculation
    writes are left out, their value is not the fixed one."""
    return _program_fixed_values(application.program, ui)


def _program_fixed_values(program: object, ui: DynamicUI) -> _ValueMap:
    """:func:`_fixed_values` of the application program ``program``."""
    from xknxeditor.namespaces.intermediate import Assign
    from xknxeditor.namespaces.intermediate.access_t import Access

    indexer = ui.indexer
    assigned: set[str] = set()
    stack: list[object] = [
        getattr(program, "dynamic", None),
        *(definition.dynamic for definition in indexer.module_defs.values()),
    ]
    while stack:
        element = stack.pop()
        if isinstance(element, Assign):
            assigned.add(element.target_param_ref_ref)
            continue
        stack.extend(getattr(element, "choice", None) or [])
        stack.extend(getattr(element, "when", None) or [])
    fixed: _ValueMap = {}
    for ref_id, ref in indexer.parameter_refs.items():
        parameter = indexer.parameters.get(ref.ref_id)
        if (
            parameter is None
            or (ref.access or parameter.access) is not Access.NONE
            or ref_id in assigned
            or indexer.calculations_for_l(ref_id)
            or indexer.calculations_for_r(ref_id)
            or _keeps_no_copy(parameter)
        ):
            continue
        value = indexer.default_value(ref_id)
        if value is not None:
            fixed[ref_id] = value
    return fixed


# The hidden settings with a fixed value of the last programs decoded (see _hidden_fixed).
_HIDDEN: list[tuple[object, frozenset[str], Mapping[str, str]]] = []


def _hidden_fixed(program: object, ui: DynamicUI) -> frozenset[str]:
    """The refs of the application program's hidden settings with a fixed value (see
    :func:`_fixed_values`): the product's data gives them, not the memory."""
    _hidden_fixed_values(program, ui)
    return _HIDDEN[0][1]


def _hidden_fixed_values(program: object, ui: DynamicUI) -> Mapping[str, str]:
    """:func:`_hidden_fixed` with the value of each (local param-ref -> value)."""
    for n, (seen, _refs, values) in enumerate(_HIDDEN):
        if seen is program:
            _HIDDEN.insert(0, _HIDDEN.pop(n))
            return values
    values = _program_fixed_values(program, ui)
    _HIDDEN.insert(0, (program, frozenset(values), values))
    del _HIDDEN[4:]
    return values


def _fixed_cells(ui: DynamicUI, fixed: _ValueMap) -> set[_Cell]:
    """The bytes the active hidden settings with a fixed value (see :func:`_fixed_values`) take
    in ``ui``."""
    indexer = ui.indexer
    active = {ui.local_ref_id(ref_id) for ref_id in ui.active_parameter_ref_ids()}
    parameters = {indexer.parameter_refs[ref].ref_id for ref in active & fixed.keys()}
    # A parameter another active reference of writes (an assign, a calculation) is left out.
    parameters -= {
        indexer.parameter_refs[ref].ref_id
        for ref in active - fixed.keys()
        if ref in indexer.parameter_refs
    }
    if not parameters:
        return set()
    return {
        (segment_id, offset)
        for segment_id, cells in ui.memory_param_map().items()
        for offset, (parameter_id, _value) in cells.items()
        if parameter_id in parameters
    }


def _mismatches(
    ui: DynamicUI, parameter_segments: Mapping[str, bytes], cells: set[_Cell]
) -> int:
    """How many of the bytes ``cells`` the device holds otherwise than ``ui`` writes them
    (compared as written, so that the members of a union in a module instance count too)."""
    from xknxeditor.prod.errors import EncodingError

    if not cells:
        return 0
    try:
        encoded = ui.encode_to_memory()
    except (EncodingError, ValueError):
        return 0
    count = 0
    for segment_id, offset in cells:
        device = parameter_segments.get(segment_id)
        mine = encoded.get(segment_id)
        if (
            device is not None
            and mine is not None
            and offset < min(len(device), len(mine))
            and device[offset] != mine[offset]
        ):
            count += 1
    return count


def _undecodable_cells(
    ui: DynamicUI, parameter_segments: Mapping[str, bytes], own: set[str]
) -> int:
    """How many bytes the parameters ``own`` take in ``ui`` whose memory is no value of their
    type: the device holds another setting there."""
    if not own:
        return 0
    decoded = ui.decode_memory_parameters(parameter_segments)
    failed = {
        parameter_id for parameter_id in own if decoded.get(parameter_id, "") is None
    }
    return sum(
        1
        for cells in ui.memory_param_map().values()
        for parameter_id, _value in cells.values()
        if parameter_id in failed
    )


def _fixed_clashes(
    uis: Mapping[str, DynamicUI],
    parameter_segments: Mapping[str, bytes],
    fixed: _ValueMap,
) -> dict[str, int]:
    """For configurations that show the same objects (a pair of buttons or two single ones, the
    datapoint types of a status value), how many bytes the device holds against each: of the
    hidden settings with a fixed value, the ones it holds otherwise than each writes them,
    compared on the bytes such settings take in any of them (a configuration that leaves the
    other one's bytes at their initial value counts those too, the device holds the other one's
    values there); and of the settings only some of them have, the ones whose memory is no
    value of the setting's type (e.g. a function selection the device holds another one's value
    in)."""
    cells: set[_Cell] = set()
    written: dict[str, set[str]] = {}
    for key, ui in uis.items():
        cells |= _fixed_cells(ui, fixed)
        written[key] = {
            parameter_id
            for column in ui.memory_param_map().values()
            for parameter_id, _value in column.values()
        }
    common: set[str] = set[str].intersection(*written.values()) if written else set()
    return {
        key: _mismatches(ui, parameter_segments, cells)
        + _undecodable_cells(ui, parameter_segments, written[key] - common)
        for key, ui in uis.items()
    }


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
    fixed: _ValueMap | None = None,
    segments: Mapping[str, bytes] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> _ValueMap:
    """Values for shown settings the device keeps no copy of, chosen to show more of the
    ``linked`` group objects (in their ``sizes``) than ``values`` alone.

    Every other value of each such setting (but those in ``known``) is tried; a setting takes
    the one that shows the most linked objects more (net of the ones it hides). Of several
    that show as many, it takes the one whose hidden settings with a ``fixed`` value hold that
    value in the memory ``segments`` read, then the one that shows the fewest objects that are
    not linked, then the first. Best first, each is taken that shows
    more together with those taken before it. When no value shows more, a value that shows
    other such settings is taken together with theirs if those show more (``look_ahead``: a
    channel whose own objects are not linked, but one behind a setting inside it; the first
    ``_LOOK_AHEAD_TRIES`` such values). Values
    that show no more linked objects and no other setting, or none that shows more, are added
    to ``dead`` and not tried again. Empty when nothing shows more.
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

    def clashes(trials: Mapping[str, Mapping[str, str]]) -> dict[str, int]:
        """For trials that show the same objects, how many bytes of the hidden settings with a
        fixed value the device holds otherwise (see :func:`_fixed_clashes`)."""
        assert fixed is not None and segments is not None
        uis: dict[str, DynamicUI] = {}
        for key, trial in trials.items():
            seeded = seed_dynamic_ui(
                application,
                {**values, **trial},
                project_code_page,
                module_instances=instances,
            )
            assert seeded is not None
            uis[key] = seeded
        return _fixed_clashes(uis, segments, fixed)

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
        gaining: list[tuple[int, int, str]] = []
        for candidate in values_to_try(parameter, current):
            _check(cancel)
            if dead is not None and (ref_id, candidate) in dead:
                continue
            try:
                objects, unlinked, reveals = shown({ref_id: candidate})
            except AllocatorOverflow:
                # More than the product has room for: no configuration of the device.
                if dead is not None:
                    dead.add((ref_id, candidate))
                continue
            gain = len(objects - base) - len(base - objects)
            if gain > 0:
                gaining.append((gain, -unlinked, candidate))
            elif objects == base and reveals:
                neutral.append((ref_id, candidate))
            elif dead is not None:
                dead.add((ref_id, candidate))
        if not gaining:
            continue
        top = max(gain for gain, _, _ in gaining)
        ties = [entry for entry in gaining if entry[0] == top]
        # Several values show the same objects (datapoint types of the same size, a pair of
        # buttons or two single ones): the hidden settings they come with tell them apart.
        clash: dict[str, int] = (
            clashes({candidate: {ref_id: candidate} for _, _, candidate in ties})
            if fixed and segments is not None and len(ties) > 1
            else {}
        )
        gain, fewest, value = max(
            ties, key=lambda entry: (-clash.get(entry[2], 0), entry[1])
        )
        best.append(((gain, fewest), ref_id, value))
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
            fixed=fixed,
            segments=segments,
            cancel=cancel,
        )
        if not nested:
            # Nothing it shows shows more either.
            if dead is not None:
                dead.add((ref_id, candidate))
            continue
        trial = {ref_id: candidate, **nested}
        count = len(shown(trial)[0])
        if count > found_count:
            found, found_count = trial, count
        elif count == found_count and found and fixed and segments is not None:
            # As many shown either way (a pair of buttons or two single ones): the hidden
            # settings with a fixed value tell them apart.
            clash = clashes({"found": found, "trial": trial})
            if clash["trial"] < clash["found"]:
                found = trial
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
    cancel: Callable[[], bool] | None = None,
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
        _check(cancel)
        seeded = seed_dynamic_ui(
            application, {**values, ref_id: candidate}, project_code_page
        )
        assert seeded is not None
        try:
            if not base <= _shown_objects(seeded, sizes):
                continue
            trial_fitting, trial_misfits = _union_fit(
                seeded, _decode_once(seeded, parameter_segments, property_values)
            )
        except AllocatorOverflow:
            # More than the product has room for: no configuration of the device.
            continue
        total = trial_fitting + len(trial_misfits)
        if len(trial_misfits) < fewest and (
            not total or trial_fitting / total >= share
        ):
            best, fewest = {ref_id: candidate}, len(trial_misfits)
    return best
