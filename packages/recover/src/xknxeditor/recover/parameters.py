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

import contextlib
import threading
from collections import Counter, OrderedDict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import count
from typing import TYPE_CHECKING, NamedTuple

from xknxeditor.prod.parser_v2.allocator import AllocatorOverflow
from xknxeditor.prod.script.errors import ScriptAborted
from xknxeditor.prod.script.sandbox import AbortToken

if TYPE_CHECKING:
    from xknxeditor.download.image import PropertyTarget
    from xknxeditor.prod import Application, MasterData
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
    from xknxeditor.prod.parser_v2.context import Gate
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI
    from xknxeditor.prod.parser_v2.encode import PropertyLayout, TypedPropertyKey
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
    # Bytes that differ (of the memory and of the properties a download writes), and missing
    # linked objects or shown objects with a different size.
    differing_bytes: int = 0
    differing_objects: int = 0
    # The search stopped at the limit of its effort, or was cancelled, with values left to try:
    # more effort may find a result that fits the device.
    limited: bool = False
    # Of ``unknown``, those whose type cannot give back a value from bytes at all (a lossy
    # encoding, such as a date without its year).
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
# How far a result is from the device: first the linked objects it does not show and the objects
# it shows with another size than the device's (the device's tables hold them: no number of
# objects shown without a link outweighs one), then what contradicts the device, the differing
# bytes and those objects (each weighing _OBJECT_WEIGHT bytes: the memory read holds the
# settings, no number of objects shown without a link outweighs a byte either), then the bytes
# that differ of properties no download writes (the device keeps its own value there, such as
# its hardware type: no result may match both, but the one that does tells the variant), then
# the objects it shows that are not linked, which only tell apart results that contradict the
# device alike (a device can have channels on whose objects it links to nothing).
_Score = tuple[int, int, int, int]
# How far a result is from the device, with the bytes it writes otherwise and the group objects
# it shows otherwise.
_Fit = tuple[_Score, set[_Cell], set[int]]
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


class _SearchCache:
    """What a search of one application's settings computes again and again from the same
    values: the UIs seeded with them (only read, never changed) and what the calculations make
    of them. Both depend on the application, the values and the code page alone."""

    # UIs and calculated values kept at most (the last used ones): a search asks for the same
    # values again soon after (a decode for the UI it seeded last), each holds thousands of
    # values (a UI megabytes).
    UIS = 8
    CALCULATED = 128
    # Explore passes kept at most (see _explored): their layouts compare, they do not hash.
    EXPLORED = 4

    def __init__(
        self, application: Application, master: MasterData | None = None
    ) -> None:
        self.application = application
        self.master = master
        self.uis: OrderedDict[tuple[object, ...], DynamicUI | None] = OrderedDict()
        self.calculated: OrderedDict[tuple[object, ...], _ValueMap] = OrderedDict()
        # The explore layouts of UIs explored, with the places found (the last used first).
        self.explored: list[
            tuple[tuple[object, ...], list[tuple[str, tuple[Gate, ...]]]]
        ] = []
        # The properties every download writes (``None``: not resolved yet, empty when the
        # procedure does not resolve), and those the download of each of the last UIs writes.
        self.always_written: frozenset[PropertyTarget] | None = None
        self.resolves = True
        self.written: OrderedDict[int, tuple[DynamicUI, frozenset[PropertyTarget]]] = (
            OrderedDict()
        )
        # The octet a full download fills each relative segment with (``None``: not resolved
        # yet, empty when the procedure does not resolve).
        self.fills: dict[str, int] | None = None

    def segment_fills(self) -> dict[str, int]:
        """The octet a full download fills each relative segment of the application with, by
        segment id (see :func:`~xknxeditor.download.relative_segment_fills`); empty when the
        procedure does not resolve."""
        from xknxeditor.download import relative_segment_fills
        from xknxeditor.download.errors import DownloadError

        if self.fills is None:
            try:
                self.fills = relative_segment_fills(self.application, self.master)
            except (DownloadError, ValueError):
                self.fills = {}
        return self.fills

    def unwritten(self, ui: DynamicUI, key: TypedPropertyKey) -> bool:
        """Whether no control of the load procedure a download of ``ui`` runs writes the
        property ``key`` (see :func:`~xknxeditor.download.written_properties`); false for
        every property when the procedure does not resolve."""
        from xknxeditor.download import written_properties
        from xknxeditor.download.errors import DownloadError

        if self.always_written is None:
            try:
                self.always_written = written_properties(self.application, self.master)
            except (DownloadError, ValueError):
                self.always_written, self.resolves = frozenset(), False
        if not self.resolves or _addresses(self.always_written, key):
            return False
        # By the UI itself (kept with its entry, so no other object has its id meanwhile).
        entry = self.written.get(id(ui))
        if entry is None or entry[0] is not ui:
            try:
                entry = (ui, written_properties(self.application, self.master, ui))
            except (DownloadError, ValueError):
                return False
            self.written[id(ui)] = entry
            if len(self.written) > self.UIS:
                self.written.popitem(last=False)
        self.written.move_to_end(id(ui))
        return not _addresses(entry[1], key)

    @staticmethod
    def get[V](store: OrderedDict[tuple[object, ...], V], key: tuple[object, ...]) -> V:
        value = store[key]
        store.move_to_end(key)
        return value

    @staticmethod
    def put[V](
        store: OrderedDict[tuple[object, ...], V],
        key: tuple[object, ...],
        value: V,
        limit: int,
    ) -> None:
        store[key] = value
        if len(store) > limit:
            store.popitem(last=False)


# The cache of the search running in this thread (see recover_parameters).
_SEARCH = threading.local()


def _search_cache(application: Application) -> _SearchCache | None:
    """The cache of the search of ``application`` running in this thread, if any."""
    cache: _SearchCache | None = getattr(_SEARCH, "cache", None)
    return cache if cache is not None and cache.application is application else None


def _seeded(
    application: Application,
    values: Mapping[str, str],
    project_code_page: str | None,
    *,
    module_instances: Sequence[tuple[str, str]] | None = None,
) -> DynamicUI | None:
    """:func:`~xknxeditor.recover.recover.seed_dynamic_ui`, the same UI again for the same
    values within a search: it is only read, never changed. Every reader evaluates it first,
    so it may come evaluated already."""
    from .recover import seed_dynamic_ui

    cache = _search_cache(application)
    if cache is None:
        return seed_dynamic_ui(
            application,
            values,
            project_code_page,
            module_instances=module_instances,
            evaluated=True,
        )
    key = (
        frozenset(values.items()),
        project_code_page,
        None if module_instances is None else tuple(module_instances),
    )
    if key in cache.uis:
        return cache.get(cache.uis, key)
    ui = seed_dynamic_ui(
        application,
        values,
        project_code_page,
        module_instances=module_instances,
        evaluated=True,
    )
    cache.put(cache.uis, key, ui, cache.UIS)
    return ui


def _seeded_for_lookups(
    application: Application, values: Mapping[str, str], project_code_page: str | None
) -> tuple[DynamicUI | None, DynamicUI | None]:
    """:func:`~xknxeditor.recover.recover.seed_dynamic_ui` of ``values`` as it comes back
    without ``evaluated`` (to look settings up in before it is evaluated), and a UI evaluated
    alike when the seeding comes back evaluated with it (see :func:`_seeded`, which hands it
    out within a search)."""
    from .recover import seed_dynamic_ui

    first = seed_dynamic_ui(application, values, project_code_page, evaluated=True)
    if first is None or not first.evaluated:
        return first, None
    instances = first.get_module_instances()
    cache = _search_cache(application)
    if cache is not None:
        cache.put(
            cache.uis,
            (frozenset(values.items()), project_code_page, None),
            first,
            cache.UIS,
        )
    if not instances:
        # Without module instances the seeding is the first build itself.
        return first, first
    plain = seed_dynamic_ui(
        application, values, project_code_page, module_instances=instances
    )
    return plain, first


def _parameter_to_refs(application: Application) -> Mapping[str, list[str]]:
    """Map each parameter id to the reference ids that point at it (of the last few programs:
    every decode asks for it)."""
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    program = application.program
    for n, (seen, refs) in enumerate(_PARAMETER_TO_REFS):
        if seen is program:
            _PARAMETER_TO_REFS.insert(0, _PARAMETER_TO_REFS.pop(n))
            return refs
    refs = _refs_by_parameter(ApplicationIndexer(program))
    _PARAMETER_TO_REFS.insert(0, (program, refs))
    del _PARAMETER_TO_REFS[4:]
    return refs


def _refs_by_parameter(indexer: ApplicationIndexer) -> dict[str, list[str]]:
    """The reference ids of each parameter id, in the order of the indexer's references."""
    refs: dict[str, list[str]] = {}
    for ref_id, ref in indexer.parameter_refs.items():
        refs.setdefault(ref.ref_id, []).append(ref_id)
    return refs


def _refs_of_parameters(indexer: ApplicationIndexer) -> Mapping[str, list[str]]:
    """:func:`_refs_by_parameter` of the indexer of a UI (an application's, shared by its UIs),
    of the last few."""
    for n, (seen, refs) in enumerate(_INDEXER_REFS):
        if seen is indexer:
            _INDEXER_REFS.insert(0, _INDEXER_REFS.pop(n))
            return refs
    refs = _refs_by_parameter(indexer)
    _INDEXER_REFS.insert(0, (indexer, refs))
    del _INDEXER_REFS[4:]
    return refs


# The references of each parameter, of the last programs decoded and of the last indexers.
_PARAMETER_TO_REFS: list[tuple[object, Mapping[str, list[str]]]] = []
_INDEXER_REFS: list[tuple[object, Mapping[str, list[str]]]] = []


def recover_parameters(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes] | None = None,
    project_code_page: str | None = None,
    linked_objects: Collection[int] = (),
    object_sizes: Mapping[int, str] | None = None,
    effort: RecoveryEffort = RecoveryEffort.MEDIUM,
    cancel: Callable[[], bool] | None = None,
    *,
    master: MasterData | None = None,
) -> RecoveredParameters:
    """Recover parameter reference values from a device's memory and properties.

    ``parameter_segments`` is ``{segment_id: bytes}`` and ``property_values`` is
    ``{(object_index, object_type, property_id, occurrence): bytes}``, both as read off the
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
    the device. ``master`` is the master data a download resolves the device's load procedure
    with: a property no control of it writes keeps the device's own value whatever the
    configuration, so its bytes only tell apart results alike otherwise, and do not count
    as differing. Without it, a merged procedure does not resolve, and every property counts.
    """
    if cancel is not None:
        requested = cancel
        stopped = False

        def latched_cancel() -> bool:
            nonlocal stopped
            stopped = stopped or requested()
            return stopped

        cancel = latched_cancel

    previous = getattr(_SEARCH, "cache", None)
    _SEARCH.cache = _SearchCache(application, master)
    try:
        return _search(
            application,
            parameter_segments,
            property_values,
            project_code_page,
            linked_objects,
            object_sizes,
            effort,
            cancel,
        )
    finally:
        _SEARCH.cache = previous


def _search(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes] | None,
    project_code_page: str | None,
    linked_objects: Collection[int],
    object_sizes: Mapping[int, str] | None,
    effort: RecoveryEffort,
    cancel: Callable[[], bool] | None,
) -> RecoveredParameters:
    """The search of :func:`recover_parameters`."""
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
    result = _refine_fixed(
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
    if fixed and effort is not RecoveryEffort.FAST and not result.complete:
        # The searches before decode the memory into hidden settings the product fixes, so a
        # result can fit only that way; with their fixed values kept, the selector that picks
        # another branch can need a setting it shows changed too.
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
            fixed=fixed,
        )
    if (
        effort is RecoveryEffort.FAST
        and not result.complete
        and not result.limited
        and _pairs_to_try(
            application,
            parameter_segments,
            property_values,
            project_code_page,
            linked,
            sizes,
            result,
        )
    ):
        # The fast search leaves out the search of pairs the other efforts make: with
        # selectors left to try, more effort may find a result that fits.
        result = replace(result, limited=True)
    return result


def _pairs_to_try(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes] | None,
    project_code_page: str | None,
    linked: set[int],
    sizes: Mapping[int, str],
    recovered: RecoveredParameters,
) -> bool:
    """Whether the search of pairs (see :func:`_refine_pairs`) has selectors to try where
    ``recovered`` differs from the device."""
    ui = _seeded(application, recovered.values, project_code_page)
    if ui is None:
        return False
    current = _misfit(ui, parameter_segments, linked, sizes, property_values)
    if current is None:
        return False
    return any(
        not _calculated_from_reads(ui, gate)
        for gate, _value in _refinement_trials(
            ui, current[1], _contradicting_objects(ui, linked, sizes)
        )
    )


