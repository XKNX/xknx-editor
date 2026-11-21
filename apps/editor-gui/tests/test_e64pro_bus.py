from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from xml.etree import ElementTree as ET

import pytest
from editor_gui.plugins.project.ui.e64pro_bus import BusView
from editor_gui.programming_dali import (
    GuardedDaliOperation,
    finish_workspace_operation,
    prepare_workspace_operation,
)
from imgui_bundle import imgui

from xknxeditor.dali.configuration import scenes_from_xml
from xknxeditor.dali.connection import DaliCommissioningError

DATA = (
    next(p for p in Path(__file__).parents if (p / "packages/dali").exists())
    / "packages/dali/tests/fixtures/ipas_e64pro_backup.dat"
).read_bytes()


def device(data=DATA):
    return SimpleNamespace(
        node_id=1,
        individual_address="1.1.1",
        app=SimpleNamespace(id="M-00EF_A-DA11-22-A545"),
        binary_data=[SimpleNamespace(name="Dalie64-Backup-Store", data=data)],
    )


@pytest.mark.parametrize("width", [360, 1400])
def test_service_sections_render_without_bus_access(width):
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.display_size = imgui.ImVec2(width, 900)
        view = BusView()
        run = Mock()
        for section in (
            "scenes",
            "effects",
            "templates",
            "inputs",
            "ecgs",
            "groups",
            "mds",
        ):
            for subsection in ("names", "calibration", "memory", "motion"):
                view.section, view.motion_section = section, subsection
                imgui.new_frame()
                imgui.set_next_window_size(imgui.ImVec2(width - 20, 800))
                imgui.begin("DCA")
                view.render(device(), Mock(), run)
                imgui.end()
                imgui.render()
        run.assert_not_called()
    finally:
        imgui.destroy_context(context)


@pytest.mark.parametrize("changed", ["generation", "address", "data", "app"])
def test_queued_plan_rejects_changed_target(changed):
    target = device()
    current = device()
    project = SimpleNamespace(generation=1)
    operation = GuardedDaliOperation(project, target, DATA, 1, AsyncMock())
    operation.validate_device(current)
    if changed == "generation":
        project.generation = 2
    elif changed == "address":
        current.individual_address = "1.1.2"
    elif changed == "app":
        current.app.id = "other"
    else:
        current.binary_data[0].data = b"changed"
    with pytest.raises(ValueError, match="changed"):
        operation.validate_device(current)


@pytest.mark.parametrize(
    "action,index,value,secondary",
    [
        ("write_memory", 0, 256, 0),
        ("save_calibration", 8, 10, 10),
        ("function", 64, 0, 0),
        ("identify", 64, 0, 0),
    ],
)
def test_invalid_request_fails_during_preparation(action, index, value, secondary):
    with pytest.raises((ValueError, DaliCommissioningError)):
        prepare_workspace_operation(
            device(), action, "mds", index=index, value=value, secondary=secondary
        )


async def test_written_section_is_verified_before_dirty_flags_are_cleared():
    target = device()
    plan = prepare_workspace_operation(target, "write", "scenes")
    scenes = scenes_from_xml(ET.fromstring(DATA))
    commissioner = SimpleNamespace(
        write_scenes=AsyncMock(), read_scenes=AsyncMock(return_value=scenes)
    )
    result = await plan.execute(commissioner)
    updated = ET.fromstring(result["configuration"])
    assert all(
        slot.get("isDirty") == "False" for slot in updated.findall("scenes/Scene")
    )
    assert ET.tostring(updated.find("effects")) == ET.tostring(
        ET.fromstring(DATA).find("effects")
    )
    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _: target,
        apply_e64pro_readback=Mock(),
    )
    assert finish_workspace_operation(project, target, plan, 1, result) == {}
    project.apply_e64pro_readback.assert_called_once_with(
        target, DATA, result["configuration"]
    )
    scenes[0].values[0] = replace(scenes[0].values[0], level=1)
    with pytest.raises(DaliCommissioningError, match="readback"):
        await plan.execute(commissioner)


