"""History entries name what they changed (#309)."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.catalog.tests.test_delete_product import _project_with_device
from editor_gui.plugins.project.service import _history_label
from editor_gui.strings import get_locale, set_locale

_OBJECT = "1.1.5 #1 Step"


@pytest.mark.parametrize(
    ("event_type", "data", "text"),
    [
        (
            "SetComObjectFlag",
            {"target": _OBJECT, "flag": "transmit_flag", "value": True},
            "Turn on flag T of 1.1.5 #1 Step",
        ),
        (
            "SetComObjectFlag",
            {"target": _OBJECT, "flag": "read_flag", "value": False},
            "Turn off flag R of 1.1.5 #1 Step",
        ),
        (
            "SetComObjectFlag",
            {"target": _OBJECT, "flag": "update_flag", "value": None},
            "Reset flag U of 1.1.5 #1 Step",
        ),
        (
            "SetComObjectDatapointType",
            {"target": _OBJECT, "datapoint_type": "DPST-3-7"},
            "Set datapoint type of 1.1.5 #1 Step to 3.007",
        ),
        (
            "SetComObjectDatapointType",
            {"target": _OBJECT, "datapoint_type": "DPT-5"},
            "Set datapoint type of 1.1.5 #1 Step to 5",
        ),
        (
            "SetComObjectDatapointType",
            {"target": _OBJECT, "datapoint_type": None},
            "Reset datapoint type of 1.1.5 #1 Step",
        ),
        (
            "SetComObjectPriority",
            {"target": _OBJECT, "priority": "High"},
            "Set priority of 1.1.5 #1 Step to High",
        ),
        (
            "SetComObjectPriority",
            {"target": _OBJECT, "priority": None},
            "Reset priority of 1.1.5 #1 Step",
        ),
        (
            "SetComObjectDescription",
            {"target": _OBJECT, "description": "x"},
            "Set description of 1.1.5 #1 Step",
        ),
        (
            "LinkComObject",
            {"target": _OBJECT, "detail": "1/2/3 Light"},
            "Link 1.1.5 #1 Step with 1/2/3 Light",
        ),
        (
            "UnlinkComObject",
            {"target": _OBJECT, "detail": "1/2/3 Light"},
            "Unlink 1.1.5 #1 Step from 1/2/3 Light",
        ),
        (
            "MoveDevice",
            {
                "segment_id": 1,
                "address": 7,
                "target": "1.1.5 Dimmer",
                "detail": "1.1.7",
            },
            "Move device 1.1.5 Dimmer to 1.1.7",
        ),
        (
            "MoveDevice",
            {"segment_id": 1, "address": None, "target": "1.1.5 Dimmer"},
            "Park device 1.1.5 Dimmer",
        ),
        (
            "MoveDevice",
            {"segment_id": None, "address": None, "target": "1.1.5 Dimmer"},
            "Unlink device 1.1.5 Dimmer",
        ),
        (
            "SetGroupAddressDatapointType",
            {"target": "1/2/3 Light", "datapoint_type": "DPST-1-1"},
            "Set datapoint type of 1/2/3 Light to 1.001",
        ),
        ("RemoveDevice", {"target": "1.1.5 Dimmer"}, "Remove device 1.1.5 Dimmer"),
        ("RemoveArea", {"target": "1 Ground"}, "Remove area 1 Ground"),
        ("RemoveLine", {"target": "1.1 Kitchen"}, "Remove line 1.1 Kitchen"),
        ("RemoveSegment", {"target": "1.1 B"}, "Remove segment 1.1 B"),
        ("RemoveGroupAddress", {"target": "1/2/3"}, "Remove group address 1/2/3"),
        ("RemoveFunction", {"target": "Ceiling"}, "Remove function Ceiling"),
        (
            "RemoveFunctionGroupAddress",
            {"target": "Ceiling", "detail": "1/2/3 Light"},
            "Remove group address 1/2/3 Light from function Ceiling",
        ),
        ("RemoveSpace", {"target": "Kitchen"}, "Remove building part Kitchen"),
        ("RemoveGroupRange", {"target": "1/ Light"}, "Remove group range 1/ Light"),
        ("RemoveTrade", {"target": "Lighting"}, "Remove trade Lighting"),
        (
            "RemoveTradeDevice",
            {"target": "Lighting", "detail": "1.1.5 Dimmer"},
            "Remove device 1.1.5 Dimmer from trade Lighting",
        ),
    ],
)
def test_entries_name_their_target(event_type: str, data: dict, text: str) -> None:
    assert _history_label(event_type, data) == text


@pytest.mark.parametrize(
    ("event_type", "data", "text"),
    [
        ("SetComObjectFlag", {"flag": "read_flag", "value": True}, "Set flag"),
        ("SetComObjectDatapointType", {"datapoint_type": None}, "Set datapoint type"),
        ("LinkComObject", {"target": _OBJECT}, "Link group object"),
        ("RemoveDevice", {"target_id": 1, "rows": []}, "Remove device"),
        ("MoveDevice", {"segment_id": 1, "address": 2, "target": "x"}, "Move device"),
    ],
)
def test_entries_stored_before_keep_their_text(
    event_type: str, data: dict, text: str
) -> None:
    assert _history_label(event_type, data) == text


def test_the_wording_follows_the_language() -> None:
    previous = get_locale()
    try:
        set_locale("de")
        assert (
            _history_label(
                "SetComObjectDatapointType",
                {"target": _OBJECT, "datapoint_type": "DPST-1-1"},
            )
            == "Datenpunkttyp von 1.1.5 #1 Step auf 1.001 festlegen"
        )
        assert (
            _history_label(
                "SetComObjectFlag",
                {"target": _OBJECT, "flag": "transmit_flag", "value": True},
            )
            == "Flag Ü von 1.1.5 #1 Step einschalten"
        )
    finally:
        set_locale(previous)


def test_edits_in_the_editor_name_the_group_object(tmp_path: Path) -> None:
    _, proj, _ = _project_with_device(tmp_path)
    (device,) = proj.devices
    co = next(c for c in device.get_visible_com_objects() if c.db_id is not None)
    target = (
        f"{device.individual_address or device.display_name} #{co.number} {co.name}"
    )

    proj.set_com_object_datapoint_type(device, co.id, "1.001")
    assert proj.history()[0].display_text == (
        f"Set datapoint type of {target} to 1.001"
    )
    proj.set_com_object_priority(device, co.id, "Alert")
    assert proj.history()[0].display_text == f"Set priority of {target} to Alert"
    proj.set_com_object_description(device, co.id, "Note")
    assert proj.history()[0].display_text == f"Set description of {target}"

    ga = proj.create_group_address("1/2/3", "Light")
    assert ga is not None
    link = proj.link_com_object_to_ga(co.db_id, ga)
    assert link is not None
    assert proj.history()[0].display_text == f"Link {target} with 1/2/3 Light"
    proj.unlink_com_object_from_ga(link)
    assert proj.history()[0].display_text == f"Unlink {target} from 1/2/3 Light"

    proj.remove_device(device.node_id)
    assert proj.history()[0].display_text.startswith("Remove device ")
    proj.close()


@pytest.mark.parametrize(
    ("event_type", "data", "text"),
    [
        (
            "SetDeviceName",
            {"name": "Kitchen", "target": "1.1.5 Dimmer"},
            "Rename device 1.1.5 Dimmer to 'Kitchen'",
        ),
        ("SetDeviceName", {"name": "Kitchen"}, "Rename device to 'Kitchen'"),
        (
            "RenameGroupAddress",
            {"name": "Light", "target": "1/2/3 Switch"},
            "Rename group address 1/2/3 Switch to 'Light'",
        ),
        (
            "SetTradeField",
            {"field": "name", "value": "Light", "target": "Lighting"},
            "Rename trade Lighting to 'Light'",
        ),
        (
            "RenameGroupRange",
            {"name": "Light", "target": "1/ Lights"},
            "Rename group range 1/ Lights to 'Light'",
        ),
    ],
)
def test_renames_name_the_element(event_type: str, data: dict, text: str) -> None:
    assert _history_label(event_type, data) == text


def test_a_step_labelled_as_an_event_is_worded_when_shown() -> None:
    data = {
        "events": [],
        "label": "1.1.5  Button: Turn on flag R of 1.1.5 #0 Switch",
        "label_as": {
            "type": "SetComObjectFlag",
            "data": {"target": "1.1.5 #0 Switch", "flag": "read_flag", "value": True},
        },
    }
    assert _history_label("Composite", data) == "Turn on flag R of 1.1.5 #0 Switch"
    previous = get_locale()
    try:
        set_locale("de")
        assert _history_label("Composite", data) == (
            "Flag L von 1.1.5 #0 Switch einschalten"
        )
    finally:
        set_locale(previous)


def test_a_plugin_device_entry_follows_the_language(tmp_path, monkeypatch) -> None:
    from editor_gui.plugins.project.tests.test_powertool import current, native_project

    proj, node = native_project(tmp_path, monkeypatch)
    proj.setup_plugin(current(proj, node))
    device = current(proj, node)
    co = device.com_objects[1]
    target = (
        f"{device.individual_address or device.display_name} #{co.number} {co.name}"
    )
    turn_on = not co.flags.read
    proj.set_flag(device, co.id, "read", turn_on)
    english = "Turn on flag R of" if turn_on else "Turn off flag R of"
    assert proj.history()[0].display_text == f"{english} {target}"
    previous = get_locale()
    try:
        set_locale("de")
        # No edit in between: the switch alone shows the history in German.
        german = "einschalten" if turn_on else "ausschalten"
        assert proj.history()[0].display_text == f"Flag L von {target} {german}"
    finally:
        set_locale(previous)
    proj.close()
