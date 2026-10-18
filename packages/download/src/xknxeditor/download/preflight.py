"""Read-only pre-flight check: what a download would change on the device.

Before writing anything, walk the resolved Load Procedure and, for every control
that would write, read the device's current bytes and compare them against the
data the download would write. Nothing is written and no load state is changed -
only reads (memory, properties, table references, object lookup) happen, plus the
procedure's own compare controls, which act as a fingerprint gate.

The resulting :class:`PreflightReport` lists, per memory segment and per property,
how many bytes would change (and the changed ranges), so an operator can confirm a
download does what they expect before committing it to hardware.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ByteRange:
    """A contiguous run of changed bytes within a diff, by offset from its start."""

    start: int
    length: int


def _changed_ranges(current: bytes, planned: bytes) -> tuple[ByteRange, ...]:
    """Return the contiguous runs where ``planned`` differs from ``current``.

    The device's length is authoritative: beyond it, a planned octet counts as a
    change only when it is non-zero. Trailing zero padding (a property carried at
    its maximum element size while the device stores a shorter value) is therefore
    not reported as a change. Memory diffs read the same length and are unaffected.
    """
    ranges: list[ByteRange] = []
    run_start: int | None = None
    for offset in range(len(planned)):
        if offset < len(current):
            differs = current[offset] != planned[offset]
        else:
            differs = planned[offset] != 0
        if differs and run_start is None:
            run_start = offset
        elif not differs and run_start is not None:
            ranges.append(ByteRange(run_start, offset - run_start))
            run_start = None
    if run_start is not None:
        ranges.append(ByteRange(run_start, len(planned) - run_start))
    return tuple(ranges)


@dataclass(frozen=True, slots=True)
class SegmentDiff:
    """The current versus planned bytes for one memory write."""

    address: int
    current: bytes
    planned: bytes

    @property
    def changed_ranges(self) -> tuple[ByteRange, ...]:
        """Contiguous runs (offset from ``address``) that would change."""
        return _changed_ranges(self.current, self.planned)

    @property
    def changed_bytes(self) -> int:
        """Number of bytes that would change."""
        return sum(r.length for r in self.changed_ranges)

    @property
    def changed(self) -> bool:
        """Whether this write would change anything."""
        return self.changed_bytes > 0


@dataclass(frozen=True, slots=True)
class PropertyDiff:
    """The current versus planned bytes for one property write."""

    object_index: int
    property_id: int
    current: bytes
    planned: bytes
    # Set for a property addressed by object type over the extended property services.
    object_type: int | None = None

    @property
    def object_label(self) -> str:
        """The object as shown: its index, or ``type N`` when addressed by type."""
        if self.object_type is not None:
            return f"type {self.object_type}"
        return str(self.object_index)

    @property
    def changed_ranges(self) -> tuple[ByteRange, ...]:
        """Contiguous runs (element offset) that would change."""
        return _changed_ranges(self.current, self.planned)

    @property
    def changed_bytes(self) -> int:
        """Number of bytes that would change."""
        return sum(r.length for r in self.changed_ranges)

    @property
    def changed(self) -> bool:
        """Whether this write would change anything."""
        return self.changed_bytes > 0


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """The full set of changes a download would make, without making them."""

    segments: tuple[SegmentDiff, ...]
    properties: tuple[PropertyDiff, ...]
    # Absolute memory addresses whose device value changed between two reads during the
    # preview, i.e. the firmware is writing them itself at runtime. A difference there is
    # not a real configuration change and should be classified as device-managed.
    device_written: frozenset[int] = frozenset()

    @property
    def changed_segments(self) -> tuple[SegmentDiff, ...]:
        """The memory writes that would actually change bytes."""
        return tuple(s for s in self.segments if s.changed)

    @property
    def changed_properties(self) -> tuple[PropertyDiff, ...]:
        """The property writes that would actually change bytes."""
        return tuple(p for p in self.properties if p.changed)

    @property
    def total_changed_bytes(self) -> int:
        """Total number of bytes that would change across all writes."""
        return sum(s.changed_bytes for s in self.segments) + sum(
            p.changed_bytes for p in self.properties
        )

    @property
    def has_changes(self) -> bool:
        """Whether the download would change anything on the device."""
        return self.total_changed_bytes > 0

    def summary(self) -> str:
        """Return a human readable, multi-line summary of the pending changes.

        Only locations that would actually change are listed; unchanged segments and
        properties are collapsed into a trailing count rather than enumerated, so the
        output stays compact even on a device with many memory segments.
        """
        changed_segments = self.changed_segments
        changed_properties = self.changed_properties
        lines = [
            f"Pre-flight: {self.total_changed_bytes} byte(s) would change "
            f"({len(changed_segments)} memory segment(s), "
            f"{len(changed_properties)} property write(s))."
        ]
        for segment in changed_segments:
            lines.append(
                f"  memory {segment.address:#06x}: "
                f"{segment.changed_bytes}/{len(segment.planned)} changed"
            )
        for prop in changed_properties:
            lines.append(
                f"  object {prop.object_label} property {prop.property_id}: "
                f"{prop.changed_bytes}/{len(prop.planned)} changed"
            )
        unchanged = (len(self.segments) - len(changed_segments)) + (
            len(self.properties) - len(changed_properties)
        )
        if unchanged:
            lines.append(f"  ({unchanged} unchanged location(s) omitted)")
        return "\n".join(lines)
