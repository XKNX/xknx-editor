"""project_move_line and project_move_segment: a sub line into another area and a sub-segment
onto another line, as their "Move to" does; refusals with the editor's reasons."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from .test_segments import _line
from .test_server import _build, _data


async def test_a_line_and_a_segment_move(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        area = (await _data(client, "project_create_area", area_number=2, name="B"))[
            "area_id"
        ]
        # The area comes with its main line 2.0.
        await _data(client, "project_create_line", area_id=area, line_number=1)
        line = await _line(client, "1.1")
        segment = (await _data(client, "project_add_segment", line_id=line["id"]))["id"]
        # 2.1 is taken: the line becomes 2.2, with its segment.
        moved = await _data(
            client, "project_move_line", line_id=line["id"], area_id=area
        )
        assert (moved["address"], moved["area_id"]) == ("2.2", area)
        assert [s["id"] for s in moved["segments"]] == [segment]
        # The segment onto 2.1.
        target = await _line(client, "2.1")
        landed = await _data(
            client, "project_move_segment", segment_id=segment, line_id=target["id"]
        )
        assert landed["address"] == "2.1"
        assert [(s["id"], s["number"]) for s in landed["segments"]] == [(segment, 1)]
        assert (await _line(client, "2.2"))["segments"] == []
        await _data(client, "project_undo")
        assert [s["id"] for s in (await _line(client, "2.2"))["segments"]] == [segment]
        # A main line stays, and a line is moved only into another area.
        main = await _line(client, "2.0")
        with pytest.raises(Exception, match="main line cannot be moved"):
            await client.call_tool(
                "project_move_line", {"line_id": main["id"], "area_id": area}
            )
        with pytest.raises(Exception, match="already assigned to an area"):
            await client.call_tool(
                "project_move_line", {"line_id": line["id"], "area_id": area}
            )
        with pytest.raises(Exception, match="main segment cannot be moved"):
            await client.call_tool(
                "project_move_segment",
                {"segment_id": main["main_segment_id"], "line_id": target["id"]},
            )
