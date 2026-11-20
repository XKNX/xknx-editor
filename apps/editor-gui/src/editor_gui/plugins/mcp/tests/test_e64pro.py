from dataclasses import replace
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from fastmcp import Client

from editor_gui.plugin_blob_readers.dali_backup import e64pro
from editor_gui.plugins.mcp.server import build_server
from xknxeditor.prod import parse_application_xml
from xknxeditor.prod.application import Application

from .test_server import _build_ctx, _data


async def test_e64pro_mcp_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[7]
    sample = parse_application_xml(
        (root / "packages/download/tests/fixtures/powertool/21.xml").read_bytes(),
        "M-0007",
    )[0]
    app = Application(
        replace(sample.program, id="M-00EF_A-DA11-22-A545"), sample.version, "M-00EF"
    )
    data = (root / "packages/dali/tests/fixtures/ipas_e64pro_backup.dat").read_bytes()
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.new(tmp_path / "project.xknx")
    monkeypatch.setattr(project, "_resolve_app", lambda *args: app)
    monkeypatch.setattr(project, "_application_source", lambda *args: None)
    try:
        node_id = project.add_device("P-1", "HP-1", "e64 Pro", app)
        assert node_id is not None
        device = project.find_device_by_node_id(node_id)
        assert device is not None
        project.persist_device_blob(device, "Dalie64-Backup-Store", data)
        async with Client(build_server(ctx)) as client:
            initial: Any = await _data(client, "project_e64pro_read", node_id=node_id)
            assert "TplCmd" in initial["xml"]
            edit = {
                "section": "templates",
                "slot": "0",
                "action": "set",
                "row": 0,
                "field": "weekdays",
                "value": "0111111",
            }
            await _data(
                client,
                "project_e64pro_edit",
                node_id=node_id,
                revision=initial["revision"],
                **edit,
            )
            with pytest.raises(Exception, match="revision changed"):
                await _data(
                    client,
                    "project_e64pro_edit",
                    node_id=node_id,
                    revision=initial["revision"],
                    **edit,
                )
            updated: Any = await _data(client, "project_e64pro_read", node_id=node_id)
            before = project.cursor
            ops = [
                {
                    "op": "edit_e64pro",
                    "params": {
                        "node_id": node_id,
                        "revision": updated["revision"],
                        "edits": [
                            dict(edit, field="time", value="07:15"),
                            dict(edit, value="0011111"),
                        ],
                    },
                }
            ]
            await _data(client, "project_batch", operations=ops)
            assert project.cursor == before + 1
            device = project.find_device_by_node_id(node_id)
            assert device is not None
            entry = e64pro.backup(device)
            assert entry is not None
            row = e64pro.document(entry[1]).find("templates/Tpl/Commands/TplCmd")
            assert row is not None
            assert (row.attrib["time"], row.attrib["weekdays"]) == ("07:15", "0011111")
            project.undo()
            undone = await _data(client, "project_e64pro_read", node_id=node_id)
            assert undone == updated
            xml = ET.fromstring(updated["xml"])
            scene = xml.find("scenes/Scene")
            assert scene is not None
            scene.set("description", "Imported scene")
            path = tmp_path / "configuration.xml"
            path.write_text(ET.tostring(xml, encoding="unicode"))
            before = project.cursor
            with pytest.raises(Exception, match="exactly one"):
                await _data(
                    client,
                    "project_e64pro_import",
                    node_id=node_id,
                    revision=updated["revision"],
                    path=str(path),
                    xml=path.read_text(),
                )
            with pytest.raises(Exception, match="exactly one"):
                await _data(
                    client,
                    "project_e64pro_import",
                    node_id=node_id,
                    revision=updated["revision"],
                )
            revision = await _data(
                client,
                "project_e64pro_import",
                node_id=node_id,
                revision=updated["revision"],
                path=str(path),
            )
            imported: Any = await _data(client, "project_e64pro_read", node_id=node_id)
            assert imported["revision"] == revision
            assert "Imported scene" in imported["xml"]
            exported: Any = await _data(
                client, "project_e64pro_read", node_id=node_id, as_export=True
            )
            assert exported["revision"] == revision
            assert exported["xml"] == project.export_e64pro(project.devices[0]).decode(
                "utf-8"
            )
            assert project.cursor == before + 1
            project.undo()
            assert (
                await _data(client, "project_e64pro_read", node_id=node_id) == updated
            )

            capacity = ET.fromstring(updated["xml"])
            templates = capacity.findall("templates/Tpl")
            for template in templates:
                commands = template.find("Commands")
                if commands is not None:
                    commands.clear()
            commands = templates[0].find("Commands")
            if commands is None:
                commands = ET.SubElement(templates[0], "Commands")
            for _ in range(299):
                ET.SubElement(
                    commands,
                    "TplCmd",
                    func="SetValue",
                    value="0",
                    time="00:00",
                    weekdays="1111111",
                    fadeIn="VFadeIn0",
                )
            await _data(
                client,
                "project_e64pro_import",
                node_id=node_id,
                revision=updated["revision"],
                xml=ET.tostring(capacity, encoding="unicode"),
            )
            limited = await _data(client, "project_e64pro_read", node_id=node_id)
            before = project.cursor
            add = {"section": "templates", "slot": "0", "action": "add"}
            copy = dict(add, action="copy", row=0)
            rejected = await _data(
                client,
                "project_batch",
                operations=[
                    {
                        "op": "edit_e64pro",
                        "params": {
                            "node_id": node_id,
                            "revision": limited["revision"],
                            "edits": [add, copy],
                        },
                    }
                ],
            )
            assert rejected["failed"] == 1
            assert rejected["results"][0]["status"] == "error"
            assert "At most 300" in rejected["results"][0]["error"]
            assert project.cursor == before
            assert (
                await _data(client, "project_e64pro_read", node_id=node_id) == limited
            )
            await _data(
                client,
                "project_e64pro_edit",
                node_id=node_id,
                revision=limited["revision"],
                **add,
            )
            full = await _data(client, "project_e64pro_read", node_id=node_id)
            before = project.cursor
            with pytest.raises(Exception, match="At most 300"):
                await _data(
                    client,
                    "project_e64pro_edit",
                    node_id=node_id,
                    revision=full["revision"],
                    **copy,
                )
            too_many = ET.fromstring(full["xml"])
            commands = too_many.find("templates/Tpl/Commands")
            assert commands is not None
            ET.SubElement(commands, "TplCmd", func="SetValue", value="0")
            with pytest.raises(Exception, match="At most 300"):
                await _data(
                    client,
                    "project_e64pro_import",
                    node_id=node_id,
                    revision=full["revision"],
                    xml=ET.tostring(too_many, encoding="unicode"),
                )
            assert project.cursor == before
            assert await _data(client, "project_e64pro_read", node_id=node_id) == full
    finally:
        project.close()
