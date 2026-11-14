"""Saved-project writeback preserves the image and original object identities."""

import json
from dataclasses import replace

import pytest

from packages.vendor.tests.powertool_fixtures import archive_cases, open_archive
from xknxeditor.vendor.powertool.codec import HostProfile
from xknxeditor.vendor.powertool.family import FamilyConfig, from_saved
from xknxeditor.vendor.powertool.family_codec import compile_family
from xknxeditor.vendor.powertool.native import (
    compile_native,
    default,
    edit_native,
    from_original,
    rebase,
)
from xknxeditor.vendor.powertool.nrbf import (
    Record,
    Reference,
    read_document,
    write_document,
)
from xknxeditor.vendor.powertool.project_blob import read_project_blob
from xknxeditor.vendor.powertool.project_writer import write_project_blob


@pytest.mark.parametrize("variant", ["01", "02"])
def test_create_without_original(variant):
    config = default(variant)
    result = write_project_blob(None, config)
    assert compile_native(from_original(result)).pid == compile_native(config).pid
    document = read_document(result)
    assert not set(map(abs, document.objects)).intersection(document.libraries)


def test_edit_preserves_project_identity_and_metadata():
    with open_archive("host-projects.zip") as archive:
        original = archive.read("DI-1.blob")
    config = from_original(original)
    changed = edit_native(config, "powertool:0:function", "value_rocker")
    result = write_project_blob(original, changed)
    assert compile_native(from_original(result)).pid == compile_native(changed).pid
    before, after = read_document(original), read_document(result)
    left, right = before.resolve(before.root), after.resolve(after.root)
    assert isinstance(left, Record) and isinstance(right, Record)
    for key in left.members:
        if key != "ProjectedDevices":
            assert before.resolve(left.members[key]) == after.resolve(
                right.members[key]
            )
    assert read_project_blob(original).channels != read_project_blob(result).channels


@pytest.mark.parametrize(
    "case",
    [
        r["case"]
        for r in archive_cases("original-projects.zip")
        if r["case"].startswith("matrix/")
    ],
)
def test_pushbutton_matrix_writeback(case):
    with open_archive("original-projects.zip") as archive:
        original = archive.read(case + "/project.blob")
    config = from_original(original)
    restored = from_original(write_project_blob(original, config))
    assert compile_native(restored).pid == compile_native(config).pid


@pytest.mark.parametrize(
    "case", [r["case"] for r in archive_cases("all-profile-images.zip")]
)
def test_all_profile_layout_writeback(case):
    with open_archive("all-profile-images.zip") as archive:
        original = archive.read(case + "/project.blob")
    config = from_saved(original, qualified=True)
    restored = from_saved(write_project_blob(original, config), qualified=True)
    programs = (bytes.fromhex("0007610840"), bytes.fromhex("0007610850"))
    assert (
        compile_family(restored, program_ids=programs).pid
        == compile_family(config, program_ids=programs).pid
    )


def test_windows_writeback_captures_match_complete_pid():
    with open_archive("windows-writeback.zip") as archive:
        manifest = json.loads(
            archive.read("captures/manifest.json").decode("utf-8-sig")
        )
        assert len(manifest) == 22
        for case in manifest:
            assert (
                case["exit_code"] == 0 and case["complete"] and case["expected_equal"]
            )
            prefix = "captures/" + case["case"]
            expected = archive.read(prefix + "/image.pid")
            source = archive.read(prefix + "/input.blob")
            config = from_saved(source, qualified=True)
            saved = read_project_blob(source)
            for blob in (
                source,
                archive.read(prefix + "/project.blob"),
                write_project_blob(source, config),
            ):
                restored = from_saved(blob, qualified=True)
                assert restored == config, case["case"]
                project = read_project_blob(blob)
                assert project.parameters == saved.parameters, case["case"]
                assert project.channels == saved.channels, case["case"]
                assert project.devices == saved.devices, case["case"]
                compiled = compile_family(
                    restored,
                    HostProfile(property_order="windows"),
                    program_ids=(
                        bytes.fromhex("0007610840"),
                        bytes.fromhex("0007610850"),
                    ),
                )
                assert compiled.pid == expected, case["case"]


@pytest.mark.parametrize(
    "case", [r["file"] for r in archive_cases("host-projects.zip")]
)
def test_nrbf_roundtrip_retains_typed_graph(case):
    with open_archive("host-projects.zip") as archive:
        original = archive.read(case)
    before = read_document(original)
    after = read_document(write_document(before))
    assert before.objects == after.objects
    assert before.classes == after.classes
    assert before.arrays == after.arrays
    assert before.primitives == after.primitives
    assert before.libraries == after.libraries


