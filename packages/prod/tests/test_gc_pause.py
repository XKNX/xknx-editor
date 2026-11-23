import gc
import threading

from xknxeditor.prod.gc_pause import gc_paused, keep_long_lived, release_long_lived


def test_overlapping_pauses_from_two_threads() -> None:
    assert gc.isenabled()
    entered = threading.Event()
    release = threading.Event()

    def other() -> None:
        with gc_paused():
            entered.set()
            release.wait()

    with gc_paused():
        thread = threading.Thread(target=other)
        thread.start()
        entered.wait()
    # the first pause ended, the other thread's pause still runs
    assert not gc.isenabled()
    release.set()
    thread.join()
    assert gc.isenabled()


def test_keeps_a_disabled_collector_disabled() -> None:
    gc.disable()
    try:
        with gc_paused(), gc_paused():
            assert not gc.isenabled()
        assert not gc.isenabled()
    finally:
        gc.enable()


class _Node:
    """An object the collector tracks."""

    def __init__(self) -> None:
        self.peer: object = None


def test_long_lived_objects_are_left_out_of_collections_until_released() -> None:
    keep_long_lived(True)
    try:
        before = gc.get_freeze_count()
        with gc_paused(long_lived=True):
            kept = [_Node() for _ in range(1000)]
        # Left out of the collections now; a pause for others leaves nothing out.
        assert gc.get_freeze_count() >= before + 1000
        with gc_paused():
            others = [_Node() for _ in range(1000)]
        frozen = gc.get_freeze_count()
        gc.collect()
        assert gc.get_freeze_count() == frozen
        # Still freed when no longer used, cycles aside.
        kept.clear()
        assert gc.get_freeze_count() < frozen
        del others
    finally:
        release_long_lived()
        keep_long_lived(False)
    assert gc.get_freeze_count() == 0


def test_a_cycle_left_out_is_collected_once_released() -> None:
    keep_long_lived(True)
    try:
        with gc_paused(long_lived=True):
            first, second = _Node(), _Node()
            first.peer, second.peer = second, first
        del first, second
        assert gc.collect() == 0
        release_long_lived()
        assert gc.collect() >= 2
    finally:
        release_long_lived()
        keep_long_lived(False)


def test_without_keeping_nothing_is_left_out() -> None:
    with gc_paused(long_lived=True):
        kept = [_Node() for _ in range(100)]
    assert gc.get_freeze_count() == 0
    del kept
