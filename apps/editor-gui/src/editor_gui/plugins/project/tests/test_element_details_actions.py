"""What the fields of the details of the backbone, a line, a segment, a trade and a group address
do when the user chooses or enters something, and the reasons they show for what is refused."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import (
    ElementDetails,
    ProjectService,
    interface_bus_access,
)
from editor_gui.plugins.project.ui import element_details
from editor_gui.plugins.project.ui.element_details import ElementDetailsPanel


@pytest.fixture
def project(tmp_path: Path) -> Iterator[ProjectService]:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    yield proj
    proj.close()


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _User:
    """The choices and entries of the user, each made once: a drop-down (by its id) returns
    its value, an entry is finished (the edited field reports it was left)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.choices: dict[str, Any] = {}
        self.finished = False

        def choose(real: Any, id_index: int = 0) -> Any:
            def fake(*args: Any, **kwargs: Any) -> Any:
                result = real(*args, **kwargs)
                key = args[id_index]
                if key in self.choices:
                    return self.choices.pop(key)
                return result

            return fake

        for name in ("choice_combo", "status_combo", "bus_connection_combo"):
            monkeypatch.setattr(
                element_details, name, choose(getattr(element_details, name))
            )
        real_checkbox = element_details.checkbox
        monkeypatch.setattr(
            element_details,
            "checkbox",
            lambda label, value: (
                (True, self.choices.pop(label))
                if label in self.choices
                else real_checkbox(label, value)
            ),
        )
        real_dpt = element_details.dpt_combo
        monkeypatch.setattr(
            element_details,
            "dpt_combo",
            lambda key, token, names, hint: (
                (True, self.choices.pop(key))
                if key in self.choices
                else real_dpt(key, token, names, hint)
            ),
        )
        real_deactivated = imgui.is_item_deactivated_after_edit
        monkeypatch.setattr(
            imgui,
            "is_item_deactivated_after_edit",
            lambda: self.finished or real_deactivated(),
        )


def _panel(
    project: ProjectService, interfaces: list[tuple[str, str]] | None = None
) -> ElementDetailsPanel:
    return ElementDetailsPanel(
        on_rename=project.rename_element,
        on_set_description=project.set_element_description,
        on_set_address=project.set_element_address,
        on_set_status=project.set_completion_status,
        on_set_medium=project.set_element_medium,
        on_set_security=project.set_element_security,
        on_set_central=project.set_group_address_central,
        on_set_ip_routing=lambda latency, address: project.set_ip_routing(
            latency=latency, multicast_address=address
        ),
        on_set_backbone_security=project.set_element_backbone_security,
        on_set_bus_connection=project.set_backbone_bus_connection,
        found_interfaces=lambda: interfaces or [],
        on_set_dpt=project.set_element_dpt,
        on_set_domain_address=project.set_element_domain_address,
        on_set_number=lambda trade_id, number: project.set_trade_field(
            trade_id, "number", number
        ),
        get_bus_load=project.bus_load_texts,
    )


def _show(panel: ElementDetailsPanel, details: ElementDetails, frames: int = 2) -> None:
    for _ in range(frames):
        imgui.new_frame()
        imgui.begin("Details")
        panel.render(details)
        imgui.end()
        imgui.end_frame()


def _details(project: ProjectService, kind: str, element_id: int) -> ElementDetails:
    details = project.element_details(kind, element_id)
    assert details is not None
    return details


def _enter(user: _User, panel: ElementDetailsPanel, details: ElementDetails) -> None:
    """Finish the entries made in the panel's fields."""
    user.finished = True
    _show(panel, details, 1)
    user.finished = False


