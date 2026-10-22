from __future__ import annotations


class AllocatorOverflow(OverflowError):
    """An allocator ran past its ``maxInclusive``; ``message_ref`` is its ``ErrorMessageRef``."""

    def __init__(
        self, message: str, allocator_id: str, message_ref: str | None
    ) -> None:
        super().__init__(message)
        self.allocator_id = allocator_id
        self.message_ref = message_ref


class Allocator:
    """Turns a pending arg into a concrete KNX address for one repeat index."""

    __slots__ = ("error_message_ref", "id", "max_inclusive", "start")

    def __init__(
        self,
        id: str,
        start: int,
        max_inclusive: int,
        error_message_ref: str | None = None,
    ) -> None:
        self.id = id
        self.start = start
        self.max_inclusive = max_inclusive
        self.error_message_ref = error_message_ref

    def resolve(
        self, position: int, allocates: int, alignment: int, base: int
    ) -> tuple[int, int]:
        """Give back (address, next_position); AllocatorOverflow past max_inclusive."""
        aligned = position
        if alignment > 1 and aligned % alignment != 0:
            aligned += alignment - (aligned % alignment)
        if aligned + allocates - 1 > self.max_inclusive:
            raise AllocatorOverflow(
                f"allocator {self.id!r} overflow: end {aligned + allocates - 1} > max {self.max_inclusive}",
                self.id,
                self.error_message_ref,
            )
        return aligned + base, aligned + allocates
