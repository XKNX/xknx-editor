"""An undo through MCP calculates the device anew, as one in the editor: an Assign target set
directly gets the value of its Assign again, stored as no undo step of its own, and the group
objects that value shows appear."""

from __future__ import annotations

from pathlib import Path

from fastmcp import Client

from xknxeditor.prod.application import parse_application_xml

from .test_server import _build, _data

_APP = "M-00FA_A-0007-01-0000"
_ENABLED = f"{_APP}_P-1_R-1"
_MODE = f"{_APP}_P-3_R-3"
_OTHER = f"{_APP}_P-4_R-4"


def _fixture() -> Path:
    return (
        Path(__file__).parents[7]
        / "packages/prod/tests/fixtures/fan_coil_application.xml"
    )


async def _objects(client: Client, node_id: int) -> list[str]:
    items = (
        await _data(client, "project_list_com_objects", node_id=node_id, compact=True)
    )["items"]
    return sorted(item["ref_id"].rsplit("_", 2)[-2] for item in items)


async def test_an_undo_writes_a_target_set_directly_over(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    app = parse_application_xml(_fixture().read_bytes(), "M-00FA")[0]
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = project.add_device("P-1", "HP-1", "Fan Coil", app)
        assert node_id is not None
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=_ENABLED,
            value="1",
        )
        # The target set directly (as a product's script may), then another edit.
        device = project.find_device_by_node_id(node_id)
        assert device is not None
        project.edit_params(device, [(_MODE, "1")], mode="raw")
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=_OTHER,
            value="1",
        )
        assert "O-3" not in await _objects(client, node_id)
        steps = (await _data(client, "project_history"))["count"]

        assert (await _data(client, "project_undo"))["undone"] is True
        assert "O-3" in await _objects(client, node_id)
        device = project.find_device_by_node_id(node_id)
        assert device is not None
        assert device.get_param_value(_MODE) == "2"
        assert (await _data(client, "project_history"))["count"] == steps
