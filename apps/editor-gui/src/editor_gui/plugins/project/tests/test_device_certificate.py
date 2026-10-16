"""The certificate dialog's texts follow what the project holds for the device."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.device_certificate import (
    CertificateState,
    certificate_prompt,
)
from xknxeditor.proj.core.service import DeviceKeys

_SERIAL = bytes.fromhex("00fa12345678")
_KEY = bytes(range(16))
_DEVICE = cast(Any, SimpleNamespace(node_id=7))


def test_without_a_device_only_the_code_is_asked_for() -> None:
    assert certificate_prompt(None) == [S.DEVICE_CERTIFICATE_ENTER]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            CertificateState(None, key_in_use=False),
            [S.DEVICE_CERTIFICATE_NEEDED, S.DEVICE_CERTIFICATE_ENTER],
        ),
        (
            CertificateState(_SERIAL, key_in_use=False),
            [S.DEVICE_CERTIFICATE_NEEDED, S.DEVICE_CERTIFICATE_REPLACE],
        ),
        (
            CertificateState(_SERIAL, key_in_use=True),
            [S.DEVICE_CERTIFICATE_IN_USE, S.DEVICE_CERTIFICATE_REPLACE],
        ),
        (
            CertificateState(None, key_in_use=True),
            [S.DEVICE_CERTIFICATE_IN_USE, S.DEVICE_CERTIFICATE_ENTER],
        ),
    ],
)
def test_the_prompt_follows_the_key_state_and_the_stored_certificate(
    state: CertificateState, expected: list[str]
) -> None:
    assert certificate_prompt(state) == expected


def _project(keys: DeviceKeys | None) -> tuple[ProjectService, list[int]]:
    project = ProjectService(cast(Any, SimpleNamespace(io_lock=threading.RLock())))
    project._pid = "p"  # pyright: ignore[reportPrivateUsage]
    reads: list[int] = []

    def device_keys(device: Any) -> DeviceKeys | None:
        reads.append(device.node_id)
        return keys

    project.device_keys = device_keys  # type: ignore[method-assign]
    project._device_serial = lambda node_id: _SERIAL  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    return project, reads


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (None, (None, False)),
        (DeviceKeys(tool_key=_KEY, loaded_tool_key=None), (None, False)),
        (DeviceKeys(tool_key=_KEY, loaded_tool_key=None, fdsk=_KEY), (_SERIAL, False)),
        (DeviceKeys(tool_key=_KEY, loaded_tool_key=_KEY, fdsk=_KEY), (_SERIAL, True)),
        (DeviceKeys(tool_key=_KEY, loaded_tool_key=_KEY), (None, True)),
    ],
)
def test_certificate_state_of_a_device(
    keys: DeviceKeys | None, expected: tuple[bytes | None, bool]
) -> None:
    project, _ = _project(keys)
    assert project.device_certificate_state(_DEVICE) == expected


def test_certificate_state_is_read_once_per_revision() -> None:
    project, reads = _project(DeviceKeys(tool_key=_KEY, loaded_tool_key=_KEY))
    project.device_certificate_state(_DEVICE)
    project.device_certificate_state(_DEVICE)
    assert reads == [7]
    project._version += 1  # pyright: ignore[reportPrivateUsage]
    project.device_certificate_state(_DEVICE)
    assert reads == [7, 7]
