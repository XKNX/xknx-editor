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


async def test_home_assistants_floors_and_areas_become_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service, link = _server(tmp_path, monkeypatch)
    applied: list[dict[str, Any]] = []

    def send(message: dict[str, Any]) -> bool:
        if message["type"] != "apply_areas":
            return _Link.send(link, message)
        applied.append(message)
        link.inbox.append(
            {"type": "result", "id": message["id"], "error": None, "changed": 3}
        )
        return True

    monkeypatch.setattr(link, "send", send)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        (house,) = (await _data(client, "project_spaces"))["items"]
        await _data(
            client,
            "project_create_space",
            space_type="Floor",
            name="EG",
            parent_id=house["id"],
        )
        link.inbox.append(
            {
                "type": "areas",
                "location_name": "Home",
                "floors": [
                    {"floor_id": "eg", "name": "EG", "level": 0},
                    {"floor_id": "og", "name": "OG", "level": 1},
                ],
                "areas": [
                    {"area_id": "kitchen", "name": "Küche", "floor_id": "eg"},
                    {"area_id": "bath", "name": "Bad", "floor_id": "og"},
                    {"area_id": "garden", "name": "Garten", "floor_id": None},
                ],
                # Linked to a space of another project: not offered.
                "links": {"other:7": "garden"},
            }
        )
        service.poll()
        offered = await _data(client, "home_assistant_spaces_to_add")
        # The project has a floor named EG.
        assert [(s["kind"], s["name"], s["floor_name"]) for s in offered["items"]] == [
            ("floor", "OG", ""),
            ("area", "Küche", "EG"),
            ("area", "Bad", "OG"),
        ]
        assert offered["buildings"] == [
            {"space_id": house["id"], "name": house["name"]}
        ]
        with pytest.raises(Exception, match="no building with space_id 999"):
            await client.call_tool(
                "home_assistant_add_spaces", {"building_space_id": 999}
            )

        result = await _data(client, "home_assistant_add_spaces")
        assert result["added"] == 3
        (house,) = (await _data(client, "project_spaces"))["items"]
        floors = {f["name"]: f for f in house["children"]}
        assert set(floors) == {"EG", "OG"}
        # The kitchen on the floor of the same name, the bath on the floor made with it.
        assert [r["name"] for r in floors["EG"]["children"]] == ["Küche"]
        assert [r["name"] for r in floors["OG"]["children"]] == ["Bad"]
        assert floors["OG"].get("number") == "1"
        # Linked to what they were made from, as they are named there.
        (message,) = applied
        assert [(f["name"], f["floor_id"]) for f in message["floors"]] == [("OG", "og")]
        assert {(a["name"], a["area_id"]) for a in message["areas"]} == {
            ("Küche", "kitchen"),
            ("Bad", "bath"),
        }
        # One undo step takes them all away.
        await _data(client, "project_undo")
        (house,) = (await _data(client, "project_spaces"))["items"]
        assert [f["name"] for f in house["children"]] == ["EG"]


async def test_without_a_building_one_is_made_like_home_assistants_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service, link = _server(tmp_path, monkeypatch)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        (house,) = (await _data(client, "project_spaces"))["items"]
        await _data(client, "project_remove_space", space_id=house["id"])
        link.inbox.append(
            {
                "type": "areas",
                "location_name": "Lake House",
                "floors": [],
                "areas": [{"area_id": "garden", "name": "Garten", "floor_id": None}],
                "links": {},
            }
        )
        service.poll()
        assert (await _data(client, "home_assistant_spaces_to_add"))["buildings"] == []
        assert (await _data(client, "home_assistant_add_spaces"))["added"] == 1
        (building,) = (await _data(client, "project_spaces"))["items"]
        # An area on no floor goes into the building itself.
        assert building["name"] == "Lake House"
        assert [r["name"] for r in building["children"]] == ["Garten"]


async def test_group_addresses_home_assistant_uses_become_the_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service, _link = _server(tmp_path, monkeypatch)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(
            client, "project_create_group_address", name="Known", address="1/2/3"
        )
        light = {"kind": "entity", "entity_id": "light.kitchen", "name": "Kitchen"}
        service.usage = {
            0x0A03: GroupAddressUsage(users=(light,), dpt="1.001"),
            0x0A04: GroupAddressUsage(users=(light,), dpt="1.001"),
            0x0A05: GroupAddressUsage(
                users=({"kind": "expose", "name": "sensor.outside"},), dpt="9.001"
            ),
        }
        offered = await _data(client, "home_assistant_group_addresses_to_add")
        assert offered["items"] == [
            {
                "group_address": "1/2/4",
                "name": "Kitchen",
                "dpt": "1.001",
                "used_by": ["light.kitchen"],
            },
            {
                "group_address": "1/2/5",
                "name": "sensor.outside",
                "dpt": "9.001",
                "used_by": ["sensor.outside"],
            },
        ]
        result = await _data(
            client, "home_assistant_add_group_addresses", group_addresses=["1/2/5"]
        )
        assert result["added"] == 1
        assert [a["group_address"] for a in result["items"]] == ["1/2/4"]
        added = await _data(client, "project_get_group_address", group_address="1/2/5")
        assert (added["name"], added["datapoint_type"]) == (
            "sensor.outside",
            "DPST-9-1",
        )
        await _data(client, "home_assistant_add_group_addresses")
        with pytest.raises(Exception, match="nothing to add"):
            await client.call_tool("home_assistant_add_group_addresses", {})


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


