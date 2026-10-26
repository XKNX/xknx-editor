"""The Security of a main or middle group and of a group address, and a group address's Central
mark, as the editor shows and changes them in their details."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.ui import group_addresses
from editor_gui.plugins.project.ui.element_details import (
    ElementDetailsPanel,
    security_options,
)
from editor_gui.plugins.project.ui.group_addresses import (
    GroupAddressesPanel,
    _forced_on,
)


@pytest.fixture
def project(tmp_path: Path) -> Iterator[ProjectService]:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    yield proj
    proj.close()


def _groups(project: ProjectService) -> tuple[int, int, int]:
    """A group address 1/1/1 and its main and middle group."""
    ga = project.create_group_address("1/1/1", "Light")
    assert ga is not None
    main = project.get_group_range_tree()[0]
    return main.id, main.children[0].id, ga


def test_the_options_of_the_security_drop_down() -> None:
    assert security_options() == [
        ("Auto", "Automatic"),
        ("On", "Forced on"),
        ("Off", "Forced off"),
    ]


def test_a_forcing_group_shows_in_the_details_below_it(project: ProjectService) -> None:
    main, middle, ga = _groups(project)
    details = project.element_details("group_address", ga)
    assert details is not None
    assert (details.security, details.forced_by, details.central) == (
        "Auto",
        None,
        False,
    )
    assert project.set_element_security("group_range", main, "Off") is None
    assert project.history()[0].display_text == "Set group security"
    for kind, element_id in (("group_address", ga), ("group_range", middle)):
        details = project.element_details(kind, element_id)
        assert details is not None and details.forced_by is not None
        assert (details.forced_by.group_range_id, details.forced_by.mode) == (
            main,
            "Off",
        )
    main_details = project.element_details("group_range", main)
    assert main_details is not None
    assert (main_details.security, main_details.forced_by) == ("Off", None)
    assert main_details.central is None


def test_securing_needs_a_password(project: ProjectService) -> None:
    main, _middle, ga = _groups(project)
    assert project.set_element_security("group_address", ga, "On") == (
        project.set_element_security("group_range", main, "On")
    )
    reason = project.set_element_security("group_range", main, "On")
    assert reason is not None and "password" in reason.lower()


def test_a_group_is_not_forced_off_over_an_object_that_requires_security(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui.plugins.project.strings import S

    main, middle, ga = _groups(project)
    svc, pid = project._svc, project._pid  # pyright: ignore[reportPrivateUsage]
    assert pid is not None
    segment = next(
        s.id
        for a in svc.topology(pid, 0).areas
        if a.address == 1
        for ln in a.lines
        if ln.address == 1
        for s in ln.segments
    )
    device = svc.add_device(
        pid, segment, "M-1_H-1_P-1", address=5, com_objects=[("M-1_A-1_O-1_R-1", None)]
    )
    com_object = next(
        co.id for d in svc.devices(pid) if d.id == device for co in d.com_objects
    )
    svc.link_com_object(pid, com_object, ga, sending=True)
    project._bump()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(project, "com_object_requires_security", lambda _link: True)
    for group in (main, middle):
        assert project.set_element_security("group_range", group, "Off") == (
            S.GA_SECURE_REQUIRED
        )
    details = project.element_details("group_range", main)
    assert details is not None and details.security == "Auto"
    # Without such an object the group is forced off.
    monkeypatch.setattr(project, "com_object_requires_security", lambda _link: False)
    assert project.set_element_security("group_range", main, "Off") is None


def test_a_group_address_is_marked_central(project: ProjectService) -> None:
    _main, _middle, ga = _groups(project)
    project.set_group_address_central(ga, True)
    details = project.element_details("group_address", ga)
    assert details is not None and details.central
    assert project.history()[0].display_text == "Mark group address as central"
    project.set_group_address_central(ga, False)
    assert project.history()[0].display_text == "Remove central mark from group address"
    listed = next(g for g in project.group_addresses if g.id == ga)
    assert not listed.central


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


@pytest.mark.usefixtures("context")
def test_the_details_render_central_and_security(project: ProjectService) -> None:
    main, _middle, ga = _groups(project)
    project.set_element_security("group_range", main, "Off")
    panel = ElementDetailsPanel(
        on_rename=project.rename_element,
        on_set_description=project.set_element_description,
        on_set_address=project.set_element_address,
        on_set_security=project.set_element_security,
        on_set_central=project.set_group_address_central,
    )
    for kind, element_id in (("group_address", ga), ("group_range", main)):
        details = project.element_details(kind, element_id)
        assert details is not None
        for _frame in range(2):
            imgui.new_frame()
            imgui.begin("Details")
            panel.render(details)
            imgui.end()
            imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_secured_groups_and_addresses_show_the_shield(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, _middle, ga = _groups(project)
    project.set_project_password("geheim")
    assert project.set_element_security("group_range", main, "On") is None
    tree = project.get_group_range_tree()
    assert _forced_on(tree[0]) and _forced_on(tree[0].children[0])
    assert tree[0].children[0].group_addresses[0].data_secure
    shields: list[float | None] = []
    monkeypatch.setattr(
        group_addresses,
        "secure_icon",
        lambda *, x=None: shields.append(x) or False,
    )
    panel = GroupAddressesPanel(
        get_range_tree=project.get_group_range_tree,
        get_assignments_for_ga=project.get_assignments_for_ga,
        get_devices=lambda: [],
        on_create_ga_in=lambda _g, _a, _n: None,
        on_remove_ga=lambda _i: None,
        take_requested_ga=lambda: ga,
    )
    imgui.new_frame()
    imgui.begin("Group Addresses")
    panel.render()
    imgui.end()
    imgui.end_frame()
    # The main group, the middle group and the group address, opened to reveal it.
    assert len(shields) == 3
    assert project.set_element_security("group_range", main, "Auto") is None
    assert not _forced_on(project.get_group_range_tree()[0])


def test_a_group_address_has_a_data_type(project: ProjectService) -> None:
    _main, _middle, ga = _groups(project)
    details = project.element_details("group_address", ga)
    assert details is not None and details.datapoint_type == ""
    assert project.set_element_dpt(ga, "16.001") is None
    details = project.element_details("group_address", ga)
    assert details is not None and details.datapoint_type == "DPST-16-1"
    assert project.set_element_dpt(ga, "nonsense") is not None
    assert project.set_element_dpt(ga, "") is None
    details = project.element_details("group_address", ga)
    assert details is not None and details.datapoint_type == ""
    # Only a group address has one.
    range_details = project.element_details("group_range", _main)
    assert range_details is not None and range_details.datapoint_type is None


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize(
    "names", [{}, {"DPT-16": "Character string", "DPST-16-1": "ISO 8859-1"}]
)
def test_the_details_render_the_data_type(
    project: ProjectService, names: dict[str, str]
) -> None:
    _main, _middle, ga = _groups(project)
    project.set_element_dpt(ga, "DPST-16-1")
    panel = ElementDetailsPanel(
        on_rename=project.rename_element,
        on_set_description=project.set_element_description,
        on_set_address=project.set_element_address,
        on_set_dpt=project.set_element_dpt,
        dpt_names=lambda: names,
    )
    details = project.element_details("group_address", ga)
    assert details is not None
    for _frame in range(2):
        imgui.new_frame()
        imgui.begin("Details")
        panel.render(details)
        imgui.end()
        imgui.end_frame()
    assert panel._dpt == "DPST-16-1"