def _infer_all(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes] | None,
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
    property_values: Mapping[TypedPropertyKey, bytes] | None,
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
    # The values tried for the memory in a round before, each with its UI: one round takes one
    # setting of one module instance, so most values of the next are tried again where only
    # that instance differs (see _infer_from_memory).
    evaluated: dict[tuple[str, str], _Evaluated] = {}
    for _ in range(_INFER_ROUNDS):
        _check(cancel)
        # The memory names the branch of a selector through a hidden setting with a fixed
        # value behind it first: what it holds there is no guess. Only without such values
        # do the objects and the unions decide.
        named = _infer_from_fixed(
            application,
            parameter_segments,
            property_values,
            project_code_page,
            values,
            linked,
            sizes,
            fixed,
            cancel,
        )
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
            if linked and not named
            else named
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
            evaluated,
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


def _compared(ui: DynamicUI, cells: set[_Cell]) -> set[_Cell]:
    """Of the differing bytes ``cells``, those that tell the device apart: of the memory where
    a parameter is stored or ``ui`` writes (a device can keep other data in its segments, which
    changes at run time), and of the properties."""
    stored = ui.stored_parameter_cells().keys() | _written_cells(ui)
    return {cell for cell in cells if cell in stored or _is_property_cell(cell)}


# A byte of a property in place of one of a code segment: the key of the property spelled out
# after this prefix, which no segment id has.
_PROPERTY = "property:"


def _property_cell(key: TypedPropertyKey, offset: int) -> _Cell:
    """The byte ``offset`` of the property ``key`` as a cell of its own."""
    return (
        _PROPERTY + ",".join("" if part is None else str(part) for part in key),
        offset,
    )


def _is_property_cell(cell: _Cell) -> bool:
    """Whether ``cell`` is a byte of a property (see :func:`_property_cell`)."""
    return cell[0].startswith(_PROPERTY)


# The property layout of each UI, of the last few (a search asks again and again for the same):
# the layout, the offsets of each property it stores parameters at, and (once asked for) the
# param-refs of each byte as cells.
_PROPERTY_LAYOUTS: list[
    tuple[
        DynamicUI,
        PropertyLayout,
        dict[TypedPropertyKey, list[int]],
        list[dict[_Cell, set[str]]],
    ]
] = []


def _property_layout(
    ui: DynamicUI,
) -> tuple[
    PropertyLayout, dict[TypedPropertyKey, list[int]], list[dict[_Cell, set[str]]]
]:
    """The layout of the properties of ``ui`` (see :meth:`DynamicUI.stored_property_layout`),
    the offsets of each property it stores parameters at, and a slot for the param-refs of
    each byte as cells (see :func:`_property_owners`)."""
    for seen, layout, offsets, owners in _PROPERTY_LAYOUTS:
        if seen is ui:
            return layout, offsets, owners
    layout = ui.stored_property_layout()
    offsets: dict[TypedPropertyKey, list[int]] = {}
    for key, offset in layout.cells:
        offsets.setdefault(key, []).append(offset)
    entry = (ui, layout, offsets, list[dict[_Cell, set[str]]]())
    _PROPERTY_LAYOUTS.insert(0, entry)
    del _PROPERTY_LAYOUTS[8:]
    return entry[1:]


def _property_owners(ui: DynamicUI) -> dict[_Cell, set[str]]:
    """The param-refs stored at each byte of a property of ``ui``, as cells (see
    :func:`_property_cell`)."""
    layout, _offsets, owners = _property_layout(ui)
    if not owners:
        owners.append(
            {
                _property_cell(key, offset): refs
                for (key, offset), refs in layout.cells.items()
            }
        )
    return owners[0]


def _differing_property_cells(
    ui: DynamicUI, properties: Mapping[TypedPropertyKey, bytes]
) -> set[_Cell] | None:
    """The bytes of the ``properties`` read off the device that ``ui`` does not give back, as
    cells (see :func:`_property_cell`), where a parameter is stored: the bits its active
    parameters write, and the other bits of the byte as the parameters there (no member of a
    union) write them with their default values. A property holds its parameter's default until a download writes
    it, so another value there is one a configuration that writes it gave (a setting shown
    behind a switch the device keeps no copy of). A byte only inactive parameters cover is
    written with the property's base, zeros (or the factory contents, which the defaults
    stand for here): zeros there fit too. An object addressed by index is one object whatever
    the occurrence (as :func:`~xknxeditor.prod.parser_v2.encode.property_data` reads it).
    ``None`` when ``ui`` cannot be encoded."""
    from xknxeditor.prod.errors import EncodingError

    try:
        written = ui.encode_to_typed_properties_masked()
    except (EncodingError, ValueError):
        return None
    layout, offsets, _owners = _property_layout(ui)
    by_index: dict[tuple[int | None, int | None, int], bytes] = {}
    for (index, object_type, pid, _occurrence), value in properties.items():
        if index is not None:
            by_index.setdefault((index, object_type, pid), value)
    differing: set[_Cell] = set()
    for key, stored in offsets.items():
        device = properties.get(key)
        if device is None and key[0] is not None:
            device = by_index.get(key[:3])
        if device is None:
            continue
        mine = written.get(key)
        default = layout.defaults.get(key)
        for offset in stored:
            if offset >= len(device):
                continue
            held = device[offset]
            ours = 0
            base = False
            if mine is not None and offset < len(mine.mask):
                ours = mine.mask[offset]
                if (mine.data[offset] ^ held) & ours:
                    differing.add(_property_cell(key, offset))
                    continue
                # Covered (by an inactive parameter downloaded in the background, or beside
                # an active one), the download writes the base where no value is.
                base = mine.covered[offset] != 0
            # The bits no active parameter writes: the default, unless a configuration wrote
            # another value.
            if default is None or offset >= len(default.mask):
                continue
            rest = default.mask[offset] & ~ours
            if (default.data[offset] ^ held) & rest and not (base and not held & rest):
                differing.add(_property_cell(key, offset))
    return differing


def _addresses(targets: Collection[PropertyTarget], key: TypedPropertyKey) -> bool:
    """Whether one of the load controls' ``targets`` (see
    :func:`~xknxeditor.download.written_properties`) is the property ``key``: the same index,
    or the same object type and occurrence. One addressed by index and the other by type may
    be the same object, and counts."""
    index, object_type, pid, occurrence = key
    for target_index, target_type, target_occurrence, target_pid in targets:
        if target_pid != pid:
            continue
        if index is not None and target_index is not None:
            if index == target_index:
                return True
        elif object_type is not None and target_type is not None:
            if object_type == target_type and (occurrence or 0) == target_occurrence:
                return True
        else:
            return True
    return False


def _unwritten_property_cells(ui: DynamicUI, cells: Collection[_Cell]) -> set[_Cell]:
    """Of ``cells``, the bytes of properties no control of the load procedure a download of
    ``ui`` runs writes (see :meth:`_SearchCache.unwritten`): the device keeps its own value
    there whatever a download writes (a hardware type), so they only tell apart results that
    fit the device alike otherwise. None outside a search."""
    names = {cell[0] for cell in cells if _is_property_cell(cell)}
    cache: _SearchCache | None = getattr(_SEARCH, "cache", None)
    if not names or cache is None:
        return set()
    _layout, offsets, _owners = _property_layout(ui)
    keys = {_property_cell(key, 0)[0]: key for key in offsets}
    unwritten = {
        name for name in names if name in keys and cache.unwritten(ui, keys[name])
    }
    return {cell for cell in cells if cell[0] in unwritten}


def _nothing_differs(score: _Score) -> bool:
    """Whether a result with ``score`` contradicts no byte or object of the device, not even
    one of a property no download writes (only objects it shows without a link)."""
    return not score[1] and not score[2]


def _differing_cells(
    ui: DynamicUI, parameter_segments: Mapping[str, bytes]
) -> set[_Cell] | None:
    """The bytes ``ui`` writes otherwise than the device holds them; ``None`` when it cannot be
    encoded. A byte a parameter of the application is stored at differs only in the bits a
    setting is stored at: its other bits are no setting's (the flags of a group object table
    record the product places a parameter in, which the download writes from the links), and
    no result writes them otherwise. One a module instance's setting is stored at as well
    counts as a whole. A byte no active setting writes in a relative segment a full download
    fills when it allocates it holds the fill (see :meth:`_SearchCache.segment_fills`)."""
    from xknxeditor.prod.errors import EncodingError

    try:
        encoded = ui.encode_to_memory()
    except (EncodingError, ValueError):
        return None
    cache: _SearchCache | None = getattr(_SEARCH, "cache", None)
    fills = cache.segment_fills() if cache is not None else {}
    bits: dict[str, bytes] | None = None
    written: dict[str, bytes] | None = None
    cells: dict[_Cell, frozenset[str]] | None = None
    differing: set[_Cell] = set()
    for segment_id, device in parameter_segments.items():
        fill = fills.get(segment_id, 0)
        for offset, (mine, theirs) in enumerate(
            zip(encoded.get(segment_id, b""), device, strict=False)
        ):
            if mine == theirs:
                continue
            if fill:
                if written is None:
                    written = ui.written_bit_mask()
                mask = written.get(segment_id, b"")
                if not (mask[offset] if offset < len(mask) else 0):
                    # No active setting writes the byte: the download leaves the fill.
                    mine = fill
                    if mine == theirs:
                        continue
            if bits is None:
                bits = ui.stored_bit_mask()
            stored = bits.get(segment_id, b"")
            own = stored[offset] if offset < len(stored) else 0
            if own and not (mine ^ theirs) & own:
                if cells is None:
                    cells = ui.stored_parameter_cells()
                if not any(
                    "_MI-" in ref for ref in cells.get((segment_id, offset), ())
                ):
                    continue
            differing.add((segment_id, offset))
    return differing


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
        return _module_gates_of(ui, instance)
    return _qualified(gates, instance)


def _module_gates_of(ui: DynamicUI, instance: tuple[str, str]) -> set[str]:
    """The selectors on the way to the module of the module instance ``instance`` (see
    :func:`_instance_of`): of its Module, its definition's id with the module's number
    (``..._MD-7_SM-1_M-1`` for ``..._MD-7_M-30_MI-1_SM-1_M-1_MI-1-1``). Those of a submodule
    are settings of the instance its Module is placed in: qualified with that one."""
    module = instance[0][: instance[0].rindex("_MI-")]
    gates = ui.module_gates(instance[1] + module[module.rindex("_M-") :])
    return _qualified(gates, _enclosing_instance(module, instance[1]))


def _enclosing_instance(module: str, definition: str) -> tuple[str, str] | None:
    """For the module ``module`` (a module instance's id without its ``_MI-`` part) of the
    definition ``definition``, the instance its Module element is placed in, as
    :func:`_instance_of` gives it (``("..._MD-7_M-30_MI-1", "..._MD-7")`` for
    ``..._MD-7_M-30_MI-1_SM-1_M-1``); None for a module the application places."""
    if "_SM-" not in definition:
        return None
    top = definition[: definition.index("_SM-")]
    enclosing = module[: module.rindex("_SM-")]
    if "_SM-" not in enclosing:
        return enclosing, top
    # A submodule placed in another submodule: the ids of that one's elements carry its
    # definition's part (``..._MI-1-1_SM-2_P-1_R-1`` for ``..._MD-7_SM-2_P-1_R-1``).
    submodule = enclosing[enclosing.rindex("_SM-") + 1 :].split("_")[0]
    return f"{enclosing}_{submodule}", f"{top}_{submodule}"


def _outer_gates(
    ui: DynamicUI,
    ref_id: str,
    gates_of: Callable[[str], frozenset[str]] | None = None,
) -> set[str]:
    """For the param-ref ``ref_id`` of a module instance that has selectors of its own on the
    way to it, the selectors on the way to the instance's module (:func:`_gates_of` gives
    them only for one without): whether the instance is there at all (another module in
    its place)."""
    local = ui.local_ref_id(ref_id)
    instance = _instance_of(ref_id, local)
    if instance is None or "_MI-" not in instance[0]:
        return set()
    if not (gates_of or ui.parameter_gates)(local):
        return set()
    return _module_gates_of(ui, instance)


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


def _calculated_from_reads(ui: DynamicUI, ref_id: str) -> bool:
    """Whether a parameter calculation computes ``ref_id`` from a value read from the device
    (one of its inputs has a copy there): the decode gives the selector already, it is not
    guessed. Computed only from settings the device keeps no copy of either (a hidden "show
    the disabling function" switch computed from the settings that show it), it is guessed
    like any of them; so is one on the left side of a calculation whose stored values on the
    right ``ui`` does not give the device (a switch that shows the times the calculation
    downloads: they are read once it has the right value), and one on the left side of a
    calculation with a setting on its right the device keeps no copy of either (the shown
    copy of a hidden switch, beside the copies of settings read): the values read need not be
    what it is computed from."""
    local = ui.local_ref_id(ref_id)
    instance = _instance_of(ref_id, local)
    indexer = ui.indexer
    right = _qualified(
        {
            ref.ref_id
            for calculation in indexer.calculations_for_l(local)
            for ref in calculation.rparameters.parameter_ref_ref
        },
        instance,
    )
    if any(_is_unstored(ui, ref) for ref in right):
        # The values read on the right do not decide it: only a stored setting an LR
        # transformation computes it from still does.
        right_reads: frozenset[str] = frozenset()
    else:
        right_reads = ui.active_parameter_ref_ids() if right else frozenset[str]()
    return any(
        not _is_unstored(ui, ref) and (ref not in right or ref in right_reads)
        for ref in _calculation_inputs(ui, ref_id)
    )


