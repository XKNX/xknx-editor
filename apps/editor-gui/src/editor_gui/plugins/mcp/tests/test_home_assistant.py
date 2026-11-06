"""The Home Assistant menu over MCP: the state, sending the project, automatic updates (the
integration replaced by a link that answers like it)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui import host
from editor_gui.host import HostOptions
from editor_gui.plugins.home_assistant.service import (
    CREATED_BY,
    GroupAddressUsage,
    HomeAssistantService,
)
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.mcp.tools import home_assistant as tools

from .test_server import _build_ctx, _data


class _Link:
    """The integration: it takes every project, or refuses it with ``refuse``."""

    def __init__(self) -> None:
        self.connected = True
        self.generation = 1
        self.inbox: list[dict[str, Any]] = [
            {"type": "knx", "loaded": True, "project": None}
        ]
        self.refuse: str | None = None
        self.projects: list[dict[str, Any]] = []

    def receive(self) -> list[dict[str, Any]]:
        messages, self.inbox = self.inbox, []
        return messages

    def send(self, message: dict[str, Any]) -> bool:
        if message["type"] == "set_project":
            if self.refuse is None:
                self.projects.append(message["project"])
                held = {
                    "guid": message["project"]["info"]["guid"],
                    "created_by": CREATED_BY,
                }
                self.inbox.append({"type": "knx", "loaded": True, "project": held})
            self.inbox.append(
                {"type": "result", "id": message["id"], "error": self.refuse}
            )
        elif message["type"] == "telegram_history":
            self.inbox.append(
                {
                    "type": "result",
                    "id": message["id"],
                    "error": self.refuse,
                    "telegrams": [{"destination": "1/2/3"}, {"destination": "1/2/4"}],
                }
            )
        return True


@pytest.fixture(autouse=True)
def inside_home_assistant() -> Iterator[None]:
    host.set_current(HostOptions(home_assistant=True))
    yield
    host.set_current(HostOptions())


def _server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, HomeAssistantService, _Link]:
    ctx = _build_ctx(tmp_path)
    link = _Link()

    def build(project: Any) -> dict[str, Any]:
        meta = project.get_project_metadata()
        return {"info": {"guid": meta.guid or meta.id}, "group_addresses": {}}

    service = HomeAssistantService(
        ctx.api.project, lambda: link, build=build, prepare=None, persist=False
    )
    ctx.home_assistant = service
    # The GUI thread polls the service each frame; here while a tool waits.
    monkeypatch.setattr(
        tools.time, "sleep", lambda _seconds: ctx.run_locked(service.poll)
    )
    service.poll()
    return build_server(ctx), service, link


async def test_the_state_and_sending_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _service, link = _server(tmp_path, monkeypatch)
    async with Client(mcp) as client:
        status = await _data(client, "home_assistant_status")
        assert status["state"] == "no_project"
        assert status["knx_loaded"] is True
        with pytest.raises(Exception, match="no project is open"):
            await client.call_tool("home_assistant_use_project", {})

        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        status = await _data(client, "home_assistant_status")
        assert status["state"] == "not_linked"
        assert status["project_in_home_assistant"] is None

        status = await _data(client, "home_assistant_use_project")
        assert status["state"] == "up_to_date"
        assert status["linked"] is True
        assert len(link.projects) == 1

        link.refuse = "disk full"
        with pytest.raises(Exception, match="disk full"):
            await client.call_tool("home_assistant_use_project", {})
        assert (await _data(client, "home_assistant_status"))["state"] == "failed"


async def test_nothing_is_sent_without_the_knx_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service, link = _server(tmp_path, monkeypatch)
    link.inbox.append({"type": "knx", "loaded": False, "project": None})
    service.poll()
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        with pytest.raises(Exception, match="knx_missing"):
            await client.call_tool("home_assistant_use_project", {})
    assert link.projects == []


async def test_outside_home_assistant_the_tools_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, _service, _link = _server(tmp_path, monkeypatch)
    host.set_current(HostOptions())
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="does not run inside Home Assistant"):
            await client.call_tool("home_assistant_status", {})


async def test_what_home_assistant_does_with_a_group_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service, _link = _server(tmp_path, monkeypatch)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        created = await _data(
            client, "project_create_group_address", name="Kitchen", address="1/2/3"
        )
        light = {"kind": "entity", "entity_id": "light.kitchen", "state": "on"}
        service.usage = {0x0A03: GroupAddressUsage(users=(light,), sends=True)}
        usage = await _data(
            client, "home_assistant_group_address_usage", group_address="1/2/3"
        )
        assert usage == {
            "group_address_id": created["group_address_id"],
            "address": "1/2/3",
            "known": True,
            "sends": True,
            "users": [light],
        }
        service.usage = {}
        usage = await _data(
            client,
            "home_assistant_group_address_usage",
            group_address_id=created["group_address_id"],
        )
        assert (usage["sends"], usage["users"]) == (False, [])


async def test_home_assistants_telegrams_are_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service, link = _server(tmp_path, monkeypatch)
    histories: list[int] = []
    service.on_history = lambda telegrams: histories.append(len(telegrams))
    async with Client(mcp) as client:
        assert await _data(client, "home_assistant_load_telegrams", hours=2) == {
            "loaded": 2
        }
        # The Group Monitor's own way of taking them stays.
        assert histories == [2]
        link.refuse = "store busy"
        with pytest.raises(Exception, match="store busy"):
            await client.call_tool("home_assistant_load_telegrams", {})
        with pytest.raises(Exception, match="at least 1"):
            await client.call_tool("home_assistant_load_telegrams", {"hours": 0})


async def test_the_buildings_become_floors_and_areas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service, link = _server(tmp_path, monkeypatch)
    applied: list[dict[str, Any]] = []
    # Home Assistant's floors and areas by id, and the links, as they grow.
    floors: dict[str, dict[str, Any]] = {}
    areas: dict[str, dict[str, Any]] = {}
    links: dict[str, str] = {}

    def send(message: dict[str, Any]) -> bool:
        if message["type"] != "apply_areas":
            return _Link.send(link, message)
        applied.append(message)
        for floor in message["floors"]:
            floor_id = f"f{len(floors)}"
            floors[floor_id] = {
                "floor_id": floor_id,
                "name": floor["name"],
                "level": None,
            }
            links[floor["key"]] = floor_id
        for area in message["areas"]:
            area_id = f"a{len(areas)}"
            floor_id = links.get(area["floor_key"] or "")
            areas[area_id] = {
                "area_id": area_id,
                "name": area["name"],
                "floor_id": floor_id,
            }
            links[area["key"]] = area_id
        link.inbox += [
            {
                "type": "areas",
                "floors": list(floors.values()),
                "areas": list(areas.values()),
                "links": dict(links),
            },
            {
                "type": "result",
                "id": message["id"],
                "error": None,
                "changed": len(message["floors"]) + len(message["areas"]),
            },
        ]
        return True

    monkeypatch.setattr(link, "send", send)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        # A new project has its building.
        (house,) = (await _data(client, "project_spaces"))["items"]
        floor = await _data(
            client,
            "project_create_space",
            space_type="Floor",
            name="Ground Floor",
            parent_id=house["id"],
        )
        kitchen = await _data(
            client,
            "project_create_space",
            space_type="Room",
            name="Kitchen",
            parent_id=floor["space_id"],
        )
        areas = await _data(client, "home_assistant_areas")
        assert [(r["kind"], r["name"], r["state"]) for r in areas["items"]] == [
            ("floor", "Ground Floor", "new"),
            ("area", "Kitchen", "new"),
        ]
        assert areas["items"][1]["floor_space_id"] == floor["space_id"]

        # Only the kitchen: its floor is not sent.
        result = await _data(
            client, "home_assistant_apply_areas", space_ids=[kitchen["space_id"]]
        )
        assert [a["name"] for a in applied[0]["areas"]] == ["Kitchen"]
        assert applied[0]["floors"] == []
        assert result["changed"] == 1

        result = await _data(client, "home_assistant_apply_areas")
        # The kitchen is linked: only its floor is left.
        assert result["changed"] == 1
        assert [f["name"] for f in applied[1]["floors"]] == ["Ground Floor"]
        assert applied[1]["areas"] == []
        assert {r["state"] for r in result["items"]} == {"linked"}
        with pytest.raises(Exception, match="nothing to apply"):
            await client.call_tool("home_assistant_apply_areas", {})
    assert service.home_assistant_areas is not None


async def test_the_building_functions_become_entities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service, link = _server(tmp_path, monkeypatch)
    created: list[dict[str, Any]] = []
    links: dict[str, str] = {}

    def send(message: dict[str, Any]) -> bool:
        if message["type"] == "check_entities":
            # Home Assistant does not take a switch named "Broken".
            checks = {
                e["key"]: ("invalid" if e["name"] == "Broken" else None)
                for e in message["entities"]
            }
            link.inbox.append(
                {"type": "result", "id": message["id"], "error": None, "checks": checks}
            )
            return True
        if message["type"] == "create_entities":
            created.extend(message["entities"])
            for entity in message["entities"]:
                links[entity["key"]] = f"{entity['platform']}.{entity['name'].lower()}"
            link.inbox += [
                {"type": "entity_links", "links": dict(links)},
                {
                    "type": "result",
                    "id": message["id"],
                    "error": None,
                    "created": {e["key"]: links[e["key"]] for e in message["entities"]},
                    "errors": {},
                },
            ]
            return True
        return _Link.send(link, message)

    monkeypatch.setattr(link, "send", send)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        (house,) = (await _data(client, "project_spaces"))["items"]
        room = await _data(
            client,
            "project_create_space",
            space_type="Room",
            name="Kitchen",
            parent_id=house["id"],
        )
        addresses = {}
        for address, dpt in (
            ("1/1/1", "1.001"),
            ("1/1/2", "5.001"),
            ("1/1/3", "1.001"),
        ):
            ga = await _data(
                client,
                "project_create_group_address",
                address=address,
                name=address,
                dpt=dpt,
            )
            addresses[address] = ga["group_address_id"]

        async def function(name: str, kind: str, *links_: tuple[str, str]) -> int:
            made = await _data(
                client,
                "project_create_function",
                space_id=room["space_id"],
                name=name,
                function_type=kind,
            )
            for address, role in links_:
                await _data(
                    client,
                    "project_add_function_group_address",
                    function_id=made["function_id"],
                    group_address_id=addresses[address],
                    role=role,
                )
            return made["function_id"]

        ceiling = await function(
            "Ceiling", "FT-6", ("1/1/1", "SwitchOnOff"), ("1/1/2", "DimmingValue")
        )
        await function("Broken", "FT-10", ("1/1/3", "SwitchOnOff"))
        # Sun protection without its up/down address: no entity.
        await function("Blind", "FT-7")

        entities = (await _data(client, "home_assistant_entities"))["items"]
        assert [(e["name"], e["platform"], e["state"]) for e in entities] == [
            ("Ceiling", "light", "new"),
            ("Broken", "switch", "not_possible"),
        ]
        assert entities[0]["knx"] == {
            "ga_switch": {"write": "1/1/1", "state": None, "passive": []},
            "ga_brightness": {"write": "1/1/2", "state": None, "passive": []},
        }
        assert entities[1]["reason"] == "invalid"

        result = await _data(client, "home_assistant_create_entities")
        assert result["created"] == 1
        assert [e["name"] for e in created] == ["Ceiling"]
        assert result["items"][0]["state"] == "created"
        assert result["items"][0]["entity_id"] == "light.ceiling"
        with pytest.raises(Exception, match="nothing to create"):
            await client.call_tool(
                "home_assistant_create_entities", {"function_ids": [ceiling]}
            )
    assert service.entity_links
