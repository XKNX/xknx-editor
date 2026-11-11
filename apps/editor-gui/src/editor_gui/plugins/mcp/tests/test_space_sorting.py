"""Building order shared by MCP and the GUI, including edits to a cached tree."""

from pathlib import Path

from fastmcp import Client

from .test_server import _build, _data


async def test_space_names_sorted_after_rename_and_undo(tmp_path: Path) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        building = (await _data(client, "project_spaces"))["items"][0]["id"]
        rooms = {}
        for name in ("Zebra", "Room 10", "Äpfel", "room 9"):
            rooms[name] = (
                await _data(
                    client,
                    "project_create_space",
                    space_type="Room",
                    name=name,
                    parent_id=building,
                )
            )["space_id"]

        async def names() -> list[str]:
            tree = (await _data(client, "project_spaces"))["items"]
            result = [s["name"] for s in tree[0]["children"]]
            assert result == [s.name for s in project.get_space_tree()[0].children]
            return result

        assert await names() == ["Äpfel", "room 9", "Room 10", "Zebra"]
        await _data(
            client, "project_rename_space", space_id=rooms["Zebra"], name="Abstellraum"
        )
        assert await names() == ["Abstellraum", "Äpfel", "room 9", "Room 10"]
        await _data(client, "project_undo")
        assert await names() == ["Äpfel", "room 9", "Room 10", "Zebra"]
    project.close()
