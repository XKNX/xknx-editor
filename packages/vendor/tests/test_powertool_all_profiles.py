"""Shared profile coverage and original Windows serialization."""

import json
from dataclasses import replace

import pytest

from packages.vendor.tests.powertool_fixtures import archive_cases, open_archive
from xknxeditor.vendor.powertool.codec import HostProfile
from xknxeditor.vendor.powertool.family import (
    FamilyConfig,
    FamilyEvaluation,
    edit,
    from_saved,
    schemas,
)
from xknxeditor.vendor.powertool.family_codec import compile_family
from xknxeditor.vendor.powertool.native import dump, load

PROGRAMS = (bytes.fromhex("0007610840"), bytes.fromhex("0007610850"))
PROFILES = [(s["code"], p) for s in schemas() for p in s["products"]]
CAPTURES = [r["case"] for r in archive_cases("all-profile-images.zip")]


@pytest.mark.parametrize(("code", "name"), PROFILES)
def test_shared_profile_default(code, name):
    config = FamilyConfig(
        name, database_code=code, program_ids=tuple(p.hex() for p in PROGRAMS)
    )
    assert load(dump(config)) == config
    image = compile_family(config)
    assert {r.object_index for r in image.resources} == {1, 2, 3, 4, 5}


@pytest.mark.parametrize("case", CAPTURES)
def test_complete_original_image(case):
    with open_archive("all-profile-images.zip") as archive:
        config = from_saved(archive.read(case + "/project.blob"), qualified=True)
        assert compile_family(config, program_ids=PROGRAMS).pid == archive.read(
            case + "/image.pid"
        )


def test_windows_originals_match_complete_pid():
    with open_archive("windows-captures.zip") as archive:
        names = {name.replace("\\", "/"): name for name in archive.namelist()}
        manifest = json.loads(
            archive.read(names["captures/manifest.json"]).decode("utf-8-sig")
        )
        assert len(manifest) == 11
        for case in manifest:
            prefix = "captures/" + case["case"]
            config = from_saved(
                archive.read(names[prefix + "/project.blob"]), qualified=True
            )
            original = archive.read(names[prefix + "/image.pid"])
            windows = compile_family(
                config, HostProfile(property_order="windows"), program_ids=PROGRAMS
            )
            mono = compile_family(config, program_ids=PROGRAMS)
            assert windows.pid == original
            assert windows.resources == mono.resources
            assert windows.pid != mono.pid


def test_special_channel_deletion_preserves_surviving_role():
    config = FamilyConfig(
        "SoloRTR2", database_code=2, program_ids=tuple(p.hex() for p in PROGRAMS)
    )
    state = FamilyEvaluation(config)
    parent = next(d for d in state.devices if d.path in state.count_limits)
    first = parent.channels[0]
    option = next(
        i
        for i, key in enumerate(first.spec["applications"])
        if state.schema["nodes"][key]["objects"]
    )
    config = edit(config, first.path, str(option))
    state = FamilyEvaluation(config)
    assert len(state.nodes[parent.path].channels) == 2
    second = state.nodes[parent.path].channels[1]
    config = edit(config, second.path, str(option))
    state = FamilyEvaluation(config)
    second_index = next(
        i for i, c in enumerate(state.channels) if c.path == second.path
    )
    before = compile_family(config)
    second_roles = {o.key for o in before.objects if o.channel == second_index}
    assert second_roles
    config = edit(config, first.path, "0")
    state = FamilyEvaluation(config)
    assert len(state.nodes[parent.path].channels) == 2
    assert second_roles <= {o.key for o in compile_family(config).objects}
    assert load(dump(config)) == config


def test_panel_control_identity_and_overlap():
    config = FamilyConfig(
        "SmartTouch", database_code=20, program_ids=tuple(p.hex() for p in PROGRAMS)
    )
    state = FamilyEvaluation(config)
    page = next(
        c
        for c in state.channels
        if c.application is not None
        and c.application.spec.get("application_type") == "Page"
        and state.active(c)
    )
    control = next(
        c
        for c in page.channels
        if any(
            state.schema["nodes"][k].get("application_type") == "Control"
            for k in c.spec["applications"]
        )
    )
    option = next(
        i
        for i, k in enumerate(control.spec["applications"])
        if state.schema["nodes"][k].get("application_type") == "Control"
    )
    config = edit(config, control.path, str(option))
    state = FamilyEvaluation(config)
    assert state.stored(
        next(
            n
            for n in state.nodes.values()
            if n.channel is state.nodes[control.path]
            and n.spec.get("extension") == "ControlId"
        )
    ) == state.channel_number(state.nodes[control.path])
    assert compile_family(config)
    position = next(
        n
        for n in state.nodes.values()
        if n.channel is state.nodes[control.path]
        and n.spec.get("extension") == "Position"
    )
    invalid = replace(config, values={**config.values, position.path: 16})
    with pytest.raises(ValueError):
        compile_family(invalid)


def test_unverified_family_blob_is_a_validation_error():
    with open_archive("host-projects.zip") as archive:
        blob = archive.read("DI-3.blob")
    changed = blob.replace(
        b"ToolBObjektTasterDatabase.dll", b"ToolBXbjektTasterDatabase.dll"
    )
    assert changed != blob
    with pytest.raises(ValueError, match="Unverified PowerTool database version"):
        from_saved(changed)


