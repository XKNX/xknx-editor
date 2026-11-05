"""The maximum APDU length a download learned for an interface that does not report it."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui import settings
from editor_gui.interface_apdu_cache import (
    learned_interface_apdu,
    remember_interface_apdu,
)
from editor_gui.plugins.connection.service import ConnectionService


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path)


def test_a_learned_length_is_kept_per_interface() -> None:
    assert learned_interface_apdu("192.168.3.13") is None
    remember_interface_apdu("192.168.3.13", 15)
    assert learned_interface_apdu("192.168.3.13") == 15
    assert learned_interface_apdu("192.168.3.14") is None


def test_nothing_is_stored_without_an_interface_or_a_length() -> None:
    remember_interface_apdu(None, 15)
    remember_interface_apdu("192.168.3.13", None)
    assert learned_interface_apdu(None) is None
    assert learned_interface_apdu("192.168.3.13") is None
    assert settings.load_settings("interface_apdu") == {}


class _Logger:
    def info(self, event: str, **kwargs: object) -> None:
        pass


def _done(result: Any) -> Future[Any]:
    future: Future[Any] = Future()
    future.set_result(result)
    return future


def _service() -> ConnectionService:
    service = ConnectionService()
    service.set_logger(_Logger())  # type: ignore[arg-type]
    service.interface_target = "192.168.3.13"
    return service


def test_a_download_that_learned_the_limit_keeps_it_for_the_interface() -> None:
    service = _service()
    service._remember_interface_apdu(  # pyright: ignore[reportPrivateUsage]
        _done(SimpleNamespace(interface_max_apdu_length=15))
    )
    # This connection and the next ones to the interface use standard frames.
    assert service.interface_max_apdu_length == 15
    assert learned_interface_apdu("192.168.3.13") == 15


def test_a_download_that_learned_nothing_changes_nothing() -> None:
    service = _service()
    service._remember_interface_apdu(  # pyright: ignore[reportPrivateUsage]
        _done(SimpleNamespace(interface_max_apdu_length=None))
    )
    failed: Future[Any] = Future()
    failed.set_exception(RuntimeError("download failed"))
    service._remember_interface_apdu(failed)  # pyright: ignore[reportPrivateUsage]
    assert service.interface_max_apdu_length is None
    assert learned_interface_apdu("192.168.3.13") is None