async def test_entities_follow_their_functions_time_server_and_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service, link = _server(tmp_path, monkeypatch)
    # Home Assistant's entities (the knx part of their configuration) by entity id.
    configs: dict[str, dict[str, Any]] = {}
    links: dict[str, str] = {}
    asked: list[dict[str, Any]] = []
    served: dict[str, Any] = {"time": None, "date": None, "datetime": None}

    def state() -> list[dict[str, Any]]:
        return [
            {"type": "entity_links", "links": dict(links), "configs": dict(configs)},
            {
                "type": "knx",
                "loaded": True,
                "project": service.knx_project,
                "time_server": dict(served),
                "keyring_refused": None,
            },
        ]

    def send(message: dict[str, Any]) -> bool:
        kind = message["type"]
        asked.append(message)
        answer: dict[str, Any] = {
            "type": "result",
            "id": message.get("id"),
            "error": None,
        }
        if kind == "check_entities":
            answer["checks"] = {e["key"]: None for e in message["entities"]}
        elif kind == "create_entities":
            for entity in message["entities"]:
                entity_id = f"{entity['platform']}.{entity['name'].lower()}"
                links[entity["key"]] = entity_id
                configs[entity_id] = entity["knx"]
            answer["created"] = {e["key"]: links[e["key"]] for e in message["entities"]}
        elif kind == "update_entities":
            for entity in message["entities"]:
                configs[links[entity["key"]]] = entity["knx"]
            answer["updated"] = [e["key"] for e in message["entities"]]
        elif kind == "delete_entities":
            for key in message["keys"]:
                configs.pop(links.pop(key))
            answer["removed"] = message["keys"]
        elif kind == "set_time_server":
            served.update(message["addresses"])
        elif kind == "set_keyring":
            assert message["keyring"] and message["password"]
        else:
            return _Link.send(link, message)
        link.inbox += [*state(), answer]
        return True

    monkeypatch.setattr(link, "send", send)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        (house,) = (await _data(client, "project_spaces"))["items"]
        room = await _data(
            client,
            "project_create_space",
            space_type="Room",
            name="Hall",
            parent_id=house["id"],
        )
        ids = {}
        for address, dpt in (
            ("1/1/1", "1.001"),
            ("1/1/2", "1.001"),
            ("0/0/1", "10.001"),
        ):
            ga = await _data(
                client,
                "project_create_group_address",
                address=address,
                name=address,
                dpt=dpt,
            )
            ids[address] = ga["group_address_id"]
        function = await _data(
            client,
            "project_create_function",
            space_id=room["space_id"],
            name="Socket",
            function_type="FT-10",
        )
        added = await _data(
            client,
            "project_add_function_group_address",
            function_id=function["function_id"],
            group_address_id=ids["1/1/1"],
            role="SwitchOnOff",
        )
        await _data(client, "home_assistant_create_entities")

        # The function gets another switching address: its entity follows.
        await _data(
            client, "project_remove_function_group_address", link_id=added["link_id"]
        )
        await _data(
            client,
            "project_add_function_group_address",
            function_id=function["function_id"],
            group_address_id=ids["1/1/2"],
            role="SwitchOnOff",
        )
        rows = (await _data(client, "home_assistant_entities"))["items"]
        assert [r["state"] for r in rows] == ["changed"]
        rows = (await _data(client, "home_assistant_update_entities"))["items"]
        assert [r["state"] for r in rows] == ["created"]
        assert configs["switch.socket"]["ga_switch"]["write"] == "1/1/2"

        # The function is removed: its entity can go.
        await _data(
            client, "project_remove_function", function_id=function["function_id"]
        )
        orphans = await _data(client, "home_assistant_orphan_entities")
        assert orphans["items"] == [{"entity_id": "switch.socket"}]
        left = await _data(
            client, "home_assistant_remove_entities", entity_ids=["switch.socket"]
        )
        assert left["count"] == 0
        assert configs == {}

        result = await _data(
            client, "home_assistant_set_time_server", time_address="0/0/1"
        )
        assert result == {"time": "0/0/1", "date": None, "datetime": None}
        assert await _data(client, "home_assistant_send_keys") == {"sent": True}
        assert asked[-1]["type"] == "set_keyring"