@pytest.mark.parametrize("invalid", [[], None, {"missing": "role"}])
def test_channel_id_validation(invalid):
    raw = json.loads(dump(FamilyConfig("SoloRTR2", database_code=2)))
    raw["channel_ids"] = invalid
    with pytest.raises(ValueError):
        load(json.dumps(raw).encode())


def test_count_recreation_gets_new_role_identity():
    config = FamilyConfig(
        "SoloRTR2", database_code=2, program_ids=tuple(p.hex() for p in PROGRAMS)
    )
    state = FamilyEvaluation(config)
    path = next(iter(state.count_limits))
    config = edit(config, path + "/channel_count", "3")
    identities = dict(config.channel_ids)
    config = edit(config, path + "/channel_count", "1")
    config = edit(config, path + "/channel_count", "3")
    assert all(config.channel_ids[k] != v for k, v in identities.items())


def test_panel_delete_cascades_and_controls_do_not_overlap():
    from xknxeditor.vendor.powertool.panel import choices

    config = FamilyConfig(
        "SmartTouch", database_code=20, program_ids=tuple(p.hex() for p in PROGRAMS)
    )
    state = FamilyEvaluation(config)
    floor = next(c for c in state.channels if "App_Floor" in c.spec["applications"])
    room = next(c for c in state.channels if "App_Room" in c.spec["applications"])
    page = next(c for c in state.channels if "App_Page" in c.spec["applications"])
    for channel, name in ((floor, "App_Floor"), (room, "App_Room"), (page, "App_Page")):
        config = edit(
            config, channel.path, str(channel.spec["applications"].index(name))
        )
    state = FamilyEvaluation(config)
    for child, parent in ((room, floor), (page, room)):
        node = next(
            n
            for n in state.nodes.values()
            if n.channel is state.nodes[child.path]
            and n.spec.get("extension") == "ParentId"
        )
        config = edit(
            config, node.path, str(state.channel_number(state.nodes[parent.path]))
        )
        state = FamilyEvaluation(config)
    controls = state.nodes[page.path].channels[:2]
    for c in controls:
        config = edit(config, c.path, str(c.spec["applications"].index("App_Switch")))
    state = FamilyEvaluation(config)
    positions = [
        n
        for n in state.nodes.values()
        if n.channel is not None
        and n.channel.path in {c.path for c in controls}
        and n.spec.get("extension") == "Position"
    ]
    assert {state.stored(n) for n in positions} == {0, 1}
    assert 0 not in dict(choices(state, positions[1]))
    with pytest.raises(ValueError, match="unavailable"):
        edit(config, positions[1].path, "0")
    config = edit(config, floor.path, "0")
    state = FamilyEvaluation(config)
    assert not state.active(state.nodes[room.path])
    assert not state.active(state.nodes[page.path])
    assert all(not state.active(state.nodes[c.path]) for c in controls)
    assert compile_family(config)


@pytest.mark.parametrize(
    "case", [r["case"] for r in archive_cases("powerline-images.zip")]
)
def test_powerline_originals(case):
    from xknxeditor.vendor.powertool import Association

    with open_archive("powerline-images.zip") as archive:
        config = from_saved(archive.read(case + "/project.blob"), qualified=True)
        context = json.loads(archive.read(case + "/host.json"))
        host = HostProfile(
            mask_version=0x17B0,
            domain_address=context["domain_address"],
            repeater_present=context["repeater_present"],
            short_associations=context.get("short_associations", False),
        )
        raw = (
            json.loads(archive.read(case + "/edit.json"))
            if case + "/edit.json" in archive.namelist()
            else {}
        )
        objects = compile_family(config, program_ids=PROGRAMS).objects
        links = [
            Association(
                next(
                    o.key
                    for o in objects
                    if o.channel == a["channel"] and o.position == a["position"]
                ),
                a["number"],
                a["send"],
                a["group_speaker"],
            )
            for a in raw.get("associations", [])
        ]
        assert compile_family(
            config, host, links, program_ids=PROGRAMS
        ).pid == archive.read(case + "/image.pid")


@pytest.mark.parametrize("powerline", [False, True])
def test_actual_product_identity_and_secondary_validation(powerline):
    from xknxeditor.prod import parse_application_xml
    from xknxeditor.vendor.powertool.native import default, for_product, product_profile

    with open_archive("all-profile-products.zip") as archive:
        prefix = (
            "host/M-0007/M-0007_A-6128-"
            if powerline
            else "smarttouch/M-0007/M-0007_A-6136-"
        )
        versions = ("90-", "A0-") if powerline else ("40-", "41-")
        app, pei = [
            parse_application_xml(
                archive.read(
                    next(n for n in archive.namelist() if n.startswith(prefix + v))
                ),
                "M-0007",
            )[0]
            for v in versions
        ]
    profile = product_profile(app, pei)
    config = for_product(default(profile), app, pei)
    assert config.program_ids == tuple(
        (
            b"\0\x07"
            + p.program.application_number.to_bytes(2, "big")
            + bytes([p.program.application_version])
        ).hex()
        for p in (app, pei)
    )
    with pytest.raises(ValueError, match="secondary"):
        product_profile(app, None)
    with pytest.raises(ValueError):
        product_profile(app, app)
    pei.program.mask_version = "MV-07B0" if powerline else "MV-17B0"
    with pytest.raises(ValueError):
        product_profile(app, pei)
    pei.program.mask_version = app.program.mask_version
    with pytest.raises(ValueError, match="identifiers"):
        for_product(replace(config, program_ids=("0007610821", "0007610831")), app, pei)
