"""Program Device starts right away; its dialog only opens when it has to ask something first."""

from __future__ import annotations

import pytest

from editor_gui.plugins.project.ui.configure import (
    ProgramRequest,
    direct_program_request,
)
from xknxeditor.download.scope import DownloadScope


@pytest.mark.parametrize(
    "scope",
    [
        DownloadScope.FULL,
        DownloadScope.APPLICATION,
        DownloadScope.PARAMETERS,
        DownloadScope.GROUP_COMMUNICATION,
        DownloadScope.INDIVIDUAL_ADDRESS,
    ],
)
def test_programming_starts_without_a_question(scope: DownloadScope):
    request = direct_program_request(
        scope, plugin_required=False, use_serial_number=False, serial_owner=None
    )
    assert request == ProgramRequest(scope=scope)


@pytest.mark.parametrize(
    "scope",
    [
        DownloadScope.UNLOAD,
        DownloadScope.UNLOAD_ALL,
        DownloadScope.OVERWRITE_INDIVIDUAL_ADDRESS,
    ],
)
def test_unloading_and_overwriting_an_address_still_ask(scope: DownloadScope):
    assert (
        direct_program_request(
            scope, plugin_required=False, use_serial_number=False, serial_owner=None
        )
        is None
    )


def test_a_missing_download_plugin_still_asks():
    assert (
        direct_program_request(
            DownloadScope.FULL,
            plugin_required=True,
            use_serial_number=False,
            serial_owner=None,
        )
        is None
    )


def test_the_address_alone_needs_no_plugin():
    request = direct_program_request(
        DownloadScope.INDIVIDUAL_ADDRESS,
        plugin_required=True,
        use_serial_number=False,
        serial_owner=None,
    )
    assert request == ProgramRequest(scope=DownloadScope.INDIVIDUAL_ADDRESS)


def test_the_serial_number_setting_is_used():
    request = direct_program_request(
        DownloadScope.INDIVIDUAL_ADDRESS,
        plugin_required=False,
        use_serial_number=True,
        serial_owner=None,
    )
    assert request == ProgramRequest(
        scope=DownloadScope.INDIVIDUAL_ADDRESS, use_serial_number=True
    )


def test_a_serial_number_of_another_device_still_asks():
    assert (
        direct_program_request(
            DownloadScope.INDIVIDUAL_ADDRESS,
            plugin_required=False,
            use_serial_number=True,
            serial_owner="1.1.5 Dimmer",
        )
        is None
    )
