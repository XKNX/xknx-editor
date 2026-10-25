"""The details of the backbone, an area, a line or a segment selected in the Topology tree, of a
group range or group address in the Group Addresses tree, or of a function in the Buildings tree,
shown in place of the device's: its name, address, medium, description and status, each saved
when its field is left or its choice is made."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.medium_text import (
    backbone_media,
    line_media,
    segment_media,
)
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.status_field import (
    bus_connection_combo,
    choice_combo,
    status_combo,
)
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.dpi import label_column_width
from editor_gui.widgets.dpt_select import dpt_combo
from xknxeditor.proj.core.medium import has_domain_address

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ElementDetails

_ERROR_COLOR = imgui.ImVec4(0.9, 0.4, 0.4, 1.0)


def latency_options() -> list[tuple[int, str]]:
    """The choices of the network latency (milliseconds, text)."""
    return [
        (400, S.LATENCY_LAN),
        (1000, S.LATENCY_WLAN),
        (2000, S.LATENCY_WAN),
        (4000, S.LATENCY_MOBILE),
        (8000, S.LATENCY_SATELLITE),
    ]


def security_options() -> list[tuple[str, str]]:
    """The choices of a Security drop-down, as (mode, text)."""
    return [
        ("Auto", S.GA_SECURITY_AUTO),
        ("On", S.SECURITY_FORCED_ON),
        ("Off", S.SECURITY_FORCED_OFF),
    ]


class ElementDetailsPanel:
    def __init__(
        self,
        on_rename: Callable[[str, int, str], None],
        on_set_description: Callable[[str, int, str], None],
        on_set_address: Callable[[str, int, str], str | None],
        on_set_status: Callable[[str, int, str], None] = lambda _k, _i, _s: None,
        on_set_medium: Callable[[str, int, str], str | None] = lambda _k, _i, _m: None,
        on_set_security: Callable[[str, int, str], str | None] = (
            lambda _k, _i, _m: None
        ),
        on_set_central: Callable[[int, bool], None] = lambda _i, _c: None,
        on_set_ip_routing: Callable[[int | None, str | None], str | None] = (
            lambda _l, _m: None
        ),
        on_set_backbone_security: Callable[[str], str | None] = lambda _m: None,
        on_set_bus_connection: Callable[[str], None] = lambda _p: None,
        found_interfaces: Callable[[], list[tuple[str, str]]] = list,
        on_set_dpt: Callable[[int, str], str | None] = lambda _i, _d: None,
        dpt_names: Callable[[], Mapping[str, str]] = dict,
        on_set_domain_address: Callable[[str, int, str | None], str | None] = (
            lambda _k, _i, _t: None
        ),
    ) -> None:
        """``dpt_names`` are the names of the data point types by token, from the master data
        ({} without it: the type is then entered as its token). ``on_set_domain_address`` sets
        the domain address as entered, or generates a new one for None."""
        self._on_set_domain_address = on_set_domain_address
        self._on_rename = on_rename
        self._on_set_description = on_set_description
        self._on_set_address = on_set_address
        self._on_set_status = on_set_status
        self._on_set_medium = on_set_medium
        self._on_set_security = on_set_security
        self._on_set_central = on_set_central
        self._on_set_ip_routing = on_set_ip_routing
        self._on_set_backbone_security = on_set_backbone_security
        self._on_set_bus_connection = on_set_bus_connection
        self._found_interfaces = found_interfaces
        self._on_set_dpt = on_set_dpt
        self._dpt_names = dpt_names
        self._key: tuple[str, int] | None = None
        self._dpt = ""
        self._dpt_error = ""
        self._domain = ""
        self._domain_error = ""
        self._name = ""
        self._description = ""
        self._address = ""
        self._error = ""
        self._medium_error = ""
        self._security_error = ""
        self._multicast = ""
        self._routing_error = ""

    def render(self, details: ElementDetails) -> None:
        if self._key != (details.kind, details.element_id):
            self._key = (details.kind, details.element_id)
            self._name = details.name
            self._description = details.description
            self._address = details.address
            self._error = ""
            self._medium_error = ""
            self._security_error = ""
            self._multicast = details.multicast_address
            self._routing_error = ""
            self._dpt = details.datapoint_type or ""
            self._dpt_error = ""
            self._domain = details.domain_address
            self._domain_error = ""
        label_width = label_column_width(
            S.CONFIGURE_NAME,
            S.CONFIGURE_DESCRIPTION,
            S.GA_ADDRESS,
            S.STATUS,
            S.MEDIUM,
            S.DOMAIN_ADDRESS,
            S.GA_CENTRAL,
            S.GA_SECURITY,
            S.GA_DATA_TYPE,
            S.BACKBONE_MEDIUM,
            S.NETWORK_LATENCY,
            S.MULTICAST_ADDRESS,
            S.BUS_CONNECTION,
        )
        if not imgui.begin_table("##element_details", 2):
            return
        imgui.table_setup_column(
            "##label", imgui.TableColumnFlags_.width_fixed, label_width
        )
        imgui.table_setup_column("##field", imgui.TableColumnFlags_.width_stretch)
        self._row(S.CONFIGURE_NAME)
        self._name_field(details)
        if details.latency is not None:
            self._backbone_rows(details, details.latency)
            imgui.end_table()
            return
        if details.has_address:
            self._row(S.GA_ADDRESS)
            self._address_field(details)
        if details.medium is not None:
            self._row(S.MEDIUM)
            self._medium_field(details, details.medium)
            if has_domain_address(details.medium):
                self._row(S.DOMAIN_ADDRESS)
                self._domain_field(details)
        self._row(S.CONFIGURE_DESCRIPTION)
        self._description_field(details)
        if details.status is not None:
            self._row(S.STATUS)
            chosen = status_combo("##element_status", details.status)
            if chosen is not None:
                self._on_set_status(details.kind, details.element_id, chosen)
        if details.central is not None:
            self._row(S.GA_CENTRAL)
            changed, central = checkbox("##element_central", details.central)
            if changed:
                self._on_set_central(details.element_id, central)
        if details.security is not None:
            self._row(S.GA_SECURITY)
            self._security_field(details, details.security)
        if details.datapoint_type is not None:
            self._row(S.GA_DATA_TYPE)
            self._dpt_field(details, details.datapoint_type)
        imgui.end_table()

    def _domain_field(self, details: ElementDetails) -> None:
        """The domain address of an RF, PL or IoT line or segment, entered as it is shown, and
        "Generate New" beside it; the reason an address is refused shows below them."""
        button = S.DOMAIN_ADDRESS_GENERATE
        style = imgui.get_style()
        button_width = imgui.calc_text_size(button).x + style.frame_padding.x * 2
        imgui.set_next_item_width(-button_width - style.item_spacing.x)
        _, self._domain = imgui.input_text("##element_domain", self._domain)
        if (
            imgui.is_item_deactivated_after_edit()
            and self._domain.strip() != details.domain_address
        ):
            self._domain_error = (
                self._on_set_domain_address(
                    details.kind, details.element_id, self._domain
                )
                or ""
            )
        if not imgui.is_item_active() and not self._domain_error:
            self._domain = details.domain_address
        imgui.same_line()
        if imgui.button(f"{button}##element_domain_new"):
            self._domain_error = (
                self._on_set_domain_address(details.kind, details.element_id, None)
                or ""
            )
        if self._domain_error:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_ERROR_COLOR, self._domain_error)
            imgui.pop_text_wrap_pos()

    def _dpt_field(self, details: ElementDetails, token: str) -> None:
        """A group address's data point type: chosen from the master data's, or without it
        entered as its token; the reason a type is refused shows below it."""
        names = self._dpt_names()
        imgui.set_next_item_width(-1)
        if names:
            changed, chosen = dpt_combo("element_dpt", token, names, S.ML_FILTER_HINT)
            if changed and chosen != token:
                self._dpt_error = self._on_set_dpt(details.element_id, chosen) or ""
        else:
            _, self._dpt = imgui.input_text_with_hint(
                "##element_dpt", "DPST-1-1", self._dpt
            )
            if imgui.is_item_deactivated_after_edit() and self._dpt.strip() != token:
                self._dpt_error = (
                    self._on_set_dpt(details.element_id, self._dpt.strip()) or ""
                )
            if not imgui.is_item_active() and not self._dpt_error:
                self._dpt = token
        if self._dpt_error:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_ERROR_COLOR, self._dpt_error)
            imgui.pop_text_wrap_pos()

    def _backbone_rows(self, details: ElementDetails, latency: int) -> None:
        """The backbone: description, status, its medium (IP or TP), the network latency, the
        multicast address and the security of the IP routing, and the bus connection."""
        self._row(S.CONFIGURE_DESCRIPTION)
        self._description_field(details)
        if details.status is not None:
            self._row(S.STATUS)
            chosen = status_combo("##element_status", details.status)
            if chosen is not None:
                self._on_set_status(details.kind, details.element_id, chosen)
        if details.medium is not None:
            self._row(S.BACKBONE_MEDIUM)
            self._medium_field(details, details.medium)
        self._row(S.NETWORK_LATENCY)
        chosen = choice_combo(
            "##latency",
            [(str(value), text) for value, text in latency_options()],
            str(latency),
        )
        if chosen is not None:
            self._routing_error = self._on_set_ip_routing(int(chosen), None) or ""
        self._row(S.MULTICAST_ADDRESS)
        imgui.set_next_item_width(-1)
        _, self._multicast = imgui.input_text("##multicast", self._multicast)
        if (
            imgui.is_item_deactivated_after_edit()
            and self._multicast.strip() != details.multicast_address
        ):
            self._routing_error = (
                self._on_set_ip_routing(None, self._multicast.strip()) or ""
            )
        if not imgui.is_item_active() and not self._routing_error:
            self._multicast = details.multicast_address
        if self._routing_error:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_ERROR_COLOR, self._routing_error)
            imgui.pop_text_wrap_pos()
        self._row(S.GA_SECURITY)
        chosen = choice_combo(
            "##backbone_security",
            security_options(),
            details.backbone_security or "Auto",
        )
        if chosen is not None:
            self._security_error = self._on_set_backbone_security(chosen) or ""
        if self._security_error:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_ERROR_COLOR, self._security_error)
            imgui.pop_text_wrap_pos()
        self._row(S.BUS_CONNECTION)
        stored = [("", S.BUS_CONNECTION_AUTOMATIC)]
        if details.bus_access:
            stored.append((details.bus_access, details.bus_connection))
        found = [
            (value, text)
            for value, text in self._found_interfaces()
            if value != details.bus_access
        ]
        chosen = bus_connection_combo(
            "##bus_connection",
            details.bus_access,
            details.bus_connection or S.BUS_CONNECTION_AUTOMATIC,
            stored,
            found,
        )
        if chosen is not None:
            self._on_set_bus_connection(chosen)

    def _security_field(self, details: ElementDetails, security: str) -> None:
        """The element's own Security; below it, the group above that overrides it, and the
        reason a choice cannot be made, until another choice."""
        options = security_options()
        chosen = choice_combo("##element_security", options, security)
        if chosen is not None:
            self._security_error = (
                self._on_set_security(details.kind, details.element_id, chosen) or ""
            )
        forced = details.forced_by
        if forced is not None:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_disabled(
                S.SECURITY_FORCED_BY.format(
                    name=forced.name, mode=dict(options).get(forced.mode, forced.mode)
                )
            )
            imgui.pop_text_wrap_pos()
        if self._security_error:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_ERROR_COLOR, self._security_error)
            imgui.pop_text_wrap_pos()

    @staticmethod
    def _row(label: str) -> None:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.align_text_to_frame_padding()
        imgui.text(label)
        imgui.table_next_column()

    def _name_field(self, details: ElementDetails) -> None:
        imgui.set_next_item_width(-1)
        _, self._name = imgui.input_text("##element_name", self._name)
        if imgui.is_item_deactivated_after_edit() and self._name != details.name:
            self._on_rename(details.kind, details.element_id, self._name.strip())
        if not imgui.is_item_active() and self._name != details.name:
            self._name = details.name

    def _address_field(self, details: ElementDetails) -> None:
        """A line's address after its area's ("1."), a middle group's after its main group's
        ("1/"); a group address's in full."""
        if details.prefix:
            imgui.align_text_to_frame_padding()
            imgui.text_unformatted(details.prefix)
            imgui.same_line(0.0, 0.0)
        imgui.set_next_item_width(-1)
        numeric = details.kind != "group_address"
        flags = imgui.InputTextFlags_.chars_decimal if numeric else 0
        if not details.editable:
            flags |= imgui.InputTextFlags_.read_only
        _, self._address = imgui.input_text("##element_address", self._address, flags)
        if imgui.is_item_deactivated_after_edit() and details.editable:
            self._error = (
                self._on_set_address(details.kind, details.element_id, self._address)
                or ""
            )
        if not imgui.is_item_active() and self._address != details.address:
            self._address = details.address
        if self._error:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_ERROR_COLOR, self._error)
            imgui.pop_text_wrap_pos()

    def _medium_field(self, details: ElementDetails, medium: str) -> None:
        """A line offers every medium, a segment those of a sub-segment; a change the media do
        not allow shows its reason below, until another choice."""
        options = (
            segment_media()
            if details.kind == "segment"
            else backbone_media()
            if details.kind == "backbone"
            else line_media()
        )
        chosen = choice_combo("##element_medium", options, medium)
        if chosen is not None:
            self._medium_error = (
                self._on_set_medium(details.kind, details.element_id, chosen) or ""
            )
        if self._medium_error:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_ERROR_COLOR, self._medium_error)
            imgui.pop_text_wrap_pos()

    def _description_field(self, details: ElementDetails) -> None:
        """Two lines high, like a device's description."""
        height = (
            imgui.get_text_line_height() * 2 + imgui.get_style().frame_padding.y * 2
        )
        _, self._description = imgui.input_text_multiline(
            "##element_description", self._description, imgui.ImVec2(-1, height)
        )
        if (
            imgui.is_item_deactivated_after_edit()
            and self._description != details.description
        ):
            self._on_set_description(
                details.kind, details.element_id, self._description
            )
        if not imgui.is_item_active() and self._description != details.description:
            self._description = details.description
