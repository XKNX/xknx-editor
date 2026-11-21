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


@pytest.mark.parametrize("mdt", [True, False])
async def test_replacement_mcp_requires_revision_and_saves_readback(
    tmp_path, monkeypatch, mdt
):
    import asyncio
    from concurrent.futures import Future
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from xknxeditor.dali.model import DaliBusState, EcgState
    from xknxeditor.dali.profiles import PROFILES

    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    data = (
        Path(__file__).parents[7]
        / "packages/dali/tests/fixtures/ipas_e64pro_backup.dat"
    ).read_bytes()
    channel = 1 if mdt else 0
    profile = PROFILES[0 if mdt else 2]
    gateway = SimpleNamespace(
        node_id=7,
        individual_address="1.1.20",
        app=SimpleNamespace(id=profile.app_id_prefixes[0] + "21-1234"),
        binary_data=[]
        if mdt
        else [SimpleNamespace(name="Dalie64-Backup-Store", data=data)],
    )
    monkeypatch.setattr(project, "find_device_by_node_id", lambda _: gateway)
    monkeypatch.setattr(type(project), "devices", property(lambda _: [gateway]))
    monkeypatch.setattr(
        project,
        "get_device_info",
        lambda _: SimpleNamespace(order_number="SCN-DA642.04" if mdt else ""),
    )
    monkeypatch.setattr(ctx.api.connection, "_xknx", object())
    scan = DaliBusState(
        channel=channel,
        ecgs=[
            EcgState(
                slot=3,
                present=True,
                ecg_type=6,
                group_index=16,
                ets_ecg_index=9,
                long_address=0xABCDEF,
            )
        ],
    )
    commissioner = SimpleNamespace(
        connection=SimpleNamespace(
            profile=profile,
            channel=channel,
            read_table=AsyncMock(return_value=bytes(384)),
        ),
        replace_ecg=AsyncMock(return_value=scan),
        read_scenes=AsyncMock(return_value=[]),
        read_templates=AsyncMock(return_value=[]),
    )
    calls, saved = [], []

    def run_dali(device, op, *, channel=0, writes=True):
        op.validate_device(device)
        calls.append((channel, writes))
        future = Future()
        future.set_result(asyncio.run(op(commissioner)))
        return future

    monkeypatch.setattr(ctx.api.connection, "run_dali", run_dali)
    monkeypatch.setattr(
        project, "persist_dali_backup", lambda *args, **kwargs: saved.append(args)
    )
    async with Client(build_server(ctx)) as client:
        state = await _data(
            client,
            "project_dali_replacement_state",
            device_address="1.1.20",
            channel=channel,
        )
        assert state["has_backup"] is not mdt
        assert not calls
        with pytest.raises(Exception, match="revision changed"):
            await _data(
                client,
                "connection_dali_replace",
                node_id=7,
                channel=channel,
                revision="stale",
            )
        assert not calls
        result = await _data(
            client,
            "connection_dali_replace",
            node_id=7,
            channel=channel,
            revision=state["revision"],
        )
        assert result["channel"] == channel and calls == [(channel, True)]
        assert len(saved) == 1
        root = ET.fromstring(saved[0][2])
        assert root.find("devecgs/DevECG[@index='3']").get("long-address") == "ABCDEF"


