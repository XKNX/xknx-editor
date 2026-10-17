"""The fingerprint of the code that evaluates applications, the key part of every device snapshot:
from the sources, or as a frozen build's build stored it."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import editor_gui
from editor_gui import device_snapshot


@pytest.fixture(autouse=True)
def _uncached() -> Iterator[None]:
    device_snapshot.code_fingerprint.cache_clear()
    yield
    device_snapshot.code_fingerprint.cache_clear()


def test_from_the_sources() -> None:
    fingerprint = device_snapshot.code_fingerprint()
    assert fingerprint == device_snapshot.source_fingerprint()
    assert len(fingerprint) == 64


def test_a_frozen_build_uses_the_stored_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / device_snapshot.BUNDLED_FINGERPRINT).write_text("f" * 64 + "\n")
    monkeypatch.setattr(editor_gui, "__file__", str(tmp_path / "__init__.pyc"))
    assert device_snapshot.code_fingerprint() == "f" * 64
