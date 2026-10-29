"""project_log reads the Project Log of an imported project, filtered and in pages."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.project.service import ProjectTrace

from .test_server import _build, _data


async def test_project_log_filters_and_pages_the_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        # A project created here has no log.
        empty = await _data(client, "project_log")
        assert (empty["items"], empty["count"], empty["total"]) == ([], 0, 0)

        traces = [
            ProjectTrace("2026-01-02", "anna", "stored", comment_plain="Programmed"),
            ProjectTrace("2026-01-03", "ben", "Exported", comment_plain=None),
            ProjectTrace("2026-02-01", "Anna", "stored", comment_plain="Renamed"),
            ProjectTrace("2026-02-02", "carl", "M: QUJD", encrypted=True),
        ]
        monkeypatch.setattr(project, "get_project_traces", lambda: traces)
        log = await _data(client, "project_log")
        assert log["total"] == 4
        assert log["items"][0] == {
            "date": "2026-01-02",
            "user_name": "anna",
            "comment": "Programmed",
            "decrypted": True,
            "encrypted": False,
        }
        # A comment that is not encrypted is returned as stored.
        assert log["items"][1]["comment"] == "Exported"
        assert log["items"][1]["decrypted"] is False
        # One stored encrypted without a key to read it has no text.
        assert log["items"][3]["comment"] is None
        assert log["items"][3]["encrypted"] is True
        assert (await _data(client, "project_log", contains="QUJD"))["total"] == 0

        anna = await _data(client, "project_log", contains="ANNA")
        assert [e["comment"] for e in anna["items"]] == ["Programmed", "Renamed"]
        assert [
            e["comment"]
            for e in (await _data(client, "project_log", contains="2026-02"))["items"]
        ] == ["Renamed", None]
        page = await _data(client, "project_log", offset=1, limit=1)
        assert (page["count"], page["total"]) == (1, 4)
        assert page["items"][0]["user_name"] == "ben"

        with pytest.raises(Exception, match="limit 1 or more"):
            await client.call_tool("project_log", {"limit": 0})