async def test_bus_callback_saves_when_tab_is_hidden_and_rejects_newer_data():
    target = device()
    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _: target,
        apply_e64pro_readback=Mock(),
    )
    queued = []
    view = BusView(section="inputs")
    view.start(target, project, lambda *args: queued.append(args), "read")
    _, operation, _, _, ended, writes = queued.pop()
    assert not writes and view.busy
    await operation(SimpleNamespace(read_inputs=AsyncMock(return_value=())))
    target.binary_data[0].data = b"newer"
    ended()
    assert not view.busy and "changed" in view.error
    project.apply_e64pro_readback.assert_not_called()


async def test_input_write_plan_checks_saved_identity_and_retains_failed_edits():
    from xknxeditor.dali.configuration import compile_configuration
    from xknxeditor.dali.configuration_readback import merge_inputs
    from xknxeditor.dali.inputs import InputDevice, InputInstance

    inputs = (
        InputDevice(
            3, 0xABCDEF, 100, (InputInstance(100, 3, 4, 7, 10, 0, 1, 118, 0, 500),)
        ),
    )
    data = merge_inputs(DATA, inputs)
    config = compile_configuration(data)
    plan = prepare_workspace_operation(device(data), "write", "inputs")
    commissioner = SimpleNamespace(write_inputs=AsyncMock(return_value=inputs))
    result = await plan.execute(commissioner)
    commissioner.write_inputs.assert_awaited_once_with(
        config.inputs, expected_inventory=config.input_inventory
    )
    assert compile_configuration(result["configuration"]).inputs == config.inputs
    commissioner.write_inputs.side_effect = DaliCommissioningError(
        "input inventory changed"
    )
    with pytest.raises(DaliCommissioningError, match="inventory changed"):
        await plan.execute(commissioner)


def template_device(func="SetValue", color_type="0", members=False):
    root = ET.fromstring(DATA)
    template = root.find("templates/Tpl[@index='0']")
    template.find("Members").clear()
    if members:
        ET.SubElement(
            template.find("Members"), "TplItem", itemtype="Group", itemindex="15"
        )
    commands = template.find("Commands")
    commands.clear()
    ET.SubElement(
        commands, "TplCmd", func=func, value="0", time="07:15", weekdays="0011111"
    )
    root.find("groups/Group[@index='15']").set("ctype", color_type)
    return device(ET.tostring(root, encoding="utf-16le"))


async def test_template_group_selection_uses_shared_plan_without_saving(monkeypatch):
    from editor_gui.plugins.project.strings import S

    target = template_device()
    original = target.binary_data[0].data
    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _: target,
        apply_e64pro_readback=Mock(),
    )
    view = BusView(section="templates")
    queued = []
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.display_size = imgui.ImVec2(600, 900)
        monkeypatch.setattr(
            "editor_gui.plugins.project.ui.e64pro_bus.pick",
            lambda label, current, options: (
                "15" if label == S.E64_TEST + "##target" else current
            ),
        )
        monkeypatch.setattr(
            imgui, "button", lambda label: label == S.E64_BUS_ACTIONS["test"]
        )
        imgui.new_frame()
        imgui.begin("DCA")
        view.render(target, project, lambda *args: queued.append(args))
        imgui.end()
        imgui.render()
    finally:
        imgui.destroy_context(context)
    assert view.test_group == 15 and view.busy and len(queued) == 1
    commissioner = SimpleNamespace(test_template_command=AsyncMock())
    _, operation, _, _, ended, writes = queued[0]
    assert writes
    await operation(commissioner)
    ended()
    args, kwargs = commissioner.test_template_command.await_args
    assert args[0].index == 0 and args[1] == 0 and kwargs == {"group": 15}
    assert not view.busy and not view.error
    assert target.binary_data[0].data == original
    project.apply_e64pro_readback.assert_not_called()


@pytest.mark.parametrize(
    "action,section,group,func,ctype",
    [
        ("test", "templates", -1, "SetValue", "0"),
        ("test", "templates", 16, "SetValue", "0"),
        ("read", "templates", 0, "SetValue", "0"),
        ("test", "scenes", 0, "SetValue", "0"),
        ("test", "templates", 0, "CallScene", "0"),
        ("test", "templates", 0, "CallEffect", "0"),
        ("test", "templates", 15, "ColourTemp", "6"),
        ("test", "templates", 15, "ColourTemp", "invalid"),
    ],
)
def test_invalid_template_group_is_rejected_before_queue(
    action, section, group, func, ctype
):
    with pytest.raises(ValueError):
        prepare_workspace_operation(
            template_device(func, ctype), action, section, test_group=group
        )


