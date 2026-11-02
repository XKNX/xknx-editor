"""Product downloads read in parts: within the size limit, and stoppable between the parts."""

from __future__ import annotations

import io
import threading
import urllib.request
from typing import Any

import pytest

from editor_gui import main
from xknxeditor.prod.errors import ImportCancelled


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """The body the next urlopen() returns (set by the test)."""
    body: list[bytes] = [b""]

    def urlopen(_request: Any, timeout: float) -> _Response:
        return _Response(body[0])

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(main, "_DOWNLOAD_CHUNK", 4)
    return body


def test_download_in_parts(served: list[bytes]) -> None:
    served[0] = b"0123456789"
    assert main._download_bytes("https://x/y.zip") == b"0123456789"  # pyright: ignore[reportPrivateUsage]
    assert main._download_bytes("https://x/y.zip", max_bytes=10) == b"0123456789"  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="size limit"):
        main._download_bytes("https://x/y.zip", max_bytes=9)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="http"):
        main._download_bytes("file:///etc/passwd")  # pyright: ignore[reportPrivateUsage]


def test_download_can_be_cancelled(served: list[bytes]) -> None:
    served[0] = b"0123456789"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ImportCancelled):
        main._download_bytes("https://x/y.zip", cancel=cancel)  # pyright: ignore[reportPrivateUsage]