def test_mismatched_source_is_refused():
    original = write_project_blob(None, default("01"))
    with pytest.raises(ValueError, match="does not match"):
        write_project_blob(original, default("02"))


def test_modified_flags_survive_writeback():
    config = default("01")
    role = compile_native(config).objects[0].key
    config = replace(config, object_flags={role: 0x2D})
    restored = from_original(write_project_blob(None, config))
    assert compile_native(restored).pid == compile_native(config).pid


def test_special_channel_ids_survive_shift():
    from xknxeditor.vendor.powertool.family import FamilyEvaluation, edit

    config = FamilyConfig("SoloRTR2", database_code=2)
    state = FamilyEvaluation(config)
    parent = next(d for d in state.devices if d.path in state.count_limits)
    first = parent.channels[0]
    option = next(
        i
        for i, key in enumerate(first.spec["applications"])
        if state.schema["nodes"][key]["objects"]
    )
    config = edit(config, first.path, str(option))
    second = FamilyEvaluation(config).nodes[parent.path].channels[1]
    config = edit(config, second.path, str(option))
    original = write_project_blob(None, config)
    updated = edit(config, first.path, "0")
    restored = from_saved(write_project_blob(original, updated), qualified=True)
    programs = (bytes.fromhex("0007610840"), bytes.fromhex("0007610850"))
    assert (
        compile_family(restored, program_ids=programs).pid
        == compile_family(updated, program_ids=programs).pid
    )


def test_named_original_channel_retains_metadata_after_shift():
    from xknxeditor.vendor.powertool.family import FamilyEvaluation, edit

    config = FamilyConfig("SoloRTR2", database_code=2)
    state = FamilyEvaluation(config)
    parent = next(d for d in state.devices if d.path in state.count_limits)
    first = parent.channels[0]
    option = next(
        i
        for i, key in enumerate(first.spec["applications"])
        if state.schema["nodes"][key]["objects"]
    )
    config = edit(config, first.path, str(option))
    second = FamilyEvaluation(config).nodes[parent.path].channels[1]
    config = edit(config, second.path, str(option))
    before = read_document(write_project_blob(None, config))

    def channel(document, index):
        root = document.resolve(document.root)
        devices = document.resolve(root.members["ProjectedDevices"])
        device = document.resolve(devices[int(parent.path.split("/")[-1])])
        channels = document.resolve(device.members["serializedChannels"])
        return document.resolve(channels[index])

    old = channel(before, 1)
    name_id = max(map(abs, before.objects)) + 1
    before.objects[name_id] = "Kept channel name"
    old.members["name"] = Reference(name_id)
    source = write_document(before)
    config = from_saved(source, qualified=True)
    updated = edit(config, first.path, "0")
    assert updated.channel_ids[first.path] == second.path
    after = read_document(write_project_blob(source, updated))
    new = channel(after, 0)
    assert after.resolve(new.members["name"]) == "Kept channel name"
    old_app = before.resolve(old.members["Application"])
    new_app = after.resolve(new.members["Application"])
    for key in ("serializedParameters", "serializedCommunicationObjects"):
        old_items = before.resolve(old_app.members[key])
        new_items = after.resolve(new_app.members[key])
        for left, right in zip(old_items, new_items, strict=True):
            left_id = before.resolve(left).members["ID"]
            right_id = after.resolve(right).members["ID"]
            assert before.resolve(left_id) == after.resolve(right_id)
    assert before.resolve(old_app.members["viewType"]) == after.resolve(
        new_app.members["viewType"]
    )
    programs = (bytes.fromhex("0007610840"), bytes.fromhex("0007610850"))
    for delay in (2000, 3000):
        source = write_document(after)
        updated = rebase(updated)
        assert isinstance(updated, FamilyConfig)
        assert not updated.channel_ids
        updated = edit(updated, first.path + "/application/0/2", str(delay))
        output = write_project_blob(source, updated)
        after = read_document(output)
        new = channel(after, 0)
        assert after.resolve(new.members["name"]) == "Kept channel name"
        new_app = after.resolve(new.members["Application"])
        for key in ("serializedParameters", "serializedCommunicationObjects"):
            old_items = before.resolve(old_app.members[key])
            new_items = after.resolve(new_app.members[key])
            for left, right in zip(old_items, new_items, strict=True):
                left_id = before.resolve(left).members["ID"]
                right_id = after.resolve(right).members["ID"]
                assert before.resolve(left_id) == after.resolve(right_id)
        assert (
            compile_family(from_saved(output, qualified=True), program_ids=programs).pid
            == compile_family(updated, program_ids=programs).pid
        )


