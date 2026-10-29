"""Recording a successful download in the project."""

from __future__ import annotations

from unittest.mock import MagicMock

from editor_gui.plugins.project.service import ProjectService


def _record(scope: str, serial: bytes | None = None) -> MagicMock:
    service = MagicMock()
    ProjectService.record_download(service, 7, scope, serial)
    return service.set_device_commissioning


def test_full_download_marks_everything_loaded() -> None:
    call = _record("full", b"\x00\xfa\x01\x02\x03\x04")
    ((args, kwargs),) = call.call_args_list
    assert args == (7,)
    assert kwargs["serial_number"] == "APoBAgME"
    assert kwargs["last_download"]
    assert all(
        kwargs[flag]
        for flag in (
            "individual_address_loaded",
            "application_program_loaded",
            "communication_part_loaded",
            "medium_config_loaded",
            "parameters_loaded",
        )
    )


def test_partial_scopes_set_only_their_part() -> None:
    ((_, kwargs),) = _record("par").call_args_list
    assert kwargs == {
        "serial_number": None,
        "last_download": kwargs["last_download"],
        "mac_address": None,
        "parameters_loaded": True,
        # A download leaves the device's "Last Modified" as it was.
        "download": True,
        # The address it is found at comes later (see record_found_ip).
        "ip_address": None,
    }
    ((_, kwargs),) = _record("ia").call_args_list
    assert kwargs["individual_address_loaded"] is True


def test_unload_clears_the_download_time() -> None:
    ((_, kwargs),) = _record("unload").call_args_list
    assert kwargs["last_download"] is None
    assert kwargs["application_program_loaded"] is False
    # The application only: the device keeps its address.
    assert "individual_address_loaded" not in kwargs
    ((_, kwargs),) = _record("unload_all").call_args_list
    assert kwargs["last_download"] is None
    assert kwargs["individual_address_loaded"] is False
    assert kwargs["medium_config_loaded"] is False


def test_a_factory_reset_by_an_unload_gives_the_device_its_fdsk_back() -> None:
    from types import SimpleNamespace

    service = MagicMock()
    device = object()
    ProjectService.record_secure_download(
        service, device, SimpleNamespace(factory_reset=True, tool_key_loaded=False)
    )
    service.record_factory_reset.assert_called_once_with(device)


def test_unknown_scope_records_nothing() -> None:
    _record("eval").assert_not_called()
