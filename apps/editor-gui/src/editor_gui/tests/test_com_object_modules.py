"""Public device queries for the module filter in the group-object table."""

from editor_gui.device import ComObject, ComObjectFlags, Device
from editor_gui.dpt import DPT_UNKNOWN


def test_ets_folders_preserve_hierarchy_and_parent_selection() -> None:
    from pathlib import Path

    from xknxeditor.prod.application import parse_application_xml

    fixture = (
        Path(__file__).parents[5]
        / "packages/prod/tests/fixtures/object_tree_application.xml"
    )
    app = parse_application_xml(fixture.read_bytes(), "M-00FA")[0]
    device = Device(1, "Tree", app, "1.2.10")
    channel = f"{app.id}_CH-1"
    folder = f"{app.id}_PB-3"
    assert device.get_com_object_modules() == {
        "": "",
        channel: "Channel A",
        folder: "Status",
    }
    assert device.get_com_object_module_parents() == {
        "": None,
        channel: None,
        folder: channel,
    }
    assert [co.number for co in device.get_filtered_com_objects([folder])] == [3]
    assert [co.number for co in device.get_filtered_com_objects([channel])] == [6, 2, 3]
    assert [co.number for co in device.get_filtered_com_objects([folder, channel])] == [
        6,
        2,
        3,
    ]
    assert [co.number for co in device.get_filtered_com_objects([""])] == [1]


def test_objects_without_application_are_available_without_module() -> None:
    co = ComObject(id="O-1", name="Switch", dpt=DPT_UNKNOWN, flags=ComObjectFlags())
    device = Device(1, "Device", None, "1.1.1", com_objects=[co])
    assert device.get_com_object_modules() == {"": ""}
    assert device.get_filtered_com_objects([""]) == [co]
    assert device.get_filtered_com_objects(None) == [co]
    assert device.get_filtered_com_objects([]) == []
    assert device.get_filtered_com_objects(["missing"]) == []


def test_module_instances_filter_by_identity_and_keep_object_order() -> None:
    from pathlib import Path

    from xknxeditor.prod import load

    fixture = (
        Path(__file__).parents[5]
        / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
    )
    app_id = "M-0008_A-7072-21-5CC3-O000A"
    app = load(fixture.read_bytes()).applications[app_id]
    device = Device(1, "Gira", app, "1.1.1")
    first = f"{app_id}_MD-1_M-100_MI-1_CH-1"
    second = f"{app_id}_MD-1_M-200_MI-1_CH-1"
    assert device.get_com_object_modules() == {
        first: "Channel 1 (...)",
        second: "Channel 2 (...)",
    }
    assert [co.number for co in device.get_filtered_com_objects([second])] == [262, 261]
    assert [co.number for co in device.get_filtered_com_objects([second, first])] == [
        254,
        253,
        262,
        261,
    ]
    assert device.get_filtered_com_objects([""]) == []


def test_equal_module_labels_remain_distinguishable() -> None:
    from pathlib import Path

    from xknxeditor.namespaces.intermediate.application_program_channel_t import (
        ApplicationProgramChannel,
    )
    from xknxeditor.prod import load

    fixture = (
        Path(__file__).parents[5]
        / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
    )
    app = next(iter(load(fixture.read_bytes()).applications.values()))
    assert app.program.module_defs is not None
    dynamic = app.program.module_defs.module_def[0].dynamic
    assert dynamic is not None
    channel = next(
        node for node in dynamic.choice if isinstance(node, ApplicationProgramChannel)
    )
    channel.text = "Input"
    device = Device(1, "Gira", app, "1.1.1")
    groups = device.get_com_object_modules()
    assert list(groups.values()) == ["Input 1 (1)", "Input 1 (2)"]
    first, second = groups
    assert [co.number for co in device.get_filtered_com_objects([first])] == [254, 253]
    assert [co.number for co in device.get_filtered_com_objects([second])] == [262, 261]


def test_module_names_follow_parameter_edits() -> None:
    from pathlib import Path

    from xknxeditor.prod import load

    fixture = (
        Path(__file__).parents[5]
        / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
    )
    app = next(iter(load(fixture.read_bytes()).applications.values()))
    device = Device(1, "Gira", app, "1.1.1")
    prefix = f"{app.id}_MD-1_M-100_MI-1"
    assert device.get_com_object_modules()[f"{prefix}_CH-1"] == "Channel 1 (...)"
    device.set_param_value(f"{prefix}_P-1_R-1", "Kitchen")
    assert device.get_com_object_modules()[f"{prefix}_CH-1"] == "Channel 1 (Kitchen)"
    assert [
        co.number for co in device.get_filtered_com_objects([f"{prefix}_CH-1"])
    ] == [254, 253]


def test_filter_does_not_restore_channels_absent_from_imported_instances() -> None:
    from pathlib import Path

    from xknxeditor.namespaces.intermediate.com_object_instance_ref_t import (
        ComObjectInstanceRef,
    )
    from xknxeditor.prod import load

    fixture = (
        Path(__file__).parents[5]
        / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
    )
    app = next(iter(load(fixture.read_bytes()).applications.values()))
    first = f"{app.id}_MD-1_M-100_MI-1"
    second = f"{app.id}_MD-1_M-200_MI-1"
    device = Device(
        1,
        "Imported",
        app,
        "1.1.1",
        com_object_instance_refs=[
            ComObjectInstanceRef(ref_id=f"{first}_O-2-1_R-2"),
            ComObjectInstanceRef(ref_id=f"{first}_O-2-0_R-1"),
        ],
    )
    assert list(device.get_com_object_modules()) == [f"{first}_CH-1"]
    assert [co.number for co in device.get_filtered_com_objects()] == [254, 253]
    assert device.get_filtered_com_objects([f"{second}_CH-1"]) == []