def test_rebase_keeps_bindings_and_flags_for_inactive_objects():
    from xknxeditor.vendor.powertool.family import FamilyEvaluation

    config = FamilyConfig("SoloRTR3", database_code=2)
    state = FamilyEvaluation(config)
    channel, hidden = next(
        (channel, obj)
        for channel in state.channels
        if channel.application is not None
        for obj in channel.application.objects
        if not state.property(obj, "Exists")
    )
    app = channel.application
    identity = "new-channel"
    roles = {
        f"{identity}/{app.key}/{obj.key}": f"reference-{i}"
        for i, obj in enumerate(app.objects)
    }
    flag_key = f"{identity}/{app.key}/{hidden.key}"
    config = replace(
        config,
        channel_ids={channel.path: identity},
        bindings=roles,
        object_flags={flag_key: 0xA5},
    )
    updated = rebase(config)
    assert isinstance(updated, FamilyConfig)
    assert updated.channel_ids == {}
    assert updated.bindings == {
        key.replace(identity, channel.path, 1): value for key, value in roles.items()
    }
    assert updated.object_flags == {f"{channel.path}/{app.key}/{hidden.key}": 0xA5}
    assert rebase(updated) is updated
    programs = (bytes.fromhex("0007612870"), bytes.fromhex("0007612880"))
    assert (
        compile_family(updated, program_ids=programs).pid
        == compile_family(config, program_ids=programs).pid
    )
    for candidate in (config, updated):
        with pytest.raises(ValueError, match="unknown object"):
            rebase(replace(candidate, bindings={"missing-role": "reference"}))


def test_source_less_insertions_rebase_to_each_written_blob():
    from xknxeditor.vendor.powertool.family import FamilyEvaluation, edit

    config = FamilyConfig("SoloRTR2", database_code=2)
    source = None
    previous_ids = []
    programs = (bytes.fromhex("0007610840"), bytes.fromhex("0007610850"))
    for _ in range(3):
        state = FamilyEvaluation(config)
        parent = next(d for d in state.devices if d.path in state.count_limits)
        config = edit(config, parent.channels[-1].path, "1")
        output = write_project_blob(source, config)
        config = rebase(config)
        assert isinstance(config, FamilyConfig)
        document = read_document(output)
        root = document.resolve(document.root)
        devices = document.resolve(root.members["ProjectedDevices"])
        device = document.resolve(devices[int(parent.path.split("/")[-1])])
        channels = document.resolve(device.members["serializedChannels"])
        ids = []
        for item in channels:
            channel = document.resolve(item)
            app = document.resolve(channel.members["Application"])
            objects = document.resolve(app.members["serializedCommunicationObjects"])
            ids.append(
                [
                    document.resolve(document.resolve(obj).members["ID"])
                    for obj in objects
                ]
            )
        assert ids[: len(previous_ids)] == previous_ids
        previous_ids = ids[:-1]
        assert (
            compile_family(from_saved(output, qualified=True), program_ids=programs).pid
            == compile_family(config, program_ids=programs).pid
        )
        source = output


def test_rebase_preserves_pushbutton_configuration():
    config = default("01")
    assert rebase(config) is config


def test_internal_addresses_are_refused_without_rewrite():
    source = write_project_blob(None, default("01"))
    document = read_document(source)
    root = document.resolve(document.root)
    assert isinstance(root, Record)
    root.members["internalAddresses"] = root.members["ID"]
    source = write_document(document)
    with pytest.raises(ValueError, match="internal PowerTool addresses"):
        write_project_blob(source, default("01"))


def test_reader_writer_keeps_typed_primitive_references_and_cycles():
    from xknxeditor.vendor.powertool.nrbf import (
        ClassInfo,
        Document,
        Reference,
        TypeInfo,
    )

    record = Record("Test", {"integer": 2**63 + 1, "float": 0.5, "self": Reference(1)})
    document = Document(
        Reference(1),
        {1: record},
        0,
        classes={1: ClassInfo("Test", tuple(record.members), (TypeInfo(2),) * 3)},
        primitives={(1, "integer"): 16, (1, "float"): 11},
    )
    restored = read_document(write_document(document))
    assert restored.objects == document.objects
    assert restored.primitives == document.primitives