def _alike_for_reads(
    application: Application,
    values: Mapping[str, str],
    project_code_page: str | None,
    ref_id: str,
    value: str,
) -> bool:
    """Whether ``value`` of a setting a calculation computes from the values read (see
    :func:`_calculated_from_reads`) gives the stored settings the values ``values`` has: the
    calculation computes the same for several of its values (two functions of a button with
    one function code), which only the settings they show tell apart. One a calculation
    computes from a stored setting (on its right side) has the one value it computes."""
    from .recover import seed_dynamic_ui

    trial = seed_dynamic_ui(
        application, {**values, ref_id: value}, project_code_page, evaluated=True
    )
    if trial is None:
        return False
    local = trial.local_ref_id(ref_id)
    instance = _instance_of(ref_id, local)
    if any(
        not _is_unstored(trial, source)
        for calculation in trial.indexer.calculations_for_r(local)
        for source in _qualified(
            [ref.ref_id for ref in calculation.lparameters.parameter_ref_ref], instance
        )
    ):
        return False
    try:
        changes = trial.recalculate([ref_id])
    except ScriptAborted:
        return False
    return not any(
        new is not None
        and new != values.get(changed)
        and not _is_unstored(trial, changed)
        for changed, (_old, new) in changes.items()
    )


def _instance_of(ref_id: str, local: str) -> tuple[str, str] | None:
    """For the param-ref ``ref_id`` of a module instance (``local`` its definition's), the
    instance's part of the id and the definition's; None for one of the application."""
    if local == ref_id or "_MD-" not in local:
        return None
    definition = _definition_of(local)
    return ref_id[: len(ref_id) - (len(local) - len(definition))], definition


def _definition_of(local: str) -> str:
    """The module definition the id ``local`` (of an element of a module definition) belongs
    to: ``..._MD-7`` for ``..._MD-7_P-1_R-1``, the submodule definition ``..._MD-7_SM-1`` for
    ``..._MD-7_SM-1_P-10_R-10``."""
    start = local.rindex("_MD-")
    parts = local[start + 1 :].split("_")
    taken = 1
    while taken < len(parts) and parts[taken].startswith("SM-"):
        taken += 1
    return local[:start] + "_" + "_".join(parts[:taken])


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
        # The objects of a submodule instance (named after the instance it is in) keep their
        # ids below the top-level definition: ``..._MI-1-1`` + ``_SM-1_O-3-0_R-1``.
        submodule = definition.find("_SM-")
        local_base = definition[:submodule] if submodule != -1 else definition
        values = {
            f"{application_id}_{argument}": value
            for argument, value in arguments.get(instance, [])
        }
        for ref_id, number, base in numbered:
            # Not one of a submodule definition inside it (``..._MD-7_SM-1_O-...``).
            if "_MD-" not in ref_id or _definition_of(ref_id) != definition:
                continue
            try:
                offset = int(values.get(base, "0")) if base else 0
            except ValueError:
                continue
            result.setdefault(number + offset, []).append(
                instance + ref_id[len(local_base) :]
            )
    return result


def _absent_module_gates(ui: DynamicUI, *, objects: bool = True) -> set[str]:
    """The selectors on the way to the application's modules that ``ui`` makes no instance of
    and whose definition has group objects (in a submodule too; without ``objects`` any); of a
    submodule, in each module instance that makes none of it, those of that instance."""
    instances = ui.get_module_instances()
    made = {instance[: instance.rindex("_MI-")] for instance, _ in instances}
    with_objects = {
        _definition_of(ref_id)
        for ref_id in ui.indexer.com_object_refs
        if "_MD-" in ref_id
    }

    def has_objects(definition: str) -> bool:
        return not objects or any(
            seen == definition or seen.startswith(f"{definition}_SM-")
            for seen in with_objects
        )

    gates: set[str] = set()
    for module_id, module_gates in ui.gated_modules().items():
        if "_M-" not in module_id or not has_objects(
            module_id[: module_id.rindex("_M-")]
        ):
            continue
        if "_SM-" not in module_id:
            if module_id not in made:
                gates |= module_gates
            continue
        # A submodule's Module placed in a module definition (``..._MD-7_SM-1_M-1``, local to
        # ``..._MD-7``): in each instance of that definition (``..._MD-7_M-30_MI-1``, its
        # Module then ``..._MD-7_M-30_MI-1_SM-1_M-1``).
        for instance, definition in instances:
            if not module_id.startswith(f"{definition}_SM-"):
                continue
            rest = module_id[len(definition) :]
            if rest.count("_SM-") != 1 or f"{instance}{rest}" in made:
                continue
            gates |= _qualified(
                module_gates,
                _enclosing_instance(
                    f"{instance}{rest}", f"{definition}{rest[: rest.rindex('_M-')]}"
                ),
            )
    return gates


def _count_selectors(
    ui: DynamicUI,
    gates: Collection[str],
    shown: Collection[str],
    weight: Counter[str],
) -> None:
    """Count the selectors ``gates`` in ``weight``, and for one not ``shown`` those on its own way,
    ``_REFINE_DEPTH`` selectors up; for one a parameter calculation sets, the parameters it is
    calculated from, and for one an Assign copies from another param-ref, that one (shown or
    not: the selector follows them; a module's hidden copy of an application setting is decided
    by that setting)."""
    frontier = set(gates)
    for _depth in range(_REFINE_DEPTH):
        above: set[str] = set()
        # In a fixed order: selectors counted as often are tried in the order counted first.
        for gate in sorted(frontier):
            weight[gate] += 1
            if gate not in shown:
                above |= _gates_of(ui, gate)
            above |= _calculation_inputs(ui, gate)
            source = ui.assigned_source(gate)
            if source is not None:
                above.add(source)
        frontier = above


