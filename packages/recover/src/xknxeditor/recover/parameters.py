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
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xknxeditor.prod import Application


@dataclass(frozen=True, slots=True)
class RecoveredParameters:
    """Recovered parameter reference values plus the parameters left unknown."""

    # parameter reference id -> recovered value.
    values: dict[str, str]
    # parameter ids whose value could not be reconstructed from bytes.
    unknown: list[str]


# Decoding rounds at most: each one can only change which alternatives own memory.
_DECODE_ROUNDS = 8


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
) -> RecoveredParameters:
    """Recover parameter reference values from a device's memory and properties.

    ``parameter_segments`` is ``{segment_id: bytes}`` and ``property_values`` is
    ``{(object_index, property_id, occurrence): bytes}``, both as read off the
    device (see :mod:`xknxeditor.recover.read_config`). ``project_code_page`` is the
    ``CodePage`` of the project the device goes into (``None``: the system language),
    which some applications encode their text parameters with. Returns the recovered value
    for every parameter reference whose type is reconstructable, and the parameter
    ids whose value is unknown (lossy types / absent data).
    """
    from .recover import seed_dynamic_ui

    ui = application.dynamic_ui()
    if ui is None:
        return RecoveredParameters(values={}, unknown=[])
    ui.project_code_page = project_code_page
    parameter_to_refs = _parameter_to_refs(application)
    values: dict[str, str] = {}
    unknown: list[str] = []
    # Which parameters own a memory field depends on the values (the active alternative of
    # a union): decode again with a UI seeded with what was read until nothing changes.
    for _ in range(_DECODE_ROUNDS):
        decoded = dict(ui.decode_memory_parameters(parameter_segments))
        if property_values:
            # Memory and property parameters are disjoint, so a plain merge is safe.
            decoded.update(ui.decode_property_parameters(property_values))
        read: dict[str, str] = {}
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
        seeded_ui = seed_dynamic_ui(application, values, project_code_page)
        if seeded_ui is None:
            break
        ui = seeded_ui

    # Module-instance parameters: decode against a UI seeded with the just-recovered
    # top-level values so the module instances match the device. The module decoder
    # already returns instance-qualified reference ids, which is what a project
    # stores, so they merge straight into values.
    seeded = seed_dynamic_ui(application, values, project_code_page)
    if seeded is not None:
        for ref_id, value in seeded.decode_module_parameters(
            parameter_segments, property_values or {}
        ).items():
            if value is None:
                unknown.append(ref_id)
            else:
                values[ref_id] = value
    return RecoveredParameters(values=values, unknown=unknown)