def test_template_without_targets_is_rejected_before_queue():
    with pytest.raises(ValueError, match="unavailable"):
        prepare_workspace_operation(template_device(), "test", "templates")
    assert prepare_workspace_operation(
        template_device("ColourTemp", "4"), "test", "templates", test_group=15
    ).writes


@pytest.mark.parametrize("change", ["missing", "duplicate"])
def test_template_group_must_resolve_uniquely_before_queue(change):
    from copy import deepcopy

    target = template_device()
    root = ET.fromstring(target.binary_data[0].data)
    groups = root.find("groups")
    group = groups.find("Group[@index='15']")
    if change == "missing":
        groups.remove(group)
    else:
        groups.append(deepcopy(group))
    with pytest.raises(ValueError, match="unavailable"):
        prepare_workspace_operation(
            device(ET.tostring(root, encoding="utf-16le")),
            "test",
            "templates",
            test_group=15,
        )


@pytest.mark.parametrize(
    "action,section",
    [("read", "templates"), ("write", "templates"), ("test", "scenes")],
)
async def test_group_selection_does_not_restrict_other_actions(action, section):
    from xknxeditor.dali.configuration import templates_from_xml

    target = template_device(members=True)
    templates = templates_from_xml(ET.fromstring(target.binary_data[0].data))
    commissioner = SimpleNamespace(
        read_templates=AsyncMock(return_value=templates),
        write_templates=AsyncMock(),
        test_scene=AsyncMock(),
    )
    view = BusView(section=section, test_group=15)
    queued = []
    view.start(
        target,
        SimpleNamespace(generation=1),
        lambda *args: queued.append(args),
        action,
    )
    assert len(queued) == 1 and not view.error
    result = await queued[0][1](commissioner)
    if action == "test":
        commissioner.test_scene.assert_awaited_once_with(0)
        assert result == {}
    else:
        commissioner.read_templates.assert_awaited_once_with()
        assert "configuration" in result
        if action == "write":
            commissioner.write_templates.assert_awaited_once_with(templates)


@pytest.mark.parametrize("remove", [False, True])
def test_complete_configuration_commits_ecg_assignments_and_guards_newer_edits(remove):
    from editor_gui.programming_dali import complete_dali_configuration

    from xknxeditor.dali.restore import prepare_restore

    root = ET.fromstring(DATA)
    root.set("CanRestore", "True")
    root.find("ecgs/ECG[@index='9']").attrib.update(
        {"short-address": "3", "groupindex": "16", "flags": "3"}
    )
    ET.SubElement(
        root.find("devecgs"),
        "DevECG",
        index="3",
        type="8",
        subtype="2",
        groupindex="32",
        **{"short-address": "255", "long-address": "123456"},
    )
    if remove:
        root.find("ecgs/ECG[@index='9']").attrib.update(
            {"short-address": "255", "flags": "2"}
        )
        root.find("devecgs/DevECG").set("short-address", "9")
    data = ET.tostring(root, encoding="utf-16le")
    target = device(data)
    saved = []
    project = SimpleNamespace(
        generation=1,
        find_device_by_node_id=lambda _: target,
        persist_dali_backup=lambda current, name, payload, **kwargs: saved.append(
            payload
        ),
    )
    complete_dali_configuration(project, target, data, 1)
    actual = ET.fromstring(saved[0])
    assert actual.find("ecgs/ECG[@index='9']").get("flags") == ("1" if remove else "4")
    assert actual.find("devecgs/DevECG[@index='3']").get("short-address") == (
        "255" if remove else "9"
    )
    assert actual.find("devecgs/DevECG[@index='3']").get("groupindex") == (
        "32" if remove else "16"
    )
    plan = prepare_restore(saved[0])
    if remove:
        assert not plan.configuration.assignments
    else:
        assert plan.configuration.assignments[0].ets_index == 9
    for changed in ("generation", "data"):
        project.generation = 2 if changed == "generation" else 1
        target.binary_data[0].data = b"newer" if changed == "data" else data
        with pytest.raises(ValueError, match="changed"):
            complete_dali_configuration(project, target, data, 1)
    assert len(saved) == 1
