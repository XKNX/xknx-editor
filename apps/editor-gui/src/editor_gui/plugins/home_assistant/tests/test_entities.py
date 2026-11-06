"""Building functions as entities of Home Assistant's KNX integration."""

from __future__ import annotations

from editor_gui.plugins.home_assistant.entities import (
    addresses_differ,
    entity_candidates,
    entity_request,
    existing_entities,
    function_key,
)
from xknxeditor.proj.core.service import (
    FunctionGroupAddressInfo,
    FunctionInfo,
    SpaceInfo,
)

# Group address id -> (printed address, datapoint type, raw).
_ADDRESSES = {
    1: ("1/1/1", "DPST-1-1", 0x0901),
    2: ("1/1/2", "DPST-5-1", 0x0902),
    3: ("1/1/3", "DPST-1-8", 0x0903),
    4: ("1/1/4", "DPST-1-7", 0x0904),
    5: ("1/1/5", "DPST-5-1", 0x0905),
}


def _lookup(ga_id: int) -> tuple[str | None, int] | None:
    found = _ADDRESSES.get(ga_id)
    return (found[1], found[2]) if found else None


def _function(
    function_id: int, name: str, kind: str, *links: tuple[int, str]
) -> FunctionInfo:
    return FunctionInfo(
        id=function_id,
        name=name,
        function_type=kind,
        usage_text="",
        group_addresses=[
            FunctionGroupAddressInfo(
                id=index, group_address_id=ga, text=_ADDRESSES[ga][0], role=role
            )
            for index, (ga, role) in enumerate(links)
        ],
    )


def _room(*functions: FunctionInfo) -> list[SpaceInfo]:
    room = SpaceInfo(
        id=7,
        name="Kitchen",
        space_type="Room",
        number="",
        usage_text="",
        description="",
        children=[],
        devices=[],
        functions=list(functions),
    )
    house = SpaceInfo(
        id=1,
        name="House",
        space_type="Building",
        number="",
        usage_text="",
        description="",
        children=[room],
        devices=[],
        functions=[],
    )
    return [house]


def test_each_type_and_its_configuration() -> None:
    tree = _room(
        _function(10, "Ceiling", "FT-6", (1, "SwitchOnOff"), (2, "DimmingValue")),
        _function(11, "Socket", "FT-10", (1, "SwitchOnOff")),
        _function(
            12,
            "Blind",
            "FT-7",
            (3, "MoveUpDown"),
            (4, "StopStepUpDown"),
            (5, "CurrentAbsolutePositionBlindsPercentage"),
        ),
        # No entity for heating, nor for a light without its switching address.
        _function(13, "Heating", "FT-9", (2, "ValvePosition")),
        _function(14, "Lamp", "FT-1", (2, "")),
    )
    found = entity_candidates(tree, "g", _lookup)
    assert [(c.name, c.platform) for c in found] == [
        ("Ceiling", "light"),
        ("Socket", "switch"),
        ("Blind", "cover"),
    ]
    ceiling, socket, blind = found
    assert ceiling.knx == {
        "ga_switch": {"write": "1/1/1", "state": None, "passive": []},
        "ga_brightness": {"write": "1/1/2", "state": None, "passive": []},
    }
    assert ceiling.group_addresses == ("1/1/1", "1/1/2")
    assert ceiling.key == function_key("g", 10)
    assert ceiling.space_key == "g:7"
    assert ceiling.path == "House / Kitchen / Ceiling"
    assert ceiling.main_raw == 0x0901
    assert socket.knx == {"ga_switch": {"write": "1/1/1", "state": None, "passive": []}}
    assert blind.knx == {
        "ga_up_down": {"write": "1/1/3", "state": None, "passive": []},
        "ga_step": {"write": "1/1/4", "state": None, "passive": []},
        "ga_position_state": {"write": None, "state": "1/1/5", "passive": []},
    }


def test_roles_not_stored_come_from_the_datapoint_types() -> None:
    tree = _room(_function(10, "Ceiling", "FT-1", (1, ""), (1, "")))
    (light,) = entity_candidates(tree, "g", _lookup)
    # The first 1.001 address switches, the next reports.
    assert light.knx == {
        "ga_switch": {"write": "1/1/1", "state": "1/1/1", "passive": []}
    }
    assert light.group_addresses == ("1/1/1",)


def test_what_is_sent_and_what_exists_already() -> None:
    (light,) = entity_candidates(
        _room(_function(10, "Ceiling", "FT-1", (1, "SwitchOnOff"))), "g", _lookup
    )
    assert entity_request(light, "kitchen") == {
        "key": light.key,
        "platform": "light",
        "name": "Ceiling",
        "knx": light.knx,
        "area_id": "kitchen",
    }
    users = {
        0x0901: [
            {"kind": "entity", "entity_id": "light.old"},
            {"kind": "expose", "name": "sensor.sun state"},
        ]
    }
    assert existing_entities(light, users) == ["light.old"]
    assert existing_entities(light, {}) == []


def test_whether_an_entity_has_other_addresses_than_its_function() -> None:
    wanted = {"ga_switch": {"write": "1/1/1", "state": "1/1/2", "passive": []}}
    assert not addresses_differ(wanted, None)
    # Keys added in Home Assistant (colour, a passive address) do not count.
    assert not addresses_differ(
        wanted,
        {
            "ga_switch": {"write": "1/1/1", "state": "1/1/2", "passive": ["1/1/9"]},
            "ga_color": {"write": "1/1/5"},
            "sync_state": True,
        },
    )
    assert addresses_differ(wanted, {"ga_switch": {"write": "1/1/3", "state": "1/1/2"}})
    assert addresses_differ(wanted, {"ga_switch": {"write": "1/1/1"}})
    assert addresses_differ(wanted, {})