def _refine(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes] | None,
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
    better (see ``_Score``: each linked group object it does not show weighing ``_OBJECT_WEIGHT``
    differing bytes, the objects it shows that are not linked only telling apart results alike
    otherwise; the search ends once no byte differs and every linked object shows). The
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
    ui = _seeded(application, values, project_code_page)
    if ui is None:
        return RecoveredParameters(values=values, unknown=unknown)

    def misfit(seeded: DynamicUI) -> tuple[_Score, set[_Cell], set[int]] | None:
        return _misfit(seeded, parameter_segments, linked, sizes, property_values)

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
            fit = (fit[0], _compared(seeded, fit[1]), fit[2])
        if fit is not None:
            fit = (fit[0], fit[1], _contradicting_objects(seeded, linked, sizes))
        return _with_misfit(values, unknown, fit, seeded, limited=limited)

    first = misfit(ui)
    if first is None or _nothing_differs(first[0]):
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
            # The settings a trial of a calculated selector changes together (see
            # :func:`_joint_values_to_try`).
            joint: dict[tuple[str, str], _ValueMap] = {}
            try:
                trials = [
                    trial
                    for trial in _refinement_trials(
                        ui,
                        differing,
                        wrong,
                        cancel,
                        hidden=_hidden_fixed_values(application.program, ui),
                        joint=joint,
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
                        {**inferred, **joint.get((gate, value), {gate: value})},
                        set(dead),
                        cancel,
                    )
                    # Cancelled during the last inference round: not measured (an encode
                    # of a large application takes seconds).
                    _check(cancel)
                except _Cancelled:
                    return (current, values, unknown, inferred, ui), True, True
                except AllocatorOverflow:
                    # A value that lays out more than the product has room for (more module
                    # instances than an allocator holds): no configuration of the device.
                    continue
                seeded = _seeded(application, trial_values, project_code_page)
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
                    if _nothing_differs(score):
                        break
                elif worse is not None:
                    worse.append((result[0], gate, trial))
            if not corrected or _nothing_differs(current[0]):
                break
        else:
            # Every correction was taken, and the result still differs.
            limited = True
        return (current, values, unknown, inferred, ui), limited, False

    state: _State = (first, values, unknown, inferred, ui)
    while True:
        worse: list[tuple[_Score, str, _State]] = []
        state, limited, stopped = descend(state, worse)
        if stopped or _nothing_differs(state[0][0]):
            break
        # A selector whose value alone fits worse, as a channel that only fits once its own
        # settings are corrected too: the corrections behind the values that fit worse (the
        # selector keeping its value), those that fit best first, taken when they end better
        # than the result.
        worse.sort(key=lambda entry: entry[0])
        better = None
        # A selector a calculation computes from the values read is not guessed.
        worse = [
            entry
            for entry in worse
            if not _calculated_from_reads(entry[2][4], entry[1])
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
    properties: Mapping[TypedPropertyKey, bytes] | None = None,
) -> tuple[_Score, set[_Cell], set[int]] | None:
    """How far ``seeded`` is from the device (see ``_Score``), the bytes it differs in (of the
    memory where a parameter is stored, and of the ``properties`` read, see
    :func:`_differing_property_cells`) and the group objects it shows otherwise; ``None`` when
    it cannot be encoded."""
    differing = _differing_cells(seeded, parameter_segments)
    if differing is None:
        return None
    if differing:
        differing = _compared(seeded, differing)
    unwritten: set[_Cell] = set()
    if properties:
        in_properties = _differing_property_cells(seeded, properties)
        if in_properties is None:
            return None
        # Kept with the others, so the search tries the selectors behind them too.
        unwritten = _unwritten_property_cells(seeded, in_properties)
        differing |= in_properties
    objects: set[int] = _shown_objects(seeded) if linked or sizes else set()
    matching = _shown_objects(seeded, sizes) if sizes else objects
    conflicts = objects - matching
    wrong = (linked ^ objects) | conflicts if linked else conflicts
    # Missing linked objects and size conflicts contradict the device, and count first: a
    # channel whose linked objects show is on, however many objects it shows without a link.
    # The differing bytes count next: a channel whose settings the memory holds is on, however
    # many objects it shows without a link. Those only tell apart results alike otherwise, after
    # the bytes of properties no download writes.
    contradicted = len((linked - objects) | conflicts)
    contradiction = _OBJECT_WEIGHT * contradicted + len(differing) - len(unwritten)
    unlinked = len(objects - linked) if linked else 0
    return (contradicted, contradiction, len(unwritten), unlinked), differing, wrong


def _refinement_trials(
    ui: DynamicUI,
    differing: set[_Cell],
    wrong: set[int],
    cancel: Callable[[], bool] | None = None,
    *,
    hidden: Mapping[str, str] | None = None,
    joint: dict[tuple[str, str], _ValueMap] | None = None,
) -> list[tuple[str, str]]:
    """Candidate selectors, ordered by how many mismatching cells and objects they gate; with
    ``hidden`` (local param-ref -> fixed value, see :func:`_hidden_fixed_values`) a hidden
    selector's fixed value too. With ``joint``, a hidden selector a calculation computes from
    several shown settings the device keeps no copy of is tried with those changed together
    (see :func:`_joint_values_to_try`), after the other trials: the trial
    ``(selector, value)`` stands for the settings ``joint`` maps it to. Raises :class:`_Cancelled` once ``cancel`` says so."""
    from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try

    shown = shown_parameters(ui.ui())
    weight: Counter[str] = Counter()
    # The selectors that decide whether a module instance is there, tried after the others.
    outer: Counter[str] = Counter()
    cells = ui.stored_parameter_cells()
    active_refs: frozenset[str] | None = None
    unowned = False
    for cell in sorted(differing):
        _check(cancel)
        owners = (
            _property_owners(ui).get(cell, set[str]())
            if _is_property_cell(cell)
            else cells.get(cell, ())
        )
        for ref_id in sorted(owners):
            _count_selectors(ui, _gates_of(ui, ref_id), shown, weight)
            _count_selectors(ui, _outer_gates(ui, ref_id), shown, outer)
        if not unowned and not _is_property_cell(cell):
            if active_refs is None:
                active_refs = ui.active_parameter_ref_ids()
            unowned = not any(ref_id in active_refs for ref_id in owners)
    if unowned:
        # A byte no active setting writes (only settings shown otherwise are stored there): a
        # module instance the tree does not make can own it (a submodule another function of
        # a button places), whose settings are not known before it is there.
        _count_selectors(ui, _absent_module_gates(ui, objects=False), shown, outer)
    objects_of = _objects_by_number(ui) if wrong else {}
    for number in sorted(wrong):
        _check(cancel)
        for ref_id in objects_of.get(number, ()):
            _count_selectors(ui, _gates_of(ui, ref_id, ui.object_gates), shown, weight)
            _count_selectors(
                ui, _outer_gates(ui, ref_id, ui.object_gates), shown, outer
            )
    if any(number not in objects_of for number in wrong):
        # A linked object no reference of the instances there are now has (its number is one
        # of a further repetition): the selectors on the way to the modules of those
        # instances, the repeat counts among them, make more of them. Or one of a module
        # instance the tree does not make at all (a function switched off), whose numbers are
        # not known before it is there: the selectors on the way to such modules with group
        # objects.
        for instance in ui.get_module_instances():
            _check(cancel)
            _count_selectors(ui, _module_gates_of(ui, instance), shown, outer)
        _count_selectors(ui, _absent_module_gates(ui), shown, outer)
    trials: list[tuple[str, str]] = []
    together: list[tuple[str, str]] = []
    actives: frozenset[str] | None = None
    ranked = [
        *weight.most_common(),
        *((gate, n) for gate, n in outer.most_common() if gate not in weight),
    ]
    for gate, _count in ranked:
        parameter = shown.get(gate)
        if parameter is None:
            # A hidden setting nobody edits keeps the value the product gives it, even where
            # the memory holds another's (a later member of its union).
            value = hidden.get(ui.local_ref_id(gate)) if hidden else None
            if value is not None and ui.get_value(gate) != value:
                trials.append((gate, value))
            elif value is None:
                kept = _kept_values_to_try(ui, gate, shown)
                trials.extend((gate, other) for other in kept)
                if not kept and joint is not None:
                    for other, change in _joint_values_to_try(ui, gate, shown):
                        joint[(gate, other)] = change
                        together.append((gate, other))
            continue
        if actives is None:
            actives = ui.active_parameter_ref_ids()
        # A union member a later active member writes over in part keeps only some of its
        # bits: the memory holds several of its values alike.
        if _inferable(ui, gate, parameter) or (
            _editable(parameter) and _written_over_in_part(ui, gate, actives)
        ):
            now = ui.get_value(gate) or parameter.default_value
            trials.extend((gate, value) for value in values_to_try(parameter, now))
    # The settings changed together after every one alone: those decide more often.
    return trials + together


def _kept_values_to_try(
    ui: DynamicUI, ref_id: str, shown: Collection[str]
) -> list[str]:
    """The other values of a hidden setting the device keeps no copy of that a parameter
    calculation computes from settings of which not all are read: one of them hidden (the
    device keeps no copy of it, or another setting of its union is the one written), the
    setting keeps the value computed before (a "show the lead objects" switch computed from
    functions a configuration no longer shows), which only the objects it shows tell. Empty for
    any other setting: the values read decide it, or a shown input the device keeps no copy of,
    whose values are tried instead.

    The same for a hidden setting only Assigns of constants set, none of which is in effect:
    it keeps the value the last one assigned (a "show the total objects" switch the Assigns of
    functions a configuration no longer shows set): the values the Assigns give it."""
    from xknxeditor.prod.parser_v2.activation import values_to_try
    from xknxeditor.prod.parser_v2.ui.parameter import UiParameter, resolve_widget

    local = ui.local_ref_id(ref_id)
    indexer = ui.indexer
    calculations = indexer.calculations_for_r(local)
    if not calculations:
        assigned = _assigned_constants(ui).get(local)
        if (
            not assigned
            or not _is_unstored(ui, ref_id)
            or ref_id in ui.assign_targets()
        ):
            return []
        now = ui.get_value(ref_id) or indexer.default_value(local) or ""
        return sorted(value for value in assigned if not _same_value(value, now))
    if not _is_unstored(ui, ref_id):
        return []
    instance = _instance_of(ref_id, local)
    inputs = _qualified(
        {
            ref.ref_id
            for calculation in calculations
            for ref in calculation.lparameters.parameter_ref_ref
        },
        instance,
    )
    actives = ui.active_parameter_ref_ids()
    if any(ref in shown and _is_unstored(ui, ref) for ref in inputs) or all(
        ref in actives and not _is_unstored(ui, ref) for ref in inputs
    ):
        return []
    ref = indexer.parameter_refs.get(local)
    base = indexer.parameters.get(ref.ref_id) if ref is not None else None
    parameter_type = (
        indexer.parameter_types.get(base.parameter_type) if base is not None else None
    )
    if parameter_type is None:
        return []
    default = indexer.default_value(local) or ""
    parameter = UiParameter(
        ref_id=ref_id,
        label="",
        value=ui.get_value(ref_id) or default,
        widget=resolve_widget(parameter_type),
        default_value=default,
    )
    return values_to_try(parameter, parameter.value, ui.tested_values(ref_id))


# The constants the Assigns write into each local param-ref, per indexer of the last few
# applications searched (see _assigned_constants).
_ASSIGNED_CONSTANTS: list[tuple[object, dict[str, frozenset[str]]]] = []


def _assigned_constants(ui: DynamicUI) -> dict[str, frozenset[str]]:
    """The values the Assigns of the application and its module definitions write into each
    param-ref (local to its application or module definition), of those only Assigns of
    constants write: one an Assign copies another setting into takes that one's value."""
    from xknxeditor.namespaces.intermediate import Assign

    indexer = ui.indexer
    for seen, known in _ASSIGNED_CONSTANTS:
        if seen is indexer:
            return known
    constants: dict[str, set[str]] = {}
    copied: set[str] = set()
    stack: list[object] = [
        getattr(getattr(ui, "_app", None), "dynamic", None),
        *(definition.dynamic for definition in indexer.module_defs.values()),
    ]
    while stack:
        element = stack.pop()
        if element is None:
            continue
        if isinstance(element, Assign):
            if element.source_param_ref_ref is not None or element.value is None:
                copied.add(element.target_param_ref_ref)
            else:
                constants.setdefault(element.target_param_ref_ref, set()).add(
                    element.value
                )
            continue
        stack.extend(getattr(element, "when", None) or [])
        stack.extend(getattr(element, "choice", None) or [])
    known = {
        target: frozenset(values)
        for target, values in constants.items()
        if target not in copied
    }
    _ASSIGNED_CONSTANTS.insert(0, (indexer, known))
    del _ASSIGNED_CONSTANTS[4:]
    return known


def _joint_values_to_try(
    ui: DynamicUI, ref_id: str, shown: Mapping[str, UiParameter]
) -> list[tuple[str, _ValueMap]]:
    """For a hidden setting a parameter calculation computes from several shown settings of
    one type the device keeps no copy of (a channel shown while any of its groups is switched
    on), those settings changed together: for each value of the type, every one of them that
    has another one set to it (all groups switched off), as ``(value, settings)``. Each of
    them changed alone can leave the calculated setting as it is, and with it what the device
    holds. Empty when fewer than two such settings change."""
    from xknxeditor.prod.parser_v2.activation import values_to_try

    local = ui.local_ref_id(ref_id)
    indexer = ui.indexer
    inputs = _qualified(
        {
            ref.ref_id
            for calculation in indexer.calculations_for_r(local)
            for ref in calculation.lparameters.parameter_ref_ref
        },
        _instance_of(ref_id, local),
    )
    by_type: dict[str, list[str]] = {}
    for ref in sorted(inputs):
        if ref not in shown or not _inferable(ui, ref, shown[ref]):
            continue
        parameter_ref = indexer.parameter_refs.get(ui.local_ref_id(ref))
        parameter = (
            indexer.parameters.get(parameter_ref.ref_id)
            if parameter_ref is not None
            else None
        )
        if parameter is not None:
            by_type.setdefault(parameter.parameter_type, []).append(ref)
    found: list[tuple[str, _ValueMap]] = []
    for refs in by_type.values():
        if len(refs) < 2:
            continue
        others = {
            ref: values_to_try(
                shown[ref], ui.get_value(ref) or shown[ref].default_value
            )
            for ref in refs
        }
        for value in dict.fromkeys(value for ref in refs for value in others[ref]):
            change = {ref: value for ref in refs if value in others[ref]}
            if len(change) > 1:
                found.append((value, change))
    return found


def _guessed(ui: DynamicUI, values: Mapping[str, str]) -> _ValueMap:
    """The values of ``values`` the memory does not decide alone: those of settings the device
    keeps no copy of (see :func:`_is_unstored`) and of union members a later active member
    writes over in part (see :func:`_written_over_in_part`)."""
    actives = ui.active_parameter_ref_ids()
    return {
        key: value
        for key, value in values.items()
        if _is_unstored(ui, key) or _written_over_in_part(ui, key, actives)
    }


def _computed_hidden(ui: DynamicUI, ref_id: str) -> bool:
    """Whether a parameter calculation computes the param-ref ``ref_id`` (it is on the right
    side of one) and the user cannot change it (no edit ever sets it otherwise)."""
    from xknxeditor.namespaces.intermediate.access_t import Access

    local = ui.local_ref_id(ref_id)
    indexer = ui.indexer
    if not indexer.calculations_for_r(local):
        return False
    ref = indexer.parameter_refs.get(local)
    parameter = indexer.parameters.get(ref.ref_id) if ref is not None else None
    access = getattr(ref, "access", None) or getattr(parameter, "access", None)
    return access is not None and access is not Access.READ_WRITE


def _written_over_in_part(ui: DynamicUI, ref_id: str, actives: Collection[str]) -> bool:
    """Whether later active members of ``ref_id``'s union write over some of its bits, but not
    all (see :meth:`DynamicUI.later_union_members`)."""
    later = ui.later_union_members(ref_id)
    if not later:
        # No member of a union (most settings): nothing to compare the active ones with.
        return False
    later = later & (
        actives if isinstance(actives, set | frozenset) else frozenset(actives)
    )
    return bool(later) and not ui.union_member_covered(ref_id, later)


def _gates_and_sources(ui: DynamicUI, ref_id: str) -> set[str]:
    """The selectors on the way to the param-ref ``ref_id`` (see :func:`_gates_of`), and the
    param-refs their Assigns copy them from: a module placed behind a hidden copy of a shown
    setting is shown by that setting."""
    gates = _gates_of(ui, ref_id)
    sources = {ui.assigned_source(gate) for gate in gates}
    return gates | {source for source in sources if source is not None}


def _refine_pairs(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes] | None,
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

    if cancel is not None and cancel():
        # Cancelled: the result so far, measured already.
        return recovered
    ui: DynamicUI | None = _seeded(application, recovered.values, project_code_page)
    if ui is None:
        return recovered
    current: _Fit | None = _misfit(
        ui, parameter_segments, linked, sizes, property_values
    )
    if current is None:
        return recovered
    values: dict[str, str] = recovered.values
    unknown: list[str] = recovered.unknown
    if fixed is None:
        fixed = _fixed_values(application, ui)
    inferred: _ValueMap = _guessed(ui, values)
    limited = recovered.limited
    budget = None if scale is None else _PAIR_TRIES * scale

    def finish() -> RecoveredParameters:
        assert current is not None
        assert ui is not None
        fit = (
            current[0],
            _compared(ui, current[1]),
            _contradicting_objects(ui, linked, sizes),
        )
        return _with_misfit(values, unknown, fit, ui, limited=limited)

    def alike(gate: str, value: str) -> bool:
        return _alike_for_reads(application, values, project_code_page, gate, value)

    rounds = count() if scale is None else range(_PAIR_ROUNDS * scale)
    try:
        for _ in rounds:
            _check(cancel)
            evaluated = 0
            improved = False
            # Selectors whose value alone leaves as many bytes differing, in fewer bits, with
            # its fewest bits and that value: tried together after the others.
            partial: dict[str, tuple[int, str]] = {}
            current_bits: int | None = None
            for gate, value in _refinement_trials(
                ui, current[1], _contradicting_objects(ui, linked, sizes), cancel
            ):
                if _calculated_from_reads(ui, gate) and not alike(gate, value):
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
                    cancel,
                    fixed=fixed,
                )
                first = _seeded(application, first_values, project_code_page)
                if first is None:
                    continue
                fit = _misfit(first, parameter_segments, linked, sizes, property_values)
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
                if fit is not None and fit[0][:2] == current[0][:2]:
                    if current_bits is None:
                        current_bits = _differing_bits(
                            ui, parameter_segments, current[1]
                        )
                    bits = _differing_bits(first, parameter_segments, fit[1])
                    if bits < current_bits and (
                        gate not in partial or bits < partial[gate][0]
                    ):
                        partial[gate] = (bits, value)
                # The settings the value shows, corrected one after the other: one that
                # brings the result closer to the device (with as many bytes differing, in
                # fewer bits) stays behind the value while the next is tried, and the
                # settings it shows are tried too (a function whose two inputs each fit only
                # with the other one, a button's function whose two assignments do).
                kept: dict[str, str] = {}
                base, base_ui = fit, first
                base_bits: int | None = None
                done: set[str] = {gate}
                while base is not None and not improved:
                    owners = {gate, *kept}
                    children = [
                        (child, parameter)
                        for child, parameter in shown_parameters(base_ui.ui()).items()
                        if child not in done
                        and _inferable(base_ui, child, parameter)
                        and owners & _gates_and_sources(base_ui, child)
                        and not _calculated_from_reads(base_ui, child)
                    ]
                    moved = False
                    for child, parameter in children:
                        done.add(child)
                        now = base_ui.get_value(child) or parameter.default_value
                        # The value of the setting that brings the result closest, of all
                        # it can take: one that fits only a little better is no reason to
                        # leave out one that fits.
                        best: tuple[str, _Fit, DynamicUI, int | None] | None = None
                        for child_value in values_to_try(parameter, now):
                            _check(cancel)
                            if evaluated == budget:
                                limited = True
                                return finish()
                            evaluated += 1
                            chosen = {
                                **inferred,
                                gate: value,
                                **kept,
                                child: child_value,
                            }
                            trial_values, trial_unknown = _decode(
                                application,
                                parameter_segments,
                                property_values,
                                project_code_page,
                                chosen,
                                cancel,
                                fixed=fixed,
                            )
                            seeded = _seeded(
                                application, trial_values, project_code_page
                            )
                            if seeded is None:
                                continue
                            fit = _misfit(
                                seeded,
                                parameter_segments,
                                linked,
                                sizes,
                                property_values,
                            )
                            if fit is None:
                                continue
                            if fit[0] < current[0]:
                                current, ui, values, unknown, inferred = (
                                    fit,
                                    seeded,
                                    trial_values,
                                    trial_unknown,
                                    chosen,
                                )
                                improved = True
                                break
                            trial_bits: int | None = None
                            # As many bytes differing and objects contradicting, whatever
                            # it shows that is not linked: two settings behind settings of
                            # their own share a byte, the first one shown and read alone
                            # leaves the byte differing, in fewer bits.
                            if fit[0][:2] == base[0][:2]:
                                if base_bits is None:
                                    base_bits = _differing_bits(
                                        base_ui, parameter_segments, base[1]
                                    )
                                trial_bits = _differing_bits(
                                    seeded, parameter_segments, fit[1]
                                )
                            closer = fit[0] < base[0] or (
                                trial_bits is not None
                                and base_bits is not None
                                and trial_bits < base_bits
                            )
                            if closer and (
                                best is None
                                or fit[0] < best[1][0]
                                or (
                                    fit[0] == best[1][0]
                                    and trial_bits is not None
                                    and best[3] is not None
                                    and trial_bits < best[3]
                                )
                            ):
                                best = (child_value, fit, seeded, trial_bits)
                        if improved:
                            break
                        if best is not None:
                            kept[child] = best[0]
                            base, base_ui, base_bits = best[1], best[2], best[3]
                            moved = True
                            break
                    if not moved:
                        break
                if improved:
                    break
            if not improved and len(partial) > 1:
                # Selectors each of which alone fits only some bits of the bytes that
                # differ (the switches of several channels whose settings share bytes, a
                # few bits each): taken one after the other, each that fits fewer bits
                # with those before it stays.
                stacked: _ValueMap = dict(inferred)
                stacked_bits: int | None = current_bits
                for gate, (_bits, value) in partial.items():
                    _check(cancel)
                    if evaluated == budget:
                        limited = True
                        return finish()
                    evaluated += 1
                    together: _ValueMap = {**stacked, gate: value}
                    stack_values, stack_unknown = _decode(
                        application,
                        parameter_segments,
                        property_values,
                        project_code_page,
                        together,
                        cancel,
                        fixed=fixed,
                    )
                    stack_ui = _seeded(application, stack_values, project_code_page)
                    if stack_ui is None:
                        continue
                    stack_fit = _misfit(
                        stack_ui, parameter_segments, linked, sizes, property_values
                    )
                    if stack_fit is None:
                        continue
                    if stack_fit[0] < current[0]:
                        current, ui, values, unknown, inferred = (
                            stack_fit,
                            stack_ui,
                            stack_values,
                            stack_unknown,
                            together,
                        )
                        improved = True
                        break
                    if stack_fit[0][:2] == current[0][:2]:
                        bits = _differing_bits(
                            stack_ui, parameter_segments, stack_fit[1]
                        )
                        if stacked_bits is None or bits < stacked_bits:
                            stacked, stacked_bits = together, bits
            if not improved or finish().complete:
                return finish()
        limited = True
    except _Cancelled:
        limited = True
    return finish()


