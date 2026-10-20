"""The cache folder: the product data cache moved into it once, its size and clearing it."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui import cache


@pytest.fixture
def cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "cache"
    monkeypatch.setattr(
        cache.platformdirs, "user_cache_dir", lambda *_a, **_k: str(root)
    )
    return root


def _entries(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x" * 10)


def test_product_cache_is_moved_into_the_cache_folder(
    tmp_path: Path, cache_root: Path
) -> None:
    old = tmp_path / "data" / "ir_cache"
    _entries(old, "a.pkl", "b.pkl")
    cache.move_product_cache(old)
    assert not old.exists()
    assert sorted(p.name for p in cache.product_cache_dir().iterdir()) == [
        "a.pkl",
        "b.pkl",
    ]
    assert cache.product_cache_dir().parent == cache_root


def test_product_cache_is_merged_into_an_existing_one(
    tmp_path: Path, cache_root: Path
) -> None:
    old = tmp_path / "data" / "ir_cache"
    _entries(old, "a.pkl", "b.pkl")
    _entries(cache.product_cache_dir(), "b.pkl", "c.pkl")
    cache.move_product_cache(old)
    assert not old.exists()
    names = sorted(p.name for p in cache.product_cache_dir().iterdir())
    assert names == ["a.pkl", "b.pkl", "c.pkl"]


def test_nothing_to_move(tmp_path: Path, cache_root: Path) -> None:
    cache.move_product_cache(tmp_path / "missing")
    assert not cache.product_cache_dir().exists()


def test_clear_frees_every_file(cache_root: Path) -> None:
    _entries(cache.product_cache_dir(), "a.pkl", "b.pkl")
    _entries(cache.docs_cache_dir(), "manual.pdf")
    assert cache.cache_size() == 30
    assert cache.clear_cache() == 30
    assert cache.cache_size() == 0
    # The folders stay, for whatever is cached next.
    assert cache.product_cache_dir().is_dir()
