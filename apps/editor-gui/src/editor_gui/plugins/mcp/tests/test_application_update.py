"""The application update through MCP: listed, carried out as in the GUI (old defaults of
unchanged settings kept, stored values winning), and undone in one step."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.project.tests.test_application_update_defaults import (
    NEW,
    OLD,
    STORED,
    project_with_device,
    ref,
)

from .test_server import _build, _data


async def test_the_update_keeps_the_old_defaults_and_undoes_in_one_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, project = _build(tmp_path)
    project_with_device(tmp_path, monkeypatch, project)
    (device,) = project.devices
    async with Client(mcp) as client:
        listed = (await _data(client, "project_devices_with_update"))["items"]
        assert [item["node_id"] for item in listed] == [device.node_id]
        assert listed[0]["available_version"] == 2

        result = await _data(
            client, "project_update_application", node_id=device.node_id
        )
        assert (result["new_version"], result["kept"]) == (2, len(STORED))

        async def value(n: int) -> str:
            data = await _data(
                client,
                "project_get_parameter",
                node_id=device.node_id,
                parameter_ref_id=ref(NEW, n),
            )
            return data["value"]

        # Unchanged with a new default, stored, the same default in both versions.
        assert [await value(n) for n in (1, 4, 3)] == ["0", "2", "2"]

        await _data(client, "project_undo")
        (device,) = project.devices
        assert device.app is not None and device.app.id == OLD
    project.close()