def _refine_fixed(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes] | None,
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

    if not fixed:
        return recovered
    ui = _seeded(application, recovered.values, project_code_page)
    if ui is None:
        return recovered
    # A hidden setting a calculation computes is computed again from each value tried (a
    # channel's function decides which of its settings show), not kept as the result has it.
    inferred = {
        key: value
        for key, value in _guessed(ui, recovered.values).items()
        if not _computed_hidden(ui, key)
    }
    decoded: tuple[_ValueMap, list[str]] | None = None
    if cancel is None or not cancel():
        with contextlib.suppress(_Cancelled):
            decoded = _decode(
                application,
                parameter_segments,
                property_values,
                project_code_page,
                inferred,
                cancel,
                fixed=fixed,
            )
    if decoded is None:
        # Cancelled before or during the decode that validates the result (with the
        # calculations of a large application it takes as long as a step of the search):
        # the result so far with the immutable references at their own values, measured.
        decoded = (
            {
                key: value
                for key, value in recovered.values.items()
                if ui.local_ref_id(key) not in fixed
            },
            list(recovered.unknown),
        )
    values, unknown = decoded
    ui = _seeded(application, values, project_code_page)
    assert ui is not None
    current = _misfit(ui, parameter_segments, linked, sizes, property_values)
    if current is None:
        raise RecoverError("Cannot validate recovered parameters with fixed values")
    limited = recovered.limited
    budget = None if scale is None else _PAIR_TRIES * scale
    rounds = count() if scale is None else range(_PAIR_ROUNDS * scale)
    # Settings of several selectors can share a byte (the bits of two hidden settings with a
    # fixed value each): a value that corrects the bits of one leaves the byte differing until
    # the other is corrected too. Of two results that differ in as many bytes, the one that
    # differs in fewer bits fits better.
    bits: int | None = None

    def better(
        chosen: _ValueMap,
        now: DynamicUI,
        fit_now: tuple[_Score, set[_Cell], set[int]],
    ) -> (
        tuple[
            dict[str, str],
            list[str],
            DynamicUI,
            tuple[_Score, set[_Cell], set[int]],
            int | None,
        ]
        | None
    ):
        """The values decoded with ``chosen`` and the fixed values kept, when they fit better
        than the result so far (``now``, as far from the device as ``fit_now``): the values,
        those left unknown, the UI seeded with them, how far it is from the device and the
        bits it differs in (when as far as the result)."""
        nonlocal bits
        trial_values, trial_unknown = _decode(
            application,
            parameter_segments,
            property_values,
            project_code_page,
            chosen,
            cancel,
            fixed=fixed,
        )
        seeded = _seeded(application, trial_values, project_code_page)
        assert seeded is not None
        fit = _misfit(seeded, parameter_segments, linked, sizes, property_values)
        if fit is None or fit[0] > fit_now[0]:
            return None
        trial_bits: int | None = None
        if fit[0] == fit_now[0]:
            if bits is None:
                bits = _differing_bits(now, parameter_segments, fit_now[1])
            trial_bits = _differing_bits(seeded, parameter_segments, fit[1])
            if trial_bits >= bits:
                return None
        return trial_values, trial_unknown, seeded, fit, trial_bits

    def behind(chosen: _ValueMap) -> _ValueMap | None:
        """``chosen`` with the settings the device keeps no copy of that the inference finds
        behind it (as the search before measures a value): a channel's function shows the
        settings the channel's linked objects need only once they are inferred too."""
        try:
            inferred_values, _unknown, _inferred = _infer_all(
                application,
                parameter_segments,
                property_values,
                project_code_page,
                linked,
                sizes,
                fixed,
                chosen,
                set(),
                cancel,
            )
            seeded = _seeded(application, inferred_values, project_code_page)
        except AllocatorOverflow:
            return None
        if seeded is None:
            return None
        return {**_guessed(seeded, inferred_values), **chosen}

    try:
        for _ in rounds:
            _check(cancel)
            if _nothing_differs(current[0]):
                break
            differing = current[1] & _fixed_cells(
                ui,
                fixed,
                properties=any(_is_property_cell(cell) for cell in current[1]),
            )
            improved = False
            exhausted = False
            evaluated = 0
            # The settings a trial of a calculated selector changes together (see
            # :func:`_joint_values_to_try`): what they compute is no guess of the selector.
            joint: dict[tuple[str, str], _ValueMap] = {}
            trials = _refinement_trials(ui, differing, set(), cancel, joint=joint)
            # The values alone first. When none fits better, each with the settings it shows
            # inferred behind it, taken when it fits better: a value that fits worse alone (a
            # channel's function without the settings that show its linked objects) can fit
            # with them.
            for wide in (False, True):
                for gate, value in trials:
                    change = joint.get((gate, value))
                    if (
                        change is None
                        and _calculated_from_reads(ui, gate)
                        and not _alike_for_reads(
                            application, values, project_code_page, gate, value
                        )
                    ):
                        continue
                    _check(cancel)
                    if evaluated == budget:
                        exhausted = True
                        break
                    evaluated += 1
                    chosen = {**inferred, **(change or {gate: value})}
                    if wide:
                        wider = behind(chosen)
                        if wider is None or wider == chosen:
                            continue
                        chosen = wider
                        _check(cancel)
                    found = better(chosen, ui, current)
                    if found is None:
                        continue
                    trial_values, trial_unknown, seeded, fit, trial_bits = found
                    values, unknown, inferred, ui, current = (
                        trial_values,
                        trial_unknown,
                        chosen,
                        seeded,
                        fit,
                    )
                    bits = trial_bits
                    improved = True
                if improved or exhausted:
                    break
            if exhausted:
                limited = True
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
    # The bytes of properties no download writes are the device's own: no result has to match.
    differing = differing - _unwritten_property_cells(seeded, differing)
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
    property_values: Mapping[TypedPropertyKey, bytes] | None,
    project_code_page: str | None,
    inferred: Mapping[str, str],
    cancel: Callable[[], bool] | None = None,
    *,
    fixed: Mapping[str, str] | None = None,
) -> tuple[_ValueMap, list[str]]:
    """Decode the memory and properties against a UI seeded with ``inferred``, returning the
    values (``inferred`` included) and the parameters left unknown. Raises :class:`_Cancelled`
    between rounds once ``cancel`` says so."""
    # The settings with a fixed value are looked up in the UI as it is seeded, before it is
    # evaluated (which drops the module instances it does not show): a UI of its own then,
    # and one evaluated alike, if there is one, to decode with.
    alike: DynamicUI | None = None
    if not inferred:
        ui = application.dynamic_ui()
    elif fixed is None or fixed:
        ui, alike = _seeded_for_lookups(application, inferred, project_code_page)
    else:
        ui = _seeded(application, inferred, project_code_page)
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
            ui = _seeded(application, inferred, project_code_page)
            assert ui is not None
        elif alike is not None:
            ui = alike
    elif alike is not None:
        ui = alike
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
        # Memory and property parameters are disjoint, so a plain merge is safe.
        decoded = ui.decode_stored_parameters(parameter_segments, property_values or {})
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
        # A setting read before that the layout its value chose now writes over (a switch
        # that shows a member of its union written after it over all of its bits) keeps the
        # value read: the memory holds nothing of it any more, and its default would choose
        # the layout back where the memory gives that value again.
        for ref_id, value in values.items():
            if (
                ref_id not in read
                and ref_id not in fixed
                and ref_id not in inferred
                and _shadowed(ui, ref_id)
            ):
                read[ref_id] = value
        written_over = _written_over_values(
            application,
            ui,
            parameter_segments,
            {**inferred, **read},
            unknown,
            project_code_page,
            property_values or {},
            cancel,
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
        seeded_ui = _seeded(
            application, calculated({**inferred, **values}), project_code_page
        )
        if seeded_ui is None:
            break
        ui = seeded_ui

    # Module-instance parameters: decode against a UI seeded with the just-recovered
    # top-level values so the module instances match the device. The module decoder
    # already returns instance-qualified reference ids, which is what a project
    # stores, so they merge straight into values. Which member of a module union owns a
    # field depends on the instance's own values too (a member shown behind another setting
    # of the instance): decode again with a UI seeded with what was read until nothing
    # changes, as above.
    values = calculated({**inferred, **values})
    top_unknown = unknown
    module_values: _ValueMap = {}
    seen_modules: set[frozenset[tuple[str, str]]] = set()
    for n in range(_DECODE_ROUNDS):
        if n:
            try:
                _check(cancel)
            except _Cancelled as cancelled:
                cancelled.partial = (
                    {**values, **module_values},
                    unknown,
                    dict(inferred),
                )
                raise
        seeded = _seeded(
            application,
            calculated({**values, **module_values}) if module_values else values,
            project_code_page,
        )
        if seeded is None:
            break
        module_read: _ValueMap = {}
        unknown = list(top_unknown)
        for ref_id, value in seeded.decode_module_parameters(
            parameter_segments, property_values or {}
        ).items():
            if seeded.local_ref_id(ref_id) in fixed:
                continue
            if value is None:
                unknown.append(ref_id)
            else:
                module_read[ref_id] = value
        # The settings of the instances the device keeps no copy of that an Assign copies
        # into their stored settings, as for the application's above; and those of the
        # application an Assign of an instance copies into the instance's stored settings
        # (one the values read give already is left as it is).
        module_read.update(_assigned_from(seeded, {**values, **module_read}, inferred))
        # Seeding again changes nothing unless a value the UI was seeded with otherwise
        # decides what it shows.
        if module_read == module_values or not any(
            _steers(seeded, ref_id, value) for ref_id, value in module_read.items()
        ):
            module_values = module_read
            break
        state = frozenset(module_read.items())
        module_values = module_read
        if state in seen_modules:
            break
        seen_modules.add(state)
    values.update(module_values)
    return calculated(values), unknown


def _steers(ui: DynamicUI, ref_id: str, value: str) -> bool:
    """Whether ``value`` of ``ref_id`` would change what ``ui`` shows: it is not the value
    ``ui`` has, and the ref is a selector or an input of a calculation."""
    local = ui.local_ref_id(ref_id)
    indexer = ui.indexer
    return ui.get_value(ref_id) != value and (
        local in ui.selector_param_refs
        or bool(indexer.calculations_for_r(local))
        or bool(indexer.calculations_for_l(local))
    )


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
    the device keeps no copy of, computed from a stored one). A read value is kept. A computed
    value can show a read one (a channel switch computed from another setting): the settings
    that read value is computed from are then computed too. Raises :class:`_Cancelled` once
    ``cancel`` says so. Within a search, computed once for the same values."""
    cache = _search_cache(application)
    if cache is None:
        return _calculate(application, values, project_code_page, cancel)
    key = (frozenset(values.items()), project_code_page)
    if key in cache.calculated:
        return dict(cache.get(cache.calculated, key))
    result = _calculate(application, values, project_code_page, cancel)
    cache.put(cache.calculated, key, dict(result), cache.CALCULATED)
    return result


def _calculate(
    application: Application,
    values: Mapping[str, str],
    project_code_page: str | None,
    cancel: Callable[[], bool] | None,
) -> _ValueMap:
    """:func:`_with_calculated`, computed."""
    from .recover import seed_dynamic_ui

    result = dict(values)
    # Only an active read value is a stored input of the calculations it is an output of: run
    # those again while the values computed show more of them (each round shows more, so this
    # ends).
    stored: set[str] = set()
    refs: list[str] | None = None
    guesses_run = False
    while True:
        # Changed below (the calculations run in it): a UI of its own, evaluated first.
        ui = seed_dynamic_ui(application, result, project_code_page, evaluated=True)
        if ui is None:
            return result
        if cancel is not None:
            ui.script_abort = _CancelToken(cancel)
        if not guesses_run:
            guesses_run = True
            sources, targets = _guessed_sources(ui, values)
            if sources:
                # A calculation with values read on its right side runs from that side below,
                # from the default of a setting on that side the device keeps no copy of (a
                # hidden copy of a shown switch): it is computed from the given settings first,
                # as an edit of them computes it.
                try:
                    changes = ui.recalculate(sources)
                except ScriptAborted:
                    raise _Cancelled from None
                computed = {
                    ref_id: new
                    for ref_id, (_old, new) in changes.items()
                    if new is not None and ref_id in targets
                }
                if computed:
                    result.update(computed)
                    continue
        before = len(stored)
        stored |= ui.active_parameter_ref_ids() & values.keys()
        if refs is not None and len(stored) == before:
            return result
        written: set[str] = set()
        try:
            changes = ui.recalculate(
                _copies_first(ui, list(values) if refs is None else refs),
                stored=True,
                written=written,
            )
        except ScriptAborted:
            raise _Cancelled from None
        for ref_id, (_old, new) in changes.items():
            if new is not None and ref_id not in values:
                result[ref_id] = new
        indexer = ui.indexer
        if _shows_no_more(ui, written, values.keys() - stored):
            return result
        shown = (ui.active_parameter_ref_ids() & values.keys()) - stored
        again = {
            id(calculation)
            for ref_id in shown
            for calculation in indexer.calculations_for_r(ui.local_ref_id(ref_id))
        }
        if not again:
            return result
        refs = [
            ref_id
            for ref_id in values
            if any(
                id(calculation) in again
                for calculation in indexer.calculations_for_r(ui.local_ref_id(ref_id))
            )
        ]


def _shows_no_more(
    ui: DynamicUI, written: Collection[str], others: Collection[str]
) -> bool:
    """Whether the calculations that wrote ``written`` in ``ui`` cannot show a setting of
    ``others`` (the values not active before) that a calculation computes from: they wrote no
    selector (the same parameters are active), or none of ``others`` is such a setting. The
    evaluation that would tell is then left out."""
    kinds = _ref_kinds(ui)
    return not any(kinds.selector(ref_id) for ref_id in written) or not any(
        kinds.computed_from(ref_id) for ref_id in others
    )


def _ref_tail(ref_id: str) -> str:
    """The part of a param-ref id that a module instance's qualified id of it keeps: from its
    parameter (``_P-5_R-5`` of ``..._MD-1_M-2_MI-3_P-5_R-5`` and of ``..._MD-1_P-5_R-5``)."""
    start = max(ref_id.rfind("_P-"), ref_id.rfind("_UP-"))
    return ref_id if start < 0 else ref_id[start:]


@dataclass(frozen=True, slots=True)
class _RefKinds:
    """Which param-refs of an application can decide which parameters are active (a selector:
    the parameter of a choose or the count of a repeat, the source or the target of an assign),
    and which can be on the right side of a parameter calculation (what it computes from) or on
    its left side, told by :func:`_ref_tail`: of a module instance, the tail of its definition's
    ref, whatever the instances there are."""

    selectors: frozenset[str]
    computed: frozenset[str]
    # Those on the left side of a calculation (what it computes).
    left: frozenset[str] = frozenset()

    def selector(self, ref_id: str) -> bool:
        return _ref_tail(ref_id) in self.selectors

    def computed_from(self, ref_id: str) -> bool:
        return _ref_tail(ref_id) in self.computed

    def computing(self, ref_id: str) -> bool:
        return _ref_tail(ref_id) in self.left


def _ref_kinds(ui: DynamicUI) -> _RefKinds:
    """The :class:`_RefKinds` of the application of ``ui`` (of the last few)."""
    from xknxeditor.namespaces.intermediate import Assign, Repeat

    indexer = ui.indexer
    for seen, kinds in _REF_KINDS:
        if seen is indexer:
            return kinds
    selectors = set(ui.selector_param_refs)
    stack: list[object] = [
        getattr(getattr(ui, "_app", None), "dynamic", None),
        *(definition.dynamic for definition in indexer.module_defs.values()),
    ]
    while stack:
        element = stack.pop()
        if element is None:
            continue
        if isinstance(element, Assign):
            selectors.add(element.target_param_ref_ref)
            if element.source_param_ref_ref is not None:
                selectors.add(element.source_param_ref_ref)
            continue
        if isinstance(element, Repeat) and element.parameter_ref_id:
            selectors.add(element.parameter_ref_id)
        when = getattr(element, "when", None)
        if when:
            # A choose: its parameter, and the content of each branch.
            choose = getattr(element, "param_ref_id", None)
            if choose:
                selectors.add(choose)
            stack.extend(when)
        stack.extend(getattr(element, "choice", None) or [])
    kinds = _RefKinds(
        frozenset(_ref_tail(ref) for ref in selectors),
        frozenset(
            _ref_tail(ref)
            for ref in indexer.parameter_refs
            if indexer.calculations_for_r(ref)
        ),
        frozenset(
            _ref_tail(ref)
            for ref in indexer.parameter_refs
            if indexer.calculations_for_l(ref)
        ),
    )
    _REF_KINDS.insert(0, (indexer, kinds))
    del _REF_KINDS[4:]
    return kinds


# The kinds of param-refs of the last few applications (see _ref_kinds).
_REF_KINDS: list[tuple[object, _RefKinds]] = []


def _guessed_sources(
    ui: DynamicUI, values: Mapping[str, str]
) -> tuple[list[str], set[str]]:
    """The settings of ``values`` the device keeps no copy of that are on the left side of a
    calculation with settings on its right side the device keeps no copy of either and that
    ``values`` does not give, and those settings: what they are computed from."""
    indexer = ui.indexer
    kinds = _ref_kinds(ui)
    sources: list[str] = []
    targets: set[str] = set()
    for ref_id in values:
        if not kinds.computing(ref_id):
            # On the left side of no calculation (most settings).
            continue
        local = ui.local_ref_id(ref_id)
        calculations = indexer.calculations_for_l(local)
        if not calculations or not _is_unstored(ui, ref_id):
            continue
        instance = _instance_of(ref_id, local)
        rights = {
            right
            for calculation in calculations
            for right in _qualified(
                [ref.ref_id for ref in calculation.rparameters.parameter_ref_ref],
                instance,
            )
            if right not in values and _is_unstored(ui, right)
        }
        if rights:
            sources.append(ref_id)
            targets |= rights
    return sources, targets


def _copies_first(ui: DynamicUI, refs: list[str]) -> list[str]:
    """``refs`` with those of parameters the device keeps a copy of first. A calculation runs
    from the side of the first of the refs it takes: one with a value read on its right side
    computes its left side from that value, also where a setting it computes the value from
    (an inferred switch) is among the refs too."""
    indexer = ui.indexer

    def no_copy(ref_id: str) -> bool:
        ref = indexer.parameter_refs.get(ui.local_ref_id(ref_id))
        return ref is not None and _keeps_no_copy(indexer.parameters.get(ref.ref_id))

    return sorted(refs, key=no_copy)


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


def _shown_objects_split(
    ui: DynamicUI, sizes: Mapping[int, str], instance: str
) -> tuple[set[int], set[int]]:
    """:func:`_shown_objects` of ``ui`` with ``sizes``, those of other module instances and the
    application's apart from those of the module instance ``instance``."""
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab

    outside: set[int] = set()
    inside: set[int] = set()
    prefix = instance + "_"
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            size = sizes.get(node.number) if sizes else None
            if size is None or node.object_size == size:
                (inside if node.ref_id.startswith(prefix) else outside).add(node.number)
        elif isinstance(node, UiTab | UiParameterBlock):
            stack.extend(node.children)
    return outside, inside


