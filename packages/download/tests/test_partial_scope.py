"""A partial download loads what the device is marked as not having loaded."""

from __future__ import annotations

import importlib
from typing import cast

import pytest

from xknxeditor.download.image import DownloadImage
from xknxeditor.download.scope import DownloadScope, PartialDownload, partial_scope
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp

from .test_ip_config import _RECORDED, _application

# The module, which the package's ``download`` function hides.
download = importlib.import_module("xknxeditor.download.download")


def _parts(
    *,
    address: bool = True,
    application: bool = True,
    parameters: bool = True,
    communication: bool = True,
    medium: bool = True,
) -> PartialDownload:
    return partial_scope(
        individual_address_loaded=address,
        application_program_loaded=application,
        parameters_loaded=parameters,
        communication_part_loaded=communication,
        medium_config_loaded=medium,
    )


def test_a_device_without_its_address_gets_a_full_download() -> None:
    assert _parts(address=False, application=False) == PartialDownload(
        DownloadScope.FULL
    )


def test_a_device_without_its_application_gets_the_application() -> None:
    assert _parts(application=False, parameters=False) == PartialDownload(
        DownloadScope.APPLICATION
    )


@pytest.mark.parametrize(
    ("parameters", "communication", "scope"),
    [
        (False, True, DownloadScope.PARAMETERS),
        (True, False, DownloadScope.GROUP_COMMUNICATION),
        (False, False, DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION),
        # Nothing changed: the application's memory is written all the same.
        (True, True, DownloadScope.PARAMETERS),
    ],
)
def test_the_parts_that_changed_are_loaded(
    parameters: bool, communication: bool, scope: DownloadScope
) -> None:
    assert _parts(parameters=parameters, communication=communication) == (
        PartialDownload(scope)
    )


def test_changed_ip_settings_are_loaded_with_the_parts() -> None:
    assert _parts(communication=False, medium=False) == PartialDownload(
        DownloadScope.GROUP_COMMUNICATION, medium_config=True
    )


def test_an_unresolved_partial_download_is_refused() -> None:
    image = DownloadImage(segments=(), properties=())
    with pytest.raises(ValueError, match="partial_scope"):
        download._resolve_procedure(  # pyright: ignore[reportPrivateUsage]
            _application(), None, image, DownloadScope.PARTIAL
        )


@pytest.mark.parametrize(
    "scope",
    [
        DownloadScope.PARAMETERS,
        DownloadScope.GROUP_COMMUNICATION,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
        DownloadScope.APPLICATION,
    ],
)
def test_ip_settings_passed_to_a_download_are_written(
    monkeypatch: pytest.MonkeyPatch, scope: DownloadScope
) -> None:
    monkeypatch.setattr(
        download, "_resolve_procedure", lambda *_args: [LdCtrlRestart()]
    )
    image = DownloadImage(segments=(), properties=())
    controls = download._resolve_controls(  # pyright: ignore[reportPrivateUsage]
        _application(), None, image, scope, _RECORDED
    )
    assert any(isinstance(c, LdCtrlWriteProp) for c in controls)
    assert isinstance(cast("object", controls[-1]), LdCtrlRestart)
