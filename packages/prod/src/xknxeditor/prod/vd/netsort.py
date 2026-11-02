"""The unstable sort of the .NET runtime, for results in the converter's order.

``List<T>.Sort`` does not keep the order of equal elements; outputs sorted that way only match when
sorted exactly like it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def list_sort[T](items: list[T], compare: Callable[[T, T], int]) -> None:
    """Sort ``items`` in place like ``List<T>.Sort(comparer)`` of the runtime the converter uses
    (depth limited quicksort; equal elements do not keep their order)."""
    if len(items) > 1:
        _quick(items, 0, len(items) - 1, compare, 32)


def _quick[T](
    a: list[T], left: int, right: int, cmp: Callable[[T, T], int], depth: int
) -> None:
    while True:
        if depth == 0:
            _heapsort(a, left, right, cmp)
            return
        i, j = left, right
        middle = i + ((j - i) >> 1)
        _swap_if_greater(a, cmp, i, middle)
        _swap_if_greater(a, cmp, i, j)
        _swap_if_greater(a, cmp, middle, j)
        x = a[middle]
        while True:
            while cmp(a[i], x) < 0:
                i += 1
            while cmp(x, a[j]) < 0:
                j -= 1
            if i > j:
                break
            if i < j:
                a[i], a[j] = a[j], a[i]
            i += 1
            j -= 1
            if i > j:
                break
        depth -= 1
        if j - left <= right - i:
            if left < j:
                _quick(a, left, j, cmp, depth)
            left = i
        else:
            if i < right:
                _quick(a, i, right, cmp, depth)
            right = j
        if left >= right:
            return


def _swap_if_greater[T](a: list[T], cmp: Callable[[T, T], int], i: int, j: int) -> None:
    if i != j and cmp(a[i], a[j]) > 0:
        a[i], a[j] = a[j], a[i]


def _heapsort[T](a: list[T], lo: int, hi: int, cmp: Callable[[T, T], int]) -> None:
    n = hi - lo + 1
    for i in range(n // 2, 0, -1):
        _down(a, i, n, lo, cmp)
    for i in range(n, 1, -1):
        a[lo], a[lo + i - 1] = a[lo + i - 1], a[lo]
        _down(a, 1, i - 1, lo, cmp)


def _down[T](a: list[T], i: int, n: int, lo: int, cmp: Callable[[T, T], int]) -> None:
    d = a[lo + i - 1]
    while i <= n // 2:
        child = 2 * i
        if child < n and cmp(a[lo + child - 1], a[lo + child]) < 0:
            child += 1
        if not cmp(d, a[lo + child - 1]) < 0:
            break
        a[lo + i - 1] = a[lo + child - 1]
        i = child
    a[lo + i - 1] = d