class _Evaluated(NamedTuple):
    """A UI a value of a module instance's setting was tried in for the memory (see
    :func:`_infer_from_memory`), with what it was seeded with: its module instances, the
    values of the application's scope and those of the value's instance."""

    instances: frozenset[tuple[str, str]]
    shared: Mapping[str, str]
    own: Mapping[str, str]
    ui: DynamicUI


def _top_instance(key: str, ids: Collection[str]) -> str | None:
    """The module instance of ``ids`` the application places itself that the (qualified) id
    ``key`` belongs to, or is; None when it belongs to none of them."""
    start = key.find("_MI-")
    if start < 0:
        return None
    end = key.find("_", start + 1)
    prefix = key if end < 0 else key[:end]
    return prefix if prefix in ids else None


class _InstanceValues:
    """Values split into those of each module instance the application places itself (with
    its instances' values) and the others (the application's scope), as a UI seeded with
    them makes the module instances ``instances``. Drops what ``evaluated`` holds that was
    seeded with other instances or other values of the application's scope."""

    def __init__(
        self,
        values: Mapping[str, str],
        instances: Sequence[tuple[str, str]],
        evaluated: dict[tuple[str, str], _Evaluated],
    ) -> None:
        self.instances = frozenset(instances)
        self.ids = {instance_id for instance_id, _ in self.instances}
        self.shared: _ValueMap = {}
        self.own: dict[str, _ValueMap] = {}
        for key, value in values.items():
            instance = _top_instance(key, self.ids)
            if instance is None:
                self.shared[key] = value
            else:
                self.own.setdefault(instance, {})[key] = value
        # The instances with instances of their own.
        self.nested = {
            top
            for instance_id in self.ids
            if (top := _top_instance(instance_id, self.ids)) not in (None, instance_id)
        }
        alike: dict[int, bool] = {}
        for key, entry in list(evaluated.items()):
            same = alike.get(id(entry.shared))
            if same is None:
                same = alike[id(entry.shared)] = entry.shared == self.shared
            if same and entry.instances == self.instances:
                evaluated[key] = entry._replace(shared=self.shared)
            else:
                del evaluated[key]

    def instance(self, ref_id: str) -> str | None:
        """The module instance the setting ``ref_id`` belongs to, when the application places
        it itself, it has no instances of its own and values of it are given; None otherwise.
        A UI seeded with the values places the instances with values in the order the values
        give them first, then those it makes: with a value of such an instance added, it
        places them as it does without."""
        instance = _top_instance(ref_id, self.ids)
        if instance is None or instance in self.nested or instance not in self.own:
            return None
        return instance


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


