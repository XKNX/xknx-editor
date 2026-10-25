"""The IP settings of an IP device as its details show and change them."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.service import IpSettings
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import configure

_DEVICE = cast(Any, SimpleNamespace(node_id=3, app=None, name="IP router"))


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    imgui.get_io().display_size = imgui.ImVec2(800, 600)
    imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _Section:
    """The section of one device: what it shows, and the changes it asks for."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, settings: IpSettings) -> None:
        self.settings = settings
        self.labels: list[str] = []
        self.fields: dict[str, str] = {}
        self.radios: dict[str, bool] = {}
        self.changes: list[dict[str, str]] = []
        self.reply: str | None = None
        # The radio button to click and the field to type into ("##ip_<field>", text).
        self.click: str | None = None
        self.typed: tuple[str, str] | None = None
        self._edited = False
        monkeypatch.setattr(configure, "collapsing_header", lambda _label: True)
        monkeypatch.setattr(configure, "radio_button", self._radio)
        real_text_disabled = imgui.text_disabled
        monkeypatch.setattr(
            imgui,
            "text_disabled",
            lambda text: (self.labels.append(text), real_text_disabled(text)),
        )
        monkeypatch.setattr(imgui, "input_text_with_hint", self._input)
        monkeypatch.setattr(imgui, "input_text", self._input_plain)
        real_deactivated = imgui.is_item_deactivated_after_edit
        monkeypatch.setattr(
            imgui,
            "is_item_deactivated_after_edit",
            lambda: self._take_edit() or real_deactivated(),
        )

        def unused(*_args: Any) -> None:
            raise AssertionError("not expected")

        self.panel = configure.ConfigurePanel(
            get_devices=lambda: [_DEVICE],
            get_selected_device=lambda: _DEVICE,
            set_selected_device=unused,
            on_param_change=unused,
            on_individual_address_change=unused,
            on_name_change=unused,
            on_description_change=unused,
            set_flag=unused,
            get_links_for_com_object=cast(Any, unused),
            get_all_group_addresses=cast(Any, unused),
            on_link_com_object=unused,
            on_unlink_com_object=unused,
            on_auto_create_gas=unused,
            get_device_info=lambda _node_id: None,
            get_ip_settings=lambda _node_id: self.settings,
            on_ip_settings_change=self._change,
        )

    def _radio(self, label: str, active: bool, **_: Any) -> bool:
        self.radios[label] = active
        return label == self.click

    def _input(self, label: str, _hint: str, value: str, *_a: Any) -> tuple[bool, str]:
        self.fields[label] = value
        if self.typed is not None and self.typed[0] == label:
            self._edited = True
            return True, self.typed[1]
        return False, value

    def _input_plain(self, label: str, value: str, *_a: Any) -> tuple[bool, str]:
        self.fields[label] = value
        return False, value

    def _take_edit(self) -> bool:
        edited, self._edited = self._edited, False
        return edited

    def _change(self, node_id: int, **change: str) -> str | None:
        assert node_id == 3
        self.changes.append(change)
        return self.reply

    def render(self) -> None:
        self.labels.clear()
        self.fields.clear()
        self.radios.clear()
        imgui.new_frame()
        imgui.begin("Device")
        self.panel._render_ip_config(_DEVICE)  # pyright: ignore[reportPrivateUsage]
        imgui.end()
        imgui.end_frame()


def _settings(**changes: Any) -> IpSettings:
    values: dict[str, Any] = {
        "tool_managed": True,
        "assign": "Auto",
        "ip_address": None,
        "subnet_mask": None,
        "default_gateway": None,
        "mac_address": "70:B3:D5:DC:8E:11",
    }
    values.update(changes)
    return IpSettings(**values)


@pytest.mark.usefixtures("context")
def test_automatic_shows_the_assigned_address_and_the_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _Section(monkeypatch, _settings(ip_address="0.0.0.0"))
    section.render()
    assert section.radios == {
        S.CONFIGURE_IP_AUTOMATIC: True,
        S.CONFIGURE_IP_STATIC: False,
    }
    # An unknown address shows as an empty field (with "Unknown" as its hint).
    assert section.fields["##ip_assigned"] == ""
    assert S.CONFIGURE_IP_SUBNET not in section.labels
    assert section.fields[f"##ip_{S.CONFIGURE_IP_MAC}"] == "70:B3:D5:DC:8E:11"
    section.click = S.CONFIGURE_IP_STATIC
    section.render()
    assert section.changes == [{"assign": "Fixed"}]


@pytest.mark.usefixtures("context")
def test_static_shows_its_addresses_and_keeps_rejected_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _Section(
        monkeypatch,
        _settings(assign="Fixed", ip_address="192.168.1.10", mac_address=None),
    )
    section.render()
    assert section.fields["##ip_ip_address"] == "192.168.1.10"
    # A missing address shows as 255.255.255.255, a missing MAC address as unknown.
    assert section.fields["##ip_subnet_mask"] == "255.255.255.255"
    assert section.fields[f"##ip_{S.CONFIGURE_IP_MAC}"] == S.CONFIGURE_IP_UNKNOWN
    section.reply = S.CONFIGURE_IP_INVALID
    section.typed = ("##ip_default_gateway", "1.2.3.")
    section.render()
    assert section.changes == [{"default_gateway": "1.2.3."}]
    section.typed = None
    section.render()
    # The rejected text stays in its field until it is changed.
    assert section.fields["##ip_default_gateway"] == "1.2.3."


@pytest.mark.usefixtures("context")
def test_an_application_with_its_own_ip_configuration_shows_the_assigned_address_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _Section(monkeypatch, _settings(tool_managed=False))
    section.render()
    assert section.radios == {}
    assert S.CONFIGURE_IP_MAC not in section.labels
    assert S.CONFIGURE_IP_ASSIGNED in section.labels
    # Leaving the empty field of an unknown address stores nothing.
    section.typed = ("##ip_assigned", " ")
    section.render()
    assert section.changes == []
    section.settings = _settings(tool_managed=False, ip_address="10.0.0.7")
    section.typed = ("##ip_assigned", "")
    section.render()
    # Emptying a known one stores 0.0.0.0, which shows as unknown.
    assert section.changes == [{"ip_address": "0.0.0.0"}]


@pytest.mark.usefixtures("context")
def test_the_multicast_address_on_an_ip_medium(monkeypatch: pytest.MonkeyPatch) -> None:
    section = _Section(monkeypatch, _settings(multicast_address="224.0.23.12"))
    section.render()
    assert section.fields[f"##ip_{S.MULTICAST_ADDRESS}"] == "224.0.23.12"
