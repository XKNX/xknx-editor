"""Widths of a grid's percentage columns in whole pixels, as WPF lays out star columns.

A WPF Grid shares its width among star columns by their weights: it adds the weights up in
increasing order and resolves the columns in decreasing order, each taking its share of what is
left. With layout rounding each width is then rounded, and where the rounded widths add up to more
(or less) than the rounded grid, a pixel is taken from (or given to) the columns in the order of
their rounding errors. Equal errors keep the order .NET's sort leaves them in, so that order is
followed here too: it decides which of several equal columns is a pixel narrower.
"""

from __future__ import annotations

# Partitions up to this size are sorted by insertion.
_INSERTION_SORT_SIZE = 16
# Rounded sizes this close count as equal.
_CLOSE = 1e-5


def _sorted_indices(keys: list[float]) -> list[int]:
    """``range(len(keys))`` sorted by ``keys`` as .NET Framework's ``Array.Sort`` with a comparer
    sorts it (introsort): equal keys end up in the same order."""
    order = list(range(len(keys)))

    def compare(a: int, b: int) -> int:
        return (keys[a] > keys[b]) - (keys[a] < keys[b])

    def swap_if_greater(a: int, b: int) -> None:
        if a != b and compare(order[a], order[b]) > 0:
            order[a], order[b] = order[b], order[a]

    def insertion_sort(lo: int, hi: int) -> None:
        for i in range(lo, hi):
            j = i
            item = order[i + 1]
            while j >= lo and compare(item, order[j]) < 0:
                order[j + 1] = order[j]
                j -= 1
            order[j + 1] = item

    def heapsort(lo: int, hi: int) -> None:
        n = hi - lo + 1

        def down(i: int, n: int) -> None:
            item = order[lo + i - 1]
            while i <= n // 2:
                child = 2 * i
                if child < n and compare(order[lo + child - 1], order[lo + child]) < 0:
                    child += 1
                if not compare(item, order[lo + child - 1]) < 0:
                    break
                order[lo + i - 1] = order[lo + child - 1]
                i = child
            order[lo + i - 1] = item

        for i in range(n // 2, 0, -1):
            down(i, n)
        for i in range(n, 1, -1):
            order[lo], order[lo + i - 1] = order[lo + i - 1], order[lo]
            down(1, i - 1)

    def partition(lo: int, hi: int) -> int:
        middle = lo + (hi - lo) // 2
        swap_if_greater(lo, middle)
        swap_if_greater(lo, hi)
        swap_if_greater(middle, hi)
        pivot = order[middle]
        order[middle], order[hi - 1] = order[hi - 1], order[middle]
        left, right = lo, hi - 1
        while left < right:
            left += 1
            while compare(order[left], pivot) < 0:
                left += 1
            right -= 1
            while compare(pivot, order[right]) < 0:
                right -= 1
            if left >= right:
                break
            order[left], order[right] = order[right], order[left]
        order[left], order[hi - 1] = order[hi - 1], order[left]
        return left

    def introsort(lo: int, hi: int, depth: int) -> None:
        while hi > lo:
            size = hi - lo + 1
            if size <= _INSERTION_SORT_SIZE:
                if size == 2:
                    swap_if_greater(lo, hi)
                elif size == 3:
                    swap_if_greater(lo, hi - 1)
                    swap_if_greater(lo, hi)
                    swap_if_greater(hi - 1, hi)
                elif size > 3:
                    insertion_sort(lo, hi)
                return
            if depth == 0:
                heapsort(lo, hi)
                return
            depth -= 1
            pivot = partition(lo, hi)
            introsort(pivot + 1, hi, depth)
            hi = pivot - 1

    if len(order) > 1:
        # .NET's depth limit: twice the number of binary digits of the length.
        introsort(0, len(order) - 1, 2 * len(order).bit_length())
    return order


def star_widths(weights: list[float], unit: float, scale: float = 1.0) -> list[float]:
    """Pixel widths of columns of ``weights`` (percentages) in a grid ``unit`` device independent
    pixels per weight wide, drawn ``scale`` pixels per device independent pixel."""

    def rounded(value: float) -> float:
        # As WPF rounds layout values: to the nearest pixel, a half to the even one.
        return round(value * scale) / scale

    count = len(weights)
    final = rounded(sum(weights) * unit)
    # Partial sums of the weights in increasing order, the columns resolved in decreasing order.
    order = _sorted_indices(weights)
    partial = [0.0] * count
    total = 0.0
    for index in order:
        total += weights[index]
        partial[index] = total
    sizes = [0.0] * count
    taken = 0.0
    for index in reversed(order):
        share = weights[index] / partial[index] if weights[index] > 0 else 0.0
        sizes[index] = max(final - taken, 0.0) * share
        taken += sizes[index]
    # Layout rounding: each width rounded, the sum brought back to the grid's rounded width.
    whole = [rounded(size) for size in sizes]
    errors = [w - size for w, size in zip(whole, sizes, strict=True)]
    step = 1.0 / scale
    total = sum(whole)
    if abs(total - final) >= _CLOSE:
        by_error = _sorted_indices(errors)
        if total > final:
            for index in reversed(by_error):
                if total <= final or abs(total - final) < _CLOSE:
                    break
                if whole[index] - step >= 0.0:
                    whole[index] -= step
                    total -= step
        else:
            for index in by_error:
                if total >= final or abs(total - final) < _CLOSE:
                    break
                whole[index] += step
                total += step
    return [w * scale for w in whole]