def _inferable(ui: DynamicUI, ref_id: str, parameter: UiParameter) -> bool:
    """Whether the search may try other values of a shown setting: the device keeps no copy
    of it (see :func:`_is_unstored`) and the user can change it. A read-only one keeps the
    value the product gives it."""
    return _editable(parameter) and _is_unstored(ui, ref_id)


def _editable(parameter: UiParameter) -> bool:
    """Whether the user can change a shown setting (a read-only one keeps its value)."""
    from xknxeditor.namespaces.intermediate.access_t import Access

    return parameter.access is Access.READ_WRITE


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
    program = getattr(ui, "_app", None)
    hidden: frozenset[str] = (
        _hidden_fixed(program, ui) if program is not None else frozenset()
    )
    actives = ui.active_parameter_ref_ids()
    by_parameter: dict[str, list[tuple[tuple[int, int], str]]] = {}
    # Only the refs that can be shadowed at all (a parameter with a hidden ref with a fixed
    # value among others, a union member with later ones): of thousands of active refs, few.
    for active in actives & _shadow_candidates(ui, hidden):
        ref = indexer.parameter_refs.get(active)
        if ref is not None and ref.ref_id:
            by_parameter.setdefault(ref.ref_id, []).append(
                (parameter_ref_order(active, ref.tag), active)
            )
    found: set[str] = set()
    for refs in by_parameter.values():
        ordered = [active for _order, active in sorted(refs)]
        for n, active in enumerate(ordered):
            if any(later in hidden for later in ordered[n + 1 :]):
                found.add(active)
    for refs in by_parameter.values():
        for _order, active in refs:
            # Bits no later member writes still hold this one's.
            later = ui.later_union_members(active) & actives
            if later and ui.union_member_covered(active, later):
                found.add(active)
    _SHADOWED.insert(0, (ui, frozenset(found)))
    del _SHADOWED[8:]
    return ref_id in found


def _shadow_candidates(ui: DynamicUI, hidden: frozenset[str]) -> frozenset[str]:
    """The param-refs of the application of ``ui`` that :func:`_shadowed` can find: of a
    parameter with several refs, one of them in ``hidden``, and union members with later
    members (of the last few applications)."""
    indexer = ui.indexer
    for seen, seen_hidden, candidates in _SHADOW_CANDIDATES:
        if seen is indexer and seen_hidden is hidden:
            return candidates
    refs_of: dict[str, list[str]] = {}
    for ref_id, ref in indexer.parameter_refs.items():
        if ref.ref_id:
            refs_of.setdefault(ref.ref_id, []).append(ref_id)
    found: set[str] = set()
    for refs in refs_of.values():
        if len(refs) > 1 and any(ref_id in hidden for ref_id in refs):
            found.update(refs)
    found.update(
        ref_id for ref_id in indexer.parameter_refs if ui.later_union_members(ref_id)
    )
    candidates = frozenset(found)
    _SHADOW_CANDIDATES.insert(0, (indexer, hidden, candidates))
    del _SHADOW_CANDIDATES[4:]
    return candidates


# The refs _shadowed can find, of the last few applications (see _shadow_candidates).
_SHADOW_CANDIDATES: list[tuple[object, frozenset[str], frozenset[str]]] = []


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
        # A module instance's are qualified with the instance: the indexer knows them by
        # their local ids.
        target_ref = indexer.parameter_refs.get(ui.local_ref_id(target))
        source_ref = indexer.parameter_refs.get(ui.local_ref_id(source))
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
    properties: Mapping[TypedPropertyKey, bytes],
    cancel: Callable[[], bool] | None = None,
) -> _ValueMap:
    """The values of the active shown union members that a later active member writes over
    in part (see :meth:`DynamicUI.later_union_members`), read as no value of theirs: the
    value whose bytes, written in ``ui`` with ``values``, are those of ``segments`` (memory)
    and ``properties`` (property members). A later member the value itself shows (a switch
    whose "yes" shows an option stored in one of its bits) is written with the value the
    memory holds for it. A member with no byte the device was read at stays unknown. Raises
    :class:`_Cancelled` once ``cancel`` says so."""
    if not unknown:
        return {}
    from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try

    lost = set(unknown)
    actives = ui.active_parameter_ref_ids()
    shown: Mapping[str, UiParameter] | None = None
    cells: Mapping[_Cell, Collection[str]] = {}
    property_cells: Mapping[tuple[TypedPropertyKey, int], Collection[str]] = {}
    found: _ValueMap = {}
    for ref_id in actives:
        ref = ui.indexer.parameter_refs.get(ref_id)
        if ref is None or ref.ref_id not in lost:
            continue
        later = ui.later_union_members(ref_id)
        if not later or ui.union_member_covered(ref_id, later):
            continue
        if shown is None:
            shown = shown_parameters(ui.ui())
            cells = ui.stored_parameter_cells()
            property_cells = ui.stored_property_layout().cells
        parameter = shown.get(ref_id)
        if parameter is None:
            continue
        own = [
            cell
            for cell, refs in cells.items()
            if ref_id in refs and cell[1] < len(segments.get(cell[0], b""))
        ]
        own_properties = [
            cell
            for cell, refs in property_cells.items()
            if ref_id in refs and cell[1] < len(properties.get(cell[0], b""))
        ]
        if not own and not own_properties:
            # Nothing the device was read at holds a bit of it: no value can be told.
            continue
        # Several values may fit the bits left; the one already given (a search's trial)
        # stays when it fits.
        given = values.get(ref_id)
        candidates = values_to_try(parameter, "")
        if given is not None:
            candidates = [given, *(value for value in candidates if value != given)]
        for value in candidates:
            _check(cancel)
            trial = {**values, ref_id: value}
            seeded = _seeded(application, trial, project_code_page)
            if seeded is None:
                continue
            if not _written_over_in_part(
                seeded, ref_id, seeded.active_parameter_ref_ids()
            ):
                continue
            behind = _later_members_read(seeded, ref_id, segments, trial)
            if behind:
                seeded = _seeded(application, {**trial, **behind}, project_code_page)
                if seeded is None:
                    continue
            memory = seeded.encode_to_memory()
            written = seeded.encode_to_typed_properties() if own_properties else {}
            if all(
                memory.get(segment, b"")[offset : offset + 1]
                == segments[segment][offset : offset + 1]
                for segment, offset in own
            ) and all(
                written.get(key, b"")[offset : offset + 1]
                == properties[key][offset : offset + 1]
                for key, offset in own_properties
            ):
                found[ref_id] = value
                break
    return found


def _later_members_read(
    ui: DynamicUI, ref_id: str, segments: Mapping[str, bytes], values: Mapping[str, str]
) -> _ValueMap:
    """The values the memory holds for the active later members of ``ref_id``'s union (see
    :meth:`DynamicUI.later_union_members`) that ``values`` gives none."""
    later = ui.later_union_members(ref_id) & ui.active_parameter_ref_ids()
    missing = [ref for ref in sorted(later) if ref not in values]
    if not missing:
        return {}
    decoded = ui.decode_stored_parameters(segments, {})
    read: _ValueMap = {}
    for ref in missing:
        local = ui.indexer.parameter_refs.get(ui.local_ref_id(ref))
        value = decoded.get(local.ref_id) if local is not None else None
        if value is not None:
            read[ref] = value
    return read


def _keeps_no_copy(parameter: object) -> bool:
    """Whether a parameter has no memory, no property and is no member of a union. One
    kept as an IoT point has neither: a memory and property read does not give it."""
    from xknxeditor.namespaces.intermediate.io_tpoint_parameter_t import (
        IoPointParameter,
    )

    storage = getattr(parameter, "choice", None)
    return isinstance(parameter, _plain_parameter_types()) and (
        storage is None or isinstance(storage, IoPointParameter)
    )


def _plain_parameter_types() -> tuple[type, ...]:
    """The types of a parameter that is no member of a union (asked for again and again: the
    imports looked up once)."""
    if not _PLAIN_PARAMETER_TYPES:
        from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_parameter import (
            ApplicationProgramStaticParametersParameter,
        )
        from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_parameter import (
            ModuleDefStaticParametersParameter,
        )

        _PLAIN_PARAMETER_TYPES.extend(
            (
                ApplicationProgramStaticParametersParameter,
                ModuleDefStaticParametersParameter,
            )
        )
    return tuple(_PLAIN_PARAMETER_TYPES)


_PLAIN_PARAMETER_TYPES: list[type] = []


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


def _fixed_cells(
    ui: DynamicUI, fixed: _ValueMap, *, properties: bool = False
) -> set[_Cell]:
    """The bytes the active hidden settings with a fixed value (see :func:`_fixed_values`) take
    in ``ui``, also the ones they share with other settings (a few bits of a byte each); with
    ``properties`` those of properties too."""
    indexer = ui.indexer
    active_refs = ui.active_parameter_ref_ids()
    active = {ui.local_ref_id(ref_id) for ref_id in active_refs}
    parameters = {indexer.parameter_refs[ref].ref_id for ref in active & fixed.keys()}
    # A parameter another active reference of writes (an assign, a calculation) is left out.
    parameters -= {
        indexer.parameter_refs[ref].ref_id
        for ref in active - fixed.keys()
        if ref in indexer.parameter_refs
    }
    if not parameters:
        return set()
    refs = {
        ref_id
        for ref_id in active_refs
        if (local := ui.local_ref_id(ref_id)) in fixed
        and indexer.parameter_refs[local].ref_id in parameters
    }
    cells = {
        cell for cell, owners in ui.stored_parameter_cells().items() if owners & refs
    }
    if properties:
        # Those stored in a property, as cells of their own (see _property_cell).
        cells |= {
            cell for cell, owners in _property_owners(ui).items() if owners & refs
        }
    return cells


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


def _differing_bits(
    ui: DynamicUI, parameter_segments: Mapping[str, bytes], cells: Collection[_Cell]
) -> int:
    """How many bits of the bytes ``cells`` the device holds otherwise than ``ui`` writes them."""
    from xknxeditor.prod.errors import EncodingError

    if not cells:
        return 0
    try:
        encoded = ui.encode_to_memory()
    except (EncodingError, ValueError):
        return 8 * len(cells)
    count = 0
    for segment_id, offset in cells:
        device = parameter_segments.get(segment_id, b"")
        mine = encoded.get(segment_id, b"")
        if offset < min(len(device), len(mine)):
            count += (device[offset] ^ mine[offset]).bit_count()
    return count


def _undecodable_cells(
    ui: DynamicUI, parameter_segments: Mapping[str, bytes], own: set[str]
) -> int:
    """How many bytes the parameters ``own`` take in ``ui`` whose memory is no value of their
    type: the device holds another setting there. A byte one of them shares with another
    setting (a few bits each) counts too."""
    if not own:
        return 0
    decoded = ui.decode_memory_parameters(parameter_segments)
    failed = {
        parameter_id for parameter_id in own if decoded.get(parameter_id, "") is None
    }
    if not failed:
        return 0
    return sum(1 for owners in ui.written_parameter_cells().values() if owners & failed)


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
        # Every parameter written, also one that shares all of its bytes with others.
        written[key] = set[str]().union(*ui.written_parameter_cells().values())
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

    ui = _seeded(application, values, project_code_page)
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
        seeded = _seeded(
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
            and _inferable(seeded, ref_id, parameter)
            for ref_id, parameter in shown_parameters(seeded.ui()).items()
        )
        return objects & linked, len(objects - linked), reveals

    def clashes(trials: Mapping[str, Mapping[str, str]]) -> dict[str, int]:
        """For trials that show the same objects, how many bytes of the hidden settings with a
        fixed value the device holds otherwise (see :func:`_fixed_clashes`)."""
        assert fixed is not None and segments is not None
        uis: dict[str, DynamicUI] = {}
        for key, trial in trials.items():
            _check(cancel)
            seeded = _seeded(
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
            or not _inferable(ui, ref_id, parameter)
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
            _check(cancel)
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
        _check(cancel)
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
    property_values: Mapping[TypedPropertyKey, bytes] | None,
) -> dict[str, str | None]:
    """One decode pass over the memory and properties against ``ui``: top-level parameters
    by id, module parameters by their instance's ref, ``None`` where the bytes are no value
    of the type."""
    decoded = ui.decode_stored_parameters(parameter_segments, property_values or {})
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
            return _refs_of_parameters(indexer).get(key, [])
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
    property_values: Mapping[TypedPropertyKey, bytes] | None,
    project_code_page: str | None,
    values: Mapping[str, str],
    unknown: Collection[str],
    linked: set[int],
    sizes: Mapping[int, str],
    cancel: Callable[[], bool] | None = None,
    evaluated: dict[tuple[str, str], _Evaluated] | None = None,
) -> _ValueMap:
    """A value for a shown setting the device keeps no copy of that leads to a member of a
    union whose bytes are no value of its type, chosen so fewer union members do so (and no
    smaller share fits) without hiding a linked object: an inactive channel, whose memory
    holds what an inactive channel writes, is not taken for an active one. The settings that
    lead to the most such members are tried first, ``_MEMORY_TRIES`` values at most. Empty
    when no value does better, or when every union member's bytes fit.

    ``evaluated`` keeps the UIs a value of a module instance's setting is tried in, from one
    call to the next: a value tried before with the same values of the application's scope
    and of its own instance is not evaluated again (only the values of other instances
    differ, whose evaluation it does not change).
    """
    from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try

    if not unknown:
        return {}
    ui = _seeded(application, values, project_code_page)
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
        if (
            ref_id in values
            or local not in weight
            or not _inferable(ui, ref_id, parameter)
        ):
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
    split = (
        None
        if evaluated is None
        else _InstanceValues(values, ui.get_module_instances(), evaluated)
    )
    for _, ref_id, candidate in candidates[:_MEMORY_TRIES]:
        _check(cancel)
        instance = None if split is None else split.instance(ref_id)
        own: _ValueMap = {}
        if split is not None and instance is not None:
            own = {**split.own[instance], ref_id: candidate}
        before = evaluated.get((ref_id, candidate)) if evaluated is not None else None
        if (
            split is not None
            and instance is not None
            and before is not None
            and before.own == own
        ):
            # Tried in a round before, seeded alike but for other instances' values: that
            # UI's instance in this round's UI is what seeding this round's values with the
            # value evaluates to (see DynamicUI.decode_parameters_with_instance).
            outside, _ = _shown_objects_split(ui, sizes, instance)
            _, inside = _shown_objects_split(before.ui, sizes, instance)
            if not base <= outside | inside:
                continue
            decoded = ui.decode_parameters_with_instance(
                parameter_segments, property_values or {}, instance, before.ui
            )
            assert decoded is not None
            trial_fitting, trial_misfits = _union_fit(ui, decoded)
        else:
            seeded = _seeded(
                application, {**values, ref_id: candidate}, project_code_page
            )
            assert seeded is not None
            try:
                shown = _shown_objects(seeded, sizes)
                if (
                    split is not None
                    and evaluated is not None
                    and instance is not None
                    and frozenset(seeded.get_module_instances()) == split.instances
                ):
                    evaluated[(ref_id, candidate)] = _Evaluated(
                        split.instances, split.shared, own, seeded
                    )
                if not base <= shown:
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
            if not fewest:
                # Every union member fits: no value after it can do better.
                break
    return best


