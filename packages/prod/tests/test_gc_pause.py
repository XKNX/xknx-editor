import gc
import threading

from xknxeditor.prod.gc_pause import gc_paused


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