async def test_dali_workspace_mcp_uses_revision_and_shared_operations(
    tmp_path, monkeypatch
):
    import asyncio
    from concurrent.futures import Future
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    root = Path(__file__).parents[7]
    data = (root / "packages/dali/tests/fixtures/ipas_e64pro_backup.dat").read_bytes()
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    gateway = SimpleNamespace(
        node_id=7,
        individual_address="1.1.20",
        app=SimpleNamespace(id="M-00EF_A-DA11-22-A545"),
        binary_data=[SimpleNamespace(name="Dalie64-Backup-Store", data=data)],
    )
    monkeypatch.setattr(project, "find_device_by_node_id", lambda _: gateway)
    monkeypatch.setattr(type(project), "devices", property(lambda _: [gateway]))
    monkeypatch.setattr(
        project, "get_device_info", lambda _: SimpleNamespace(order_number="")
    )
    monkeypatch.setattr(ctx.api.connection, "_xknx", object())
    commissioner = SimpleNamespace(
        test_scene=AsyncMock(),
        read_inputs=AsyncMock(return_value=()),
        test_template_command=AsyncMock(),
    )
    calls = []
    saved = []

    def run_dali(device, op, *, channel=0, writes=True):
        op.validate_device(device)
        calls.append(writes)
        future = Future()
        future.set_result(asyncio.run(op(commissioner)))
        return future

    monkeypatch.setattr(ctx.api.connection, "run_dali", run_dali)
    monkeypatch.setattr(
        project, "apply_e64pro_readback", lambda *args: saved.append(args)
    )
    async with Client(build_server(ctx)) as client:
        revision = e64pro.revision(data)
        await _data(
            client,
            "connection_dali_workspace",
            node_id=7,
            revision=revision,
            action="test",
            section="scenes",
            index=15,
        )
        commissioner.test_scene.assert_awaited_once_with(15)
        await _data(
            client,
            "connection_dali_workspace",
            device_address="1.1.20",
            revision=revision,
            action="read",
            section="inputs",
        )
        assert calls == [True, False] and len(saved) == 1
        await _data(
            client,
            "connection_dali_workspace",
            device_address="1.1.20",
            revision=revision,
            action="test",
            section="templates",
            index=0,
            row=0,
            test_group=15,
        )
        args, kwargs = commissioner.test_template_command.await_args
        assert args[0].index == 0 and args[1] == 0 and kwargs == {"group": 15}
        assert calls == [True, False, True] and len(saved) == 1
        for action, section, group in [
            ("test", "templates", 16),
            ("read", "inputs", 0),
        ]:
            with pytest.raises(Exception, match="unavailable"):
                await _data(
                    client,
                    "connection_dali_workspace",
                    node_id=7,
                    revision=revision,
                    action=action,
                    section=section,
                    test_group=group,
                )
        for action, rev in [
            ("test", "stale"),
            ("replace", revision),
            ("restore", revision),
        ]:
            with pytest.raises(Exception, match=r"revision|use connection"):
                await _data(
                    client,
                    "connection_dali_workspace",
                    node_id=7,
                    revision=rev,
                    action=action,
                )
        assert len(calls) == 3


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

            latest = await _data(client, "project_e64pro_read", node_id=node_id)
            before_calls = project.cursor
            await _data(
                client,
                "project_batch",
                operations=[
                    {
                        "op": "edit_e64pro",
                        "params": {
                            "node_id": node_id,
                            "revision": latest["revision"],
                            "edits": [
                                {
                                    "section": "templates",
                                    "slot": "0",
                                    "action": "set",
                                    "row": 0,
                                    "field": "func",
                                    "value": "CallEffect",
                                },
                                {
                                    "section": "templates",
                                    "slot": "0",
                                    "action": "set",
                                    "row": 0,
                                    "field": "value",
                                    "value": "15",
                                },
                                {
                                    "section": "templates",
                                    "slot": "0",
                                    "action": "set",
                                    "row": 0,
                                    "field": "scaleMode",
                                    "value": "ScaleNoneEffectStart",
                                },
                            ],
                        },
                    }
                ],
            )
            latest = await _data(client, "project_e64pro_read", node_id=node_id)
            command = ET.fromstring(latest["xml"]).find("templates/Tpl/Commands/TplCmd")
            assert (
                command.get("func"),
                command.get("value"),
                command.get("scaleMode"),
            ) == ("CallEffect", "15", "ScaleNoneEffectStart")
            assert project.cursor == before_calls + 1
            project.undo()

            for section in ("scenes", "effects", "templates"):
                slot_xml = await _data(
                    client,
                    "project_e64pro_export_slot",
                    node_id=node_id,
                    section=section,
                    slot="0",
                )
                slot_file = tmp_path / f"{section}.xml"
                slot_file.write_text(slot_xml)
                before_import = project.cursor
                await _data(
                    client,
                    "project_e64pro_import_slot",
                    node_id=node_id,
                    section=section,
                    slot="1",
                    revision=updated["revision"],
                    path=str(slot_file),
                )
                assert project.cursor == before_import + 1
                assert project.history()[0].display_text
                project.undo()
                assert (
                    await _data(client, "project_e64pro_read", node_id=node_id)
                    == updated
                )

            await _data(
                client,
                "project_batch",
                operations=[
                    {
                        "op": "edit_e64pro",
                        "params": {
                            "node_id": node_id,
                            "revision": updated["revision"],
                            "edits": [
                                {
                                    "section": "groups",
                                    "slot": "0",
                                    "action": "description",
                                    "value": "x" * 60,
                                },
                                {
                                    "section": "ecgs",
                                    "slot": "0",
                                    "action": "description",
                                    "value": "Lamp",
                                },
                            ],
                        },
                    }
                ],
            )
            descriptions = await _data(client, "project_e64pro_read", node_id=node_id)
            assert (
                f'description="{"x" * 60}"' in descriptions["xml"]
                and 'description="Lamp"' in descriptions["xml"]
            )
            before = project.cursor
            with pytest.raises(Exception, match="60"):
                await _data(
                    client,
                    "project_e64pro_edit",
                    node_id=node_id,
                    revision=descriptions["revision"],
                    section="groups",
                    slot="0",
                    action="description",
                    value="x" * 61,
                )
            assert project.cursor == before
            assert (
                await _data(client, "project_e64pro_read", node_id=node_id)
                == descriptions
            )
            project.undo()
            assert (
                await _data(client, "project_e64pro_read", node_id=node_id) == updated
            )

            from concurrent.futures import Future

            descriptions_xml = await _data(
                client,
                "project_e64pro_export_descriptions",
                node_id=node_id,
                section="groups",
            )
            assert ET.fromstring(descriptions_xml).tag == "GRP_TEXT"
            cursor = project.cursor
            description_edit = {
                "section": "groups",
                "slot": "",
                "action": "import_descriptions",
                "field": "txt",
                "value": "Imported group\n''\n",
            }
            revision = await _data(
                client,
                "project_e64pro_edit",
                node_id=node_id,
                revision=updated["revision"],
                **description_edit,
            )
            assert project.cursor == cursor + 1
            text = await _data(
                client,
                "project_e64pro_export_descriptions",
                node_id=node_id,
                section="groups",
                format="txt",
            )
            assert text.startswith("Imported group\r\n''\r\n")
            with pytest.raises(Exception, match="revision changed"):
                await _data(
                    client,
                    "project_e64pro_edit",
                    node_id=node_id,
                    revision=updated["revision"],
                    **description_edit,
                )
            await _data(
                client,
                "project_batch",
                operations=[
                    {
                        "op": "edit_e64pro",
                        "params": {
                            "node_id": node_id,
                            "revision": revision,
                            "edits": [
                                {
                                    "section": "templates",
                                    "slot": "0",
                                    "action": "insert",
                                    "row": 0,
                                },
                                {
                                    "section": "templates",
                                    "slot": "0",
                                    "action": "sort_function",
                                },
                                {
                                    "section": "templates",
                                    "slot": "0",
                                    "action": "clear",
                                },
                                {
                                    "section": "groups",
                                    "slot": "",
                                    "action": "clear_descriptions",
                                },
                            ],
                        },
                    }
                ],
            )
            assert project.cursor == cursor + 2
            cleared = ET.fromstring(
                (await _data(client, "project_e64pro_read", node_id=node_id))["xml"]
            )
            assert not cleared.findall("templates/Tpl/Commands/TplCmd")
            assert all(
                not item.get("description", "") for item in cleared.findall("groups/*")
            )
            project.undo()
            project.undo()
            assert (
                await _data(client, "project_e64pro_read", node_id=node_id) == updated
            )

            current = project.find_device_by_node_id(node_id)
            input_config = ET.fromstring(updated["xml"])
            logical = input_config.find("gis/GenInput/Instances/EtsInstance")
            assert logical is not None
            logical.set("type", "0")
            logical.set("ref-idx", "-1")
            logical.set("origin", "-1")
            physical = input_config.find("inputs")
            if physical is None:
                physical = ET.SubElement(input_config, "inputs")
            parent = ET.SubElement(
                physical, "DevInput", index="0", noi="1", **{"start-index": "10"}
            )
            instances = ET.SubElement(parent, "Instances")
            ET.SubElement(
                instances,
                "DevInputInstance",
                index="10",
                number="0",
                type="ValueInSensor",
                target="-1",
                **{"ets-index": "255", "ets-detail": "0"},
            )
            project.persist_device_blob(
                current,
                "Dalie64-Backup-Store",
                ET.tostring(input_config, encoding="unicode").encode("utf-16le"),
            )
            initial_inputs = await _data(client, "project_e64pro_read", node_id=node_id)
            await _data(
                client,
                "project_e64pro_edit",
                node_id=node_id,
                revision=initial_inputs["revision"],
                section="gis",
                slot="0",
                action="assign",
                row=0,
                index="10",
            )
            assigned = await _data(client, "project_e64pro_read", node_id=node_id)
            assigned_xml = ET.fromstring(assigned["xml"])
            assert (
                assigned_xml.find("gis/GenInput/Instances/EtsInstance").get("ref-idx")
                == "10"
            )
            assert (
                assigned_xml.find("inputs/DevInput/Instances/DevInputInstance").get(
                    "ets-index"
                )
                == "2"
            )
            project.undo()
            assert (
                await _data(client, "project_e64pro_read", node_id=node_id)
                == initial_inputs
            )
            project.undo()
            assert (
                await _data(client, "project_e64pro_read", node_id=node_id) == updated
            )

            from xknxeditor.dali.report import DiagnosticReport, FailureSummary

            report = DiagnosticReport(
                "2026-11-20T12:00:00+00:00", FailureSummary.decode(bytes(10)), ()
            )
            calls = []

            def run_report(device, op, *, channel, writes):
                calls.append((channel, writes))
                future = Future()
                future.set_result(report)
                return future

            monkeypatch.setattr(ctx.api.connection, "can_reach_bus", lambda: True)
            monkeypatch.setattr(ctx.api.connection, "run_dali", run_report)
            # Give the device an address so it can be used by a bus operation.
            monkeypatch.setattr(
                project.find_device_by_node_id(node_id), "individual_address", "1.1.1"
            )
            report_result = await _data(
                client, "connection_dali_report", node_id=node_id
            )
            assert report_result["summary"]["lamps"] == 0
            assert calls == [(0, False)]
            cached = await _data(
                client, "connection_dali_report", node_id=node_id, refresh=False
            )
            assert cached == report_result and calls == [(0, False)]

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