@pytest.mark.usefixtures("context")
def test_the_backbone_details(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _User(monkeypatch)
    backbone = next(a for a in project.get_areas() if a.area_number == 0).id
    access = interface_bus_access("IP-Interface", "192.168.16.88", 3671)
    panel = _panel(project, [(access, "IP-Interface (192.168.16.88:3671 Udp)")])
    details = _details(project, "backbone", backbone)
    _show(panel, details)
    user.choices["##latency"] = "4000"
    user.choices["##element_status"] = "Tested"
    user.choices["##bus_connection"] = access
    _show(panel, details)
    details = _details(project, "backbone", backbone)
    assert (details.latency, details.status, details.bus_access) == (
        4000,
        "Tested",
        access,
    )
    # A multicast address that is none is refused with the reason; a valid one is taken.
    panel._multicast = "10.0.0.1"  # pyright: ignore[reportPrivateUsage]
    _enter(user, panel, details)
    assert panel._routing_error  # pyright: ignore[reportPrivateUsage]
    _show(panel, details)
    panel._multicast = "239.1.2.3"  # pyright: ignore[reportPrivateUsage]
    _enter(user, panel, details)
    assert _details(project, "backbone", backbone).multicast_address == "239.1.2.3"
    # Securing the IP routing needs the project password.
    user.choices["##backbone_security"] = "On"
    _show(panel, details)
    assert panel._security_error  # pyright: ignore[reportPrivateUsage]
    # Back to Automatic.
    user.choices["##bus_connection"] = ""
    _show(panel, _details(project, "backbone", backbone))
    assert _details(project, "backbone", backbone).bus_access == ""


@pytest.mark.usefixtures("context")
def test_a_line_with_its_medium_and_domain_address(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _User(monkeypatch)
    area = next(a for a in project.get_areas() if a.area_number == 1)
    line = next(ln for ln in project.get_lines(area.id) if ln.line_number == 1)
    panel = _panel(project)
    user.choices["##element_medium"] = "MT-1"
    _show(panel, _details(project, "line", line.id))
    details = _details(project, "line", line.id)
    assert details.medium == "MT-1" and details.domain_address
    # The number of devices and the bus current show for a line.
    _show(panel, details)
    # A domain address out of range is refused with the reason; "Generate New" makes one.
    panel._domain = "999"  # pyright: ignore[reportPrivateUsage]
    _enter(user, panel, details)
    assert panel._domain_error  # pyright: ignore[reportPrivateUsage]
    _show(panel, details)
    panel._domain = "7"  # pyright: ignore[reportPrivateUsage]
    _enter(user, panel, details)
    assert _details(project, "line", line.id).domain_address == "7"
    # A medium the topology does not allow shows the reason: the backbone is IP or TP.
    backbone = next(a for a in project.get_areas() if a.area_number == 0)
    user.choices["##element_medium"] = "MT-2"
    _show(panel, _details(project, "backbone", backbone.id))
    assert panel._medium_error  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("context")
def test_a_trade_number_and_a_group_address(
    project: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _User(monkeypatch)
    trade = project.create_trade(None, "Lighting")
    assert trade is not None
    panel = _panel(project)
    details = _details(project, "trade", trade)
    _show(panel, details)
    panel._number = " 07 "  # pyright: ignore[reportPrivateUsage]
    _enter(user, panel, details)
    assert _details(project, "trade", trade).number == "07"
    # A group address: Central, its data type entered as a token, and Security.
    ga = project.create_group_address("1/1/1", "Light")
    assert ga is not None
    details = _details(project, "group_address", ga)
    user.choices["##element_central"] = True
    _show(panel, details)
    details = _details(project, "group_address", ga)
    assert details.central is True
    panel._dpt = "DPST-1-1"  # pyright: ignore[reportPrivateUsage]
    _enter(user, panel, details)
    assert _details(project, "group_address", ga).datapoint_type == "DPST-1-1"
    panel._dpt = "nonsense"  # pyright: ignore[reportPrivateUsage]
    _enter(user, panel, _details(project, "group_address", ga))
    assert panel._dpt_error  # pyright: ignore[reportPrivateUsage]
    # With the master data's names the type is chosen from them.
    names_panel = ElementDetailsPanel(
        on_rename=project.rename_element,
        on_set_description=project.set_element_description,
        on_set_address=project.set_element_address,
        on_set_dpt=project.set_element_dpt,
        dpt_names=lambda: {"DPST-1-1": "1.001 Switch", "DPST-5-1": "5.001 Percentage"},
    )
    user.choices["element_dpt"] = "DPST-5-1"
    _show(names_panel, _details(project, "group_address", ga))
    assert _details(project, "group_address", ga).datapoint_type == "DPST-5-1"
    user.choices["##element_security"] = "On"
    _show(panel, _details(project, "group_address", ga))
    assert panel._security_error  # pyright: ignore[reportPrivateUsage]
    _show(panel, _details(project, "group_address", ga))
