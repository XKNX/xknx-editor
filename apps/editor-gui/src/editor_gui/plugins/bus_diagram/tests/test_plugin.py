"""The plugin's reads for the Links view: the group objects of a device, with their channels once
its application is loaded, cached until the project changes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui.plugins.bus_diagram import plugin as plugin_module
from editor_gui.plugins.bus_diagram.flow import GroupObject
from editor_gui.plugins.bus_diagram.plugin import BusDiagramPlugin


def test_group_objects_get_their_channels_once_the_application_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = SimpleNamespace(node_id=1)
    project = SimpleNamespace(
        loading=False,
        revision=1,
        devices=[device],
        find_device_by_node_id=lambda nid: device if nid == 1 else None,
        ready=False,
        application_ready=lambda _d: project.ready,
        get_space_tree=list,
        is_open=True,
        take_bus_diagram_request=lambda: None,
    )
    reads: list[bool] = []

    def group_objects(
        _device: Any, with_channels: bool = True
    ) -> dict[int, GroupObject]:
        reads.append(with_channels)
        return {11: GroupObject(1, "Switch", "CH-1" if with_channels else "")}

    monkeypatch.setattr(plugin_module, "device_group_objects", group_objects)
    plugin = BusDiagramPlugin(SimpleNamespace(project=project))  # pyright: ignore[reportArgumentType]
    read = plugin._group_objects  # pyright: ignore[reportPrivateUsage]

    # Still loading: without channels, read once.
    assert read(1)[11].channel == ""
    assert read(1)[11].channel == ""
    assert reads == [False]
    # Loaded: read again with the channels, then from the cache.
    project.ready = True
    assert read(1)[11].channel == "CH-1"
    assert read(1)[11].channel == "CH-1"
    assert reads == [False, True]
    # An edit reads them again; an unknown device has none.
    project.revision = 2
    read(1)
    assert reads == [False, True, True]
    assert read(9) == {}
    # While a project loads there is nothing to read.
    project.loading = True
    assert read(1) == {}
