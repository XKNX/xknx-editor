"""The parse cache keeps the evaluation trees of its programs, as the current code builds them: a
program loaded from it comes with its tree, which another code builds and stores again."""

from __future__ import annotations

import pickle
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from xknxeditor.prod import parse_cache
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2 import dynamic
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

_XML = (Path(__file__).parent / "fixtures" / "fan_coil_application.xml").read_bytes()
_MANUFACTURER = re.search(rb'RefId="(M-[0-9A-F]{4})"', _XML).group(1).decode()  # type: ignore[union-attr]


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """The programs a tree is built for, and the code the cache keeps the trees of."""
    ids: list[str] = []
    real = DynamicTreeBuilder.__init__

    def counted(self: DynamicTreeBuilder, app: Any) -> None:
        ids.append(app.id)
        real(self, app)

    monkeypatch.setattr(DynamicTreeBuilder, "__init__", counted)
    parse_cache.set_tree_code("code A")
    try:
        yield ids
    finally:
        parse_cache.set_tree_code(None)


def _load(cache: Path, language: str | None = None) -> Application:
    (app,) = parse_application_xml(_XML, _MANUFACTURER, language, cache_dir=cache)
    return app


def _entry(cache: Path) -> object:
    (path,) = cache.glob("ir_*.pkl")
    with path.open("rb") as file:
        return pickle.load(file)


def test_a_program_comes_with_its_tree_from_the_cache(
    tmp_path: Path, built: list[str]
) -> None:
    first = _load(tmp_path)
    assert len(built) == 1
    stored = _entry(tmp_path)
    assert isinstance(stored, tuple) and stored[1] == "code A"
    again = _load(tmp_path)
    # Taken from the cache: no tree built, and none when it is used.
    program, tree = again.resolved()
    assert len(built) == 1
    first_program, first_tree = first.resolved()
    assert DynamicUI(program, tree_builder=tree).ui() == (
        DynamicUI(first_program, tree_builder=first_tree).ui()
    )


def test_another_code_builds_the_tree_again(tmp_path: Path, built: list[str]) -> None:
    _load(tmp_path)
    parse_cache.set_tree_code("code B")
    _load(tmp_path)
    assert len(built) == 2
    stored = _entry(tmp_path)
    assert isinstance(stored, tuple) and stored[1] == "code B"
    _load(tmp_path)
    assert len(built) == 2


def test_an_entry_of_the_program_alone_gets_its_tree(
    tmp_path: Path, built: list[str]
) -> None:
    parse_cache.set_tree_code(None)
    _load(tmp_path)
    assert not built
    assert not isinstance(_entry(tmp_path), tuple)
    parse_cache.set_tree_code("code A")
    _load(tmp_path)
    _load(tmp_path)
    assert len(built) == 1
    assert isinstance(_entry(tmp_path), tuple)


def test_the_kept_tree_shows_the_language_asked_for(
    tmp_path: Path, built: list[str]
) -> None:
    _load(tmp_path)
    english = _load(tmp_path, "en")
    program, tree = english.resolved()
    fresh = dynamic.DynamicTreeBuilder(program)
    assert DynamicUI(program, tree_builder=tree).ui() == (
        DynamicUI(program, tree_builder=fresh).ui()
    )


def test_a_reloaded_application_keeps_the_tree_it_was_loaded_with(
    tmp_path: Path, built: list[str]
) -> None:
    loaded = _load(tmp_path)
    tree = loaded.resolved()[1]
    app = Application.unloaded(
        loaded.id, "Fan coil", "20", _MANUFACTURER, lambda: loaded
    )
    assert app.resolved()[1] is tree
    assert len(built) == 1