def _same_value(a: str, b: str) -> bool:
    """Whether two values of a setting are the same: as written, or as numbers."""
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def _gate_holds(
    ui: DynamicUI, gate: Gate, shown: Collection[str], active: Collection[str]
) -> bool:
    """Whether ``ui`` takes the branch the gate ``gate`` stands for (see
    :class:`~xknxeditor.prod.parser_v2.context.Gate`)."""
    from xknxeditor.prod.parser_v2.nodes.choose import satisfies

    if gate.needs_active and gate.param not in shown and gate.param not in active:
        return False
    value = ui.get_value(gate.param) or ""
    if gate.when is not None:
        return satisfies(gate.when, value)
    return not any(satisfies(test, value) for test in gate.unless)


def _infer_from_fixed(
    application: Application,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes] | None,
    project_code_page: str | None,
    values: Mapping[str, str],
    linked: set[int],
    sizes: Mapping[int, str],
    fixed: _ValueMap,
    cancel: Callable[[], bool] | None = None,
) -> _ValueMap:
    """Values for shown settings the device keeps no copy of, read from the hidden settings
    with a fixed value (see :func:`_fixed_values`) their branches hold: a datapoint's type
    picks one of the references of a hidden setting the device stores (or one of the members
    of a union), each with its own value, so the value the memory holds names the branch, and
    with it the type.

    A setting whose branches' hidden settings the memory holds otherwise than its value now
    writes them takes the value whose branch has the most of them with the value held and
    none with another. Where several values have as many (the same value for several types),
    the one whose group objects have the sizes the device's table gives, when only one does.
    Taken together when they make fewer bytes of the hidden settings differ from the memory
    and hide no linked group object shown now; empty otherwise, or when nothing differs.
    ``values`` are decoded with no fixed values kept (each reference of a hidden setting of a
    module instance has the value the device holds); the application's own hidden settings
    are each read as if they were the one written.
    """
    from xknxeditor.prod.errors import EncodingError
    from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try
    from xknxeditor.prod.parser_v2.nodes.choose import satisfies, tested_values

    if not fixed:
        return {}
    seeded = _seeded(application, values, project_code_page)
    assert seeded is not None
    # The hidden settings at the values the product fixes, as a download writes them.
    clean = {
        key: value
        for key, value in values.items()
        if seeded.local_ref_id(key) not in fixed
    }
    ui = _seeded(application, clean, project_code_page)
    assert ui is not None
    indexer = ui.indexer
    shown = shown_parameters(ui.ui())
    gating = {gate for local in fixed for gate in ui.parameter_gates(local)}
    selectors = {
        ref_id: parameter
        for ref_id, parameter in shown.items()
        if ref_id not in values
        and ui.local_ref_id(ref_id) in gating
        and _inferable(ui, ref_id, parameter)
    }
    if not selectors:
        return {}
    _check(cancel)
    try:
        written = ui.encode_to_memory()
    except (EncodingError, ValueError, AllocatorOverflow):
        return {}
    held = _held_values(application, ui, parameter_segments, property_values)
    cells_of: dict[str, set[_Cell]] = {}
    stored = ui.stored_parameter_cells()
    for cell, owners in stored.items():
        for owner in owners:
            cells_of.setdefault(owner, set()).add(cell)

    def differs(cell: _Cell) -> bool:
        device = parameter_segments.get(cell[0], b"")
        mine = written.get(cell[0], b"")
        offset = cell[1]
        return offset < min(len(device), len(mine)) and device[offset] != mine[offset]

    if not any(
        differs(cell) and any(ui.local_ref_id(owner) in fixed for owner in owners)
        for cell, owners in stored.items()
    ):
        # No byte of a hidden setting with a fixed value differs: no selector has a branch
        # to correct (the placements below are not looked for).
        return {}
    _check(cancel)
    active = ui.active_parameter_ref_ids()
    # Per selector and value, how many of the hidden settings its branch holds have the value
    # held and how many another; and the selectors whose branches' bytes differ.
    evidence: dict[str, dict[str, list[int]]] = {}
    differing: set[str] = set()
    for ref_id, gates in _explored(application, ui, cancel):
        local = ui.local_ref_id(ref_id)
        own = fixed.get(local)
        if own is None or not gates:
            continue
        inner = gates[-1]
        test = inner.when
        parameter = selectors.get(inner.param)
        if parameter is None or not all(
            _gate_holds(ui, gate, shown, active) for gate in gates[:-1]
        ):
            continue
        value = (
            held.get(indexer.parameter_refs[local].ref_id)
            if ref_id == local
            else values.get(ref_id)
        )
        if value is None:
            continue
        if any(differs(cell) for cell in cells_of.get(ref_id, ())):
            differing.add(inner.param)
        current = ui.get_value(inner.param) or parameter.default_value
        # The values to try and the value now (counted too: one that fits as well is kept).
        allowed = [
            *values_to_try(parameter, current, ui.tested_values(inner.param)),
            current,
        ]
        # A default branch takes every value its choose tests for nothing else.
        taken = (
            [name for name in tested_values(test) if satisfies(test, name)]
            if test is not None
            else [
                name
                for name in allowed
                if not any(satisfies(other, name) for other in inner.unless)
            ]
        )
        counts = evidence.setdefault(inner.param, {})
        for name in taken:
            if name in allowed:
                tally = counts.setdefault(name, [0, 0])
                tally[0 if _same_value(value, own) else 1] += 1
    candidates: dict[str, set[str]] = {}
    for selector in differing:
        fitting = {
            name: fit
            for name, (fit, misfit) in evidence.get(selector, {}).items()
            if fit and not misfit
        }
        if not fitting:
            continue
        most = max(fitting.values())
        best = {name for name, fit in fitting.items() if fit == most}
        current = ui.get_value(selector) or selectors[selector].default_value
        if current not in best:
            candidates[selector] = best
    chosen: _ValueMap = {
        selector: next(iter(names))
        for selector, names in candidates.items()
        if len(names) == 1
    }
    ties = {selector: names for selector, names in candidates.items() if len(names) > 1}
    if ties and sizes:
        chosen.update(
            _sized(application, clean, project_code_page, sizes, ties, cancel)
        )
    if not chosen:
        return {}
    _check(cancel)
    try:
        trial = _seeded(application, {**clean, **chosen}, project_code_page)
        assert trial is not None
        if not _shown_objects(ui, sizes) & linked <= _shown_objects(trial, sizes):
            return {}
        cells = _fixed_cells(ui, fixed) | _fixed_cells(trial, fixed)
        before = _mismatches(ui, parameter_segments, cells)
        after = _mismatches(trial, parameter_segments, cells)
    except AllocatorOverflow:
        # More than the product has room for: no configuration of the device.
        return {}
    return chosen if after < before else {}


def _explored(
    application: Application,
    ui: DynamicUI,
    cancel: Callable[[], bool] | None = None,
) -> list[tuple[str, tuple[Gate, ...]]]:
    """Every place of every parameter in ``ui``'s tree with the gates leading there (see
    :meth:`DynamicUI.explore_parameters`): its ref id and the gates. Within a search, explored
    once for the UIs explored alike (see :meth:`DynamicUI.explore_layout`): exploring a large
    application takes far longer than evaluating it, and the values a search tries seldom
    change where an explore pass goes. Raises :class:`_Cancelled` once ``cancel`` says so,
    also while exploring."""
    cache = _search_cache(application)
    layout: tuple[object, ...] = ()
    if cache is not None:
        layout = ui.explore_layout()
        for n, (seen, placements) in enumerate(cache.explored):
            if seen == layout:
                cache.explored.insert(0, cache.explored.pop(n))
                return placements
    placements = [
        (placement.parameter.ref_id, placement.gates)
        for placement in ui.explore_parameters(
            None if cancel is None else lambda: _check(cancel)
        )
    ]
    if cache is not None:
        cache.explored.insert(0, (layout, placements))
        del cache.explored[cache.EXPLORED :]
    return placements


# The stored settings of the last device read, decoded each on its own (see _held_values).
_HELD: list[tuple[object, object, object, dict[str, str | None]]] = []


def _held_values(
    application: Application,
    ui: DynamicUI,
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes] | None,
) -> dict[str, str | None]:
    """Every stored setting of the application (not those of module instances) decoded from
    the device as if it were the one written, the members of a union each on its own:
    parameter id -> value (None: the bytes are no value of its type). The last device read
    is kept: a search asks again and again."""
    from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_union import (
        ApplicationProgramStaticParametersUnion,
    )
    from xknxeditor.prod.parser_v2.encode import (
        collect_writes,
        decode_memory_parameters,
        decode_property_parameters,
    )

    program = application.program
    segments = dict(parameter_segments)
    properties = dict(property_values or {})
    for seen, seen_segments, seen_properties, held in _HELD:
        if (
            seen is program
            and seen_segments == segments
            and seen_properties == properties
        ):
            return held
    parameters = program.static.parameters
    members = {
        member.id: member.value or ""
        for item in (parameters.choice if parameters is not None else [])
        if isinstance(item, ApplicationProgramStaticParametersUnion)
        for member in item.parameter
    }
    writes = collect_writes(program, ui.indexer, members, None)
    held = decode_memory_parameters(
        program, ui.indexer, parameter_segments, None, ui.text_encoding, writes=writes
    )
    if property_values:
        held.update(
            decode_property_parameters(
                program,
                ui.indexer,
                property_values,
                None,
                ui.text_encoding,
                writes=writes,
            )
        )
    _HELD[:] = [(program, segments, properties, held)]
    return held


def _sized(
    application: Application,
    values: Mapping[str, str],
    project_code_page: str | None,
    sizes: Mapping[int, str],
    ties: Mapping[str, set[str]],
    cancel: Callable[[], bool] | None,
) -> _ValueMap:
    """Of the values ``ties`` names for each selector, the one with which no group object it
    shows has another size than the device's table gives (``sizes``), when only one has none.
    The selectors take each value together (one UI per value)."""
    good: dict[str, set[str]] = {}
    for value in sorted({value for names in ties.values() for value in names}):
        _check(cancel)
        trial = {selector: value for selector, names in ties.items() if value in names}
        try:
            seeded = _seeded(application, {**values, **trial}, project_code_page)
            assert seeded is not None
            wrong = _shown_objects(seeded) - _shown_objects(seeded, sizes)
        except AllocatorOverflow:
            continue
        objects_of = _objects_by_number(seeded) if wrong else {}
        blamed: set[str] = set()
        for number in wrong:
            for ref_id in objects_of.get(number, ()):
                blamed |= _gates_of(seeded, ref_id, seeded.object_gates)
        for selector in trial:
            if selector not in blamed:
                good.setdefault(selector, set()).add(value)
    return {
        selector: next(iter(names))
        for selector, names in good.items()
        if len(names) == 1
    }
