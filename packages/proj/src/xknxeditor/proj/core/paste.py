"""Paste Special: the options copies of elements are made with (a drop with Ctrl held), and how
the numbers of new group addresses and group ranges are given out.

The options:

* ``count`` copies of each element.
* ``group_addresses``: what the copies of devices, building parts, trades, functions, lines and
  segments link. ``DONT_COPY`` links nothing; ``KEEP`` links the same group addresses;
  ``CREATE_NEW`` gives every copy one new group address per group address its source links, in the
  group named by ``target_range`` (a middle group "1/2" in three-level style, a main group "1" in
  two-level style; in free style the range ``target_range_id``).
* ``allocation``: where in their group new group addresses (and new main and middle groups) go.
  ``FILL_UP`` takes the lowest free one, ``APPEND`` the one after the highest used, ``START_WITH``
  the first free from ``start_with`` on, ``OFFSET`` the source's own place moved by ``offset``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from xknxeditor.proj.core.addressing import GroupAddressStyle


class GroupAddressCopy(StrEnum):
    """What the copies link (see the module)."""

    DONT_COPY = "dont_copy"
    KEEP = "keep"
    CREATE_NEW = "create_new"


class Allocation(StrEnum):
    """Where new group addresses and groups go in their group (see the module)."""

    FILL_UP = "fill_up"
    APPEND = "append"
    START_WITH = "start_with"
    OFFSET = "offset"


@dataclass(frozen=True)
class PasteOptions:
    """The choices of the Paste Special dialog (see the module); the defaults are its own."""

    count: int = 1
    group_addresses: GroupAddressCopy = GroupAddressCopy.DONT_COPY
    target_range: str = ""
    target_range_id: int | None = None
    allocation: Allocation = Allocation.FILL_UP
    start_with: int = 1
    offset: int = 0


# What one group of each level spans: a middle group (three-level), a main group (two-level).
_MIDDLE_SPAN = 0x100
_MAIN_SPAN = 0x800


def parse_target_range(text: str, style: GroupAddressStyle) -> tuple[int, int] | None:
    """The addresses the group ``text`` names, as ``(first, last)``: a middle group "1/2" in
    three-level style, a main group "1" in two-level style. None when ``text`` names no such
    group, and always in free style (whose groups are picked, not typed)."""
    parts = text.strip().split("/")
    try:
        numbers = [int(part.strip()) for part in parts]
    except ValueError:
        return None
    if style is GroupAddressStyle.THREE_LEVEL and len(numbers) == 2:
        main, middle = numbers
        if 0 <= main <= 31 and 0 <= middle <= 7:
            first = (main << 11) | (middle << 8)
            return first, first + _MIDDLE_SPAN - 1
    if style is GroupAddressStyle.TWO_LEVEL and len(numbers) == 1:
        main = numbers[0]
        if 0 <= main <= 31:
            first = main << 11
            return first, first + _MAIN_SPAN - 1
    return None


def group_base(range_start: int, style: GroupAddressStyle) -> int:
    """The first address of the group starting at ``range_start``: its middle (three-level) or
    main group (two-level) as numbered, also when the stored range skips the reserved address 0;
    the range's own start in free style."""
    if style is GroupAddressStyle.THREE_LEVEL:
        return range_start & ~(_MIDDLE_SPAN - 1)
    if style is GroupAddressStyle.TWO_LEVEL:
        return range_start & ~(_MAIN_SPAN - 1)
    return range_start


def place_in_group(address: int, range_start: int, style: GroupAddressStyle) -> int:
    """Where ``address`` sits in its group (the group starting at ``range_start``): its sub
    address in the level styles, its distance from the range's start in free style."""
    return address - group_base(range_start, style)


class AddressPool:
    """The free numbers from ``low`` to ``high`` (addresses of a group, or the numbers of main or
    middle groups) given out as ``allocation`` says. ``base`` is where ``start_with`` and an
    offset place count from; ``used`` are taken already, and so is every number given out.
    0 is never given out when ``skip_zero`` (the reserved group address 0)."""

    def __init__(
        self,
        low: int,
        high: int,
        used: Iterable[int],
        allocation: Allocation = Allocation.FILL_UP,
        *,
        start_with: int = 1,
        offset: int = 0,
        base: int | None = None,
        skip_zero: bool = True,
    ) -> None:
        self._low = low
        self._high = high
        self._used = {number for number in used if low <= number <= high}
        self._allocation = allocation
        self._start_with = start_with
        self._offset = offset
        self._base = low if base is None else base
        self._skip_zero = skip_zero
        self._last: int | None = None

    def _free(self, number: int) -> bool:
        return (
            self._low <= number <= self._high
            and number not in self._used
            and not (self._skip_zero and number == 0)
        )

    def _first_free(self, start: int) -> int | None:
        """The first free number from ``start`` up to ``high``."""
        return next(
            (n for n in range(max(start, self._low), self._high + 1) if self._free(n)),
            None,
        )

    def _next_free(self, after: int) -> int | None:
        """The first free number after ``after``, from ``low`` on again past ``high``."""
        found = self._first_free(after + 1)
        return found if found is not None else self._first_free(self._low)

    def take(self, place: int = 0) -> int | None:
        """The next number, taken; None when none is free. ``place`` is where the source sits
        in its own group (see :func:`place_in_group`), which an offset keeps."""
        if self._allocation is Allocation.OFFSET:
            wanted = self._base + place + self._offset
            number = wanted if self._free(wanted) else self._first_free(self._low)
        elif self._last is not None:
            number = self._next_free(self._last)
        elif self._allocation is Allocation.APPEND:
            number = self._next_free(max(self._used, default=self._low - 1))
        elif self._allocation is Allocation.START_WITH:
            number = self._first_free(self._base + self._start_with)
            if number is None:
                number = self._first_free(self._low)
        else:
            number = self._first_free(self._low)
        if number is not None:
            self._used.add(number)
            self._last = number
        return number

    def room(self) -> int:
        """How many numbers are still free."""
        return sum(1 for n in range(self._low, self._high + 1) if self._free(n))


def place_range(
    low: int,
    high: int,
    taken: Iterable[tuple[int, int]],
    size: int,
    allocation: Allocation = Allocation.FILL_UP,
    start_with: int = 0,
) -> int | None:
    """Where a group range of ``size`` addresses goes between ``low`` and ``high`` (a free-style
    group range, beside the ranges ``taken`` as ``(start, end)``): ``FILL_UP`` the lowest place
    it fits, ``APPEND`` right after the highest taken one, ``START_WITH`` the first place from
    ``start_with`` on; the lowest place when the asked one has no room. None when it fits
    nowhere."""
    spans = sorted((start, end) for start, end in taken)

    def fits(start: int) -> bool:
        end = start + size - 1
        return (
            low <= start and end <= high and all(end < s or start > e for s, e in spans)
        )

    def first_from(begin: int) -> int | None:
        candidates = [begin, *(e + 1 for _s, e in spans if e + 1 > begin)]
        return next((c for c in sorted(candidates) if fits(c)), None)

    if size < 1:
        return None
    wanted: int | None = None
    if allocation is Allocation.APPEND:
        wanted = first_from(max((e + 1 for _s, e in spans), default=low))
    elif allocation is Allocation.START_WITH:
        wanted = first_from(max(low, start_with))
    return wanted if wanted is not None else first_from(low)
