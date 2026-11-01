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
        # Only an unload clears what the device no longer holds.
        "clear_load_data": False,
        "clear_network": False,
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


def test_an_unload_clears_what_the_device_no_longer_holds() -> None:
    ((_, kwargs),) = _record("unload", b"\x00\xfa\x01\x02\x03\x04").call_args_list
    assert (kwargs["clear_load_data"], kwargs["clear_network"]) == (True, False)
    # Nothing read during an unload is recorded.
    assert kwargs["serial_number"] is None
    ((_, kwargs),) = _record("unload_all").call_args_list
    assert (kwargs["clear_load_data"], kwargs["clear_network"]) == (True, True)


def test_a_partial_download_with_the_ip_settings_marks_them_loaded() -> None:
    service = MagicMock()
    ProjectService.record_download(service, 7, "grp", None, medium_config=True)
    ((_, kwargs),) = service.set_device_commissioning.call_args_list
    assert kwargs["communication_part_loaded"] is True
    assert kwargs["medium_config_loaded"] is True
    assert "parameters_loaded" not in kwargs


def _parts(**loaded: bool):
    from types import SimpleNamespace

    from xknxeditor.download.scope import DownloadScope

    flags = {
        "individual_address_loaded": True,
        "application_program_loaded": True,
        "parameters_loaded": True,
        "communication_part_loaded": True,
        "medium_config_loaded": True,
        **loaded,
    }
    service = MagicMock()
    service.get_device_info.return_value = SimpleNamespace(**flags)
    return ProjectService.download_parts(service, 7, DownloadScope.PARTIAL)


def test_a_partial_download_loads_what_the_device_needs() -> None:
    from xknxeditor.download.scope import DownloadScope, PartialDownload

    # The 1.0.47 case: group addresses linked after the last download.
    assert _parts(communication_part_loaded=False) == PartialDownload(
        DownloadScope.GROUP_COMMUNICATION
    )
    assert _parts(parameters_loaded=False, medium_config_loaded=False) == (
        PartialDownload(DownloadScope.PARAMETERS, medium_config=True)
    )
    assert _parts(individual_address_loaded=False).scope is DownloadScope.FULL


def test_other_scopes_are_loaded_as_they_are() -> None:
    from xknxeditor.download.scope import DownloadScope, PartialDownload

    service = MagicMock()
    parts = ProjectService.download_parts(service, 7, DownloadScope.PARAMETERS)
    assert parts == PartialDownload(DownloadScope.PARAMETERS)
    service.get_device_info.assert_not_called()


def test_a_device_not_in_the_project_gets_a_full_download() -> None:
    from xknxeditor.download.scope import DownloadScope

    service = MagicMock()
    service.get_device_info.return_value = None
    parts = ProjectService.download_parts(service, 7, DownloadScope.PARTIAL)
    assert parts.scope is DownloadScope.FULL


def test_the_ip_settings_go_with_a_full_download_or_when_changed() -> None:
    from xknxeditor.download.scope import DownloadScope, PartialDownload

    service = MagicMock()
    service.ip_download_config.return_value = "settings"
    device = object()
    for parts, settings in (
        (PartialDownload(DownloadScope.FULL), "settings"),
        (PartialDownload(DownloadScope.APPLICATION), "settings"),
        (PartialDownload(DownloadScope.PARAMETERS), None),
        (PartialDownload(DownloadScope.GROUP_COMMUNICATION, True), "settings"),
    ):
        assert ProjectService.download_ip_config(service, device, parts) == settings
