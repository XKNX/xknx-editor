"""Tests for the revision_cached decorator (per-frame DB read memoisation)."""

from editor_gui.concurrency import revision_cached


class _Fake:
    """Stand-in for a service exposing the contract revision_cached requires."""

    def __init__(self) -> None:
        self.revision = 0
        self._revision_cache: dict[str, tuple[int, object]] = {}
        self.calls = 0

    @revision_cached
    def read(self) -> list[int]:
        self.calls += 1
        return [self.revision]

    def reset_cache(self) -> None:
        """Mirror ProjectService._reset clearing its revision cache on open/close."""
        self._revision_cache.clear()


def test_caches_until_revision_changes() -> None:
    f = _Fake()
    assert f.read() == [0]
    assert f.read() == [0]
    assert f.calls == 1  # second call served from cache, no rebuild

    f.revision = 1
    assert f.read() == [1]
    assert f.calls == 2  # revision bump forced a rebuild


def test_returns_shared_reference() -> None:
    f = _Fake()
    assert f.read() is f.read()  # same object, not a fresh copy each frame


def test_empty_result_is_still_cached() -> None:
    """A falsy result (empty list) must not be mistaken for 'not cached' and re-run every frame."""

    class Empty:
        def __init__(self) -> None:
            self.revision = 0
            self._revision_cache: dict[str, tuple[int, object]] = {}
            self.calls = 0

        @revision_cached
        def read(self) -> list[int]:
            self.calls += 1
            return []

    e = Empty()
    assert e.read() == []
    assert e.read() == []
    assert e.calls == 1


def test_clearing_cache_forces_rebuild() -> None:
    """_reset clears _revision_cache so a cache built at revision 0 for one project is not reused
    for the next project (which also starts at revision 0)."""
    f = _Fake()
    assert f.read() == [0]
    assert f.calls == 1
    f.reset_cache()  # simulates ProjectService._reset on open/close
    assert f.read() == [0]
    assert f.calls == 2  # rebuilt despite the revision still being 0


def test_a_language_switch_forces_rebuild() -> None:
    """A read holding shown texts (the History's labels) is made again in the new language, not
    only after the next edit."""
    from editor_gui.strings import get_locale, set_locale

    f = _Fake()
    previous = get_locale()
    try:
        set_locale("en")
        f.read()
        set_locale("de")
        f.read()
        assert f.calls == 2
        f.read()
        assert f.calls == 2
    finally:
        set_locale(previous)
