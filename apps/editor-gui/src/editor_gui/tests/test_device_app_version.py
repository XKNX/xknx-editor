"""The application-program version shown in the device-list hover tooltips."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from editor_gui.device import Device
from editor_gui.device_info import device_app_version_text
from xknxeditor.prod import Application


def _device(app: object) -> Device:
    return cast(Device, SimpleNamespace(app=app))


@pytest.mark.parametrize(
    ("app_id", "expected"),
    [
        ("M-0083_A-0045-26-ABCD", "2.6"),
        ("M-0083_A-0045-00-ABCD", "0.0"),
        (" m-0083_a-0045-2a-abcd ", "2.10"),
        ("M-0083_A-0045-FF-ABCD", "15.15"),
    ],
)
def test_version_parsed_from_app_id(app_id: str, expected: str) -> None:
    app = SimpleNamespace(
        id=app_id,
        version="23",
        program=SimpleNamespace(application_version=0x12),
    )
    assert device_app_version_text(_device(app)) == expected


@pytest.mark.parametrize("app_id", ["not-an-app-id", ""])
@pytest.mark.parametrize(("version", "expected"), [(0x75, "7.5"), (0, "0.0")])
def test_version_falls_back_to_program_when_app_id_unparsable(
    app_id: str, version: int, expected: str
) -> None:
    app = SimpleNamespace(
        id=app_id, version="23", program=SimpleNamespace(application_version=version)
    )
    assert device_app_version_text(_device(app)) == expected


def test_no_application_is_empty() -> None:
    assert device_app_version_text(_device(None)) == ""


def test_version_from_id_does_not_load_the_program() -> None:
    def load() -> Application:
        pytest.fail("Reading the version from the id must not load the program")

    app = Application.unloaded("M-0083_A-0045-26-ABCD", "Switch", "23", "M-0083", load)
    device = Device(1, "", app, "", visible=[])
    assert device_app_version_text(device) == "2.6"
