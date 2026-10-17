"""Tables of data keep their ids until the default layout is restored; then every table gets a
new id, which has no stored column settings, and the count of resets survives a restart."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from editor_gui import settings
from editor_gui.widgets import tables


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path)
    tables._generation.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    tables._generation.clear()  # pyright: ignore[reportPrivateUsage]


def test_ids_stay_until_the_first_reset() -> None:
    assert tables.data_table_id("##logs") == "##logs"


def test_a_reset_gives_every_table_a_new_id() -> None:
    tables.reset_all_tables()
    assert tables.data_table_id("##logs") == "##logs#1"
    tables.reset_all_tables()
    assert tables.data_table_id("##logs") == "##logs#2"


def test_the_resets_are_remembered() -> None:
    tables.reset_all_tables()
    tables._generation.clear()  # pyright: ignore[reportPrivateUsage]
    assert tables.data_table_id("##logs") == "##logs#1"
