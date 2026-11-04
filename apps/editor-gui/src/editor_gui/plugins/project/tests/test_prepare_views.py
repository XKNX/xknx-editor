"""What the views show right after a project opened is read on the worker that opened it: the
first frame finds it in the caches."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from editor_gui.plugins.project.tests.test_application_change import _setup


def test_the_views_read_from_the_caches_afterwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cat, proj, _db = _setup(tmp_path)
    (device,) = proj.devices
    proj.prepare_views()

    def no_read(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("read from the project file")

    for name in (
        "group_addresses",
        "device_links",
        "device",
        "project",
        "project_traces",
        "topology",
    ):
        monkeypatch.setattr(proj._svc, name, no_read)
    _ = proj.group_addresses
    _ = proj.group_address_style
    proj.device_links()
    proj.device_lines()
    assert proj.has_project_log() is False
    proj.get_project_metadata()
    assert proj.get_device_info(device.node_id) is not None
    monkeypatch.undo()
    proj.close()


def test_nothing_to_prepare_without_a_project(tmp_path: Path) -> None:
    _cat, proj, _db = _setup(tmp_path)
    proj.close()
    proj.prepare_views()
