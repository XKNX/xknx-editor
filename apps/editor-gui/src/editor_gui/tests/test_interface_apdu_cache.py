"""The maximum APDU length a download learned for an interface that does not report it."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui import settings
from editor_gui.interface_apdu_cache import (
    forget_interface_apdu,
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


def test_a_forgotten_length_is_negotiated_again() -> None:
    remember_interface_apdu("192.168.3.13", 15)
    remember_interface_apdu("192.168.3.14", 15)
    forget_interface_apdu("192.168.3.13")
    assert learned_interface_apdu("192.168.3.13") is None
    assert learned_interface_apdu("192.168.3.14") == 15
    # Forgetting what is not stored, or without an interface, changes nothing.
    forget_interface_apdu("192.168.3.13")
    forget_interface_apdu(None)
    assert settings.load_settings("interface_apdu") == {"192.168.3.14": 15}


class _Logger:
    def info(self, event: str, **kwargs: object) -> None:
        pass


def _service() -> ConnectionService:
    service = ConnectionService()
    service.set_logger(_Logger())  # type: ignore[arg-type]
    service.interface_target = "192.168.3.13"
    return service


def test_a_limit_a_download_found_is_kept_at_once() -> None:
    service = _service()
    service._interface_limit_found(15)  # pyright: ignore[reportPrivateUsage]
    # This connection and the next ones to the interface use standard frames, even if the
    # download that found the limit goes on to fail.
    assert service.interface_max_apdu_length == 15
    assert service.standard_frames_only
    assert learned_interface_apdu("192.168.3.13") == 15


def test_standard_frames_can_be_set_and_cleared_for_the_interface() -> None:
    service = _service()
    assert service.can_set_frame_size
    assert not service.standard_frames_only
    assert service.set_standard_frames_only(True)
    assert service.standard_frames_only
    assert service.interface_max_apdu_length == 15
    assert learned_interface_apdu("192.168.3.13") == 15
    assert service.set_standard_frames_only(False)
    assert not service.standard_frames_only
    assert service.interface_max_apdu_length is None
    assert learned_interface_apdu("192.168.3.13") is None


def test_the_frame_size_of_an_interface_that_reports_it_is_not_set() -> None:
    service = _service()
    service.interface_reports_apdu = True
    service.interface_max_apdu_length = 15
    # A reported maximum is not a user setting, and cannot be changed.
    assert not service.can_set_frame_size
    assert not service.standard_frames_only
    assert not service.set_standard_frames_only(False)
    assert service.interface_max_apdu_length == 15
    assert settings.load_settings("interface_apdu") == {}


def test_the_frame_size_needs_a_connection() -> None:
    service = _service()
    service.interface_target = None
    assert not service.can_set_frame_size
    assert not service.set_standard_frames_only(True)
    assert service.interface_max_apdu_length is None
