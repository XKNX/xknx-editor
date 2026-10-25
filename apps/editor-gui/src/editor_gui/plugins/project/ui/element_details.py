"""The details of an area, a line or a segment selected in the Devices tree, of a group range or
group address in the Group Addresses tree, or of a function in the Buildings tree, shown in place
of the device's: its name, address, medium, description and status, each saved when its field is
left or its choice is made."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.medium_text import line_media, segment_media
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.status_field import choice_combo, status_combo
from editor_gui.widgets.dpi import label_column_width

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ElementDetails

_ERROR_COLOR = imgui.ImVec4(0.9, 0.4, 0.4, 1.0)


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
    ) -> None:
        self._on_rename = on_rename
        self._on_set_description = on_set_description
        self._on_set_address = on_set_address
        self._on_set_status = on_set_status
        self._on_set_medium = on_set_medium
        self._on_set_security = on_set_security
        self._on_set_central = on_set_central
        self._key: tuple[str, int] | None = None
        self._name = ""
        self._description = ""
        self._address = ""
        self._error = ""
        self._medium_error = ""
        self._security_error = ""

    def render(self, details: ElementDetails) -> None:
        if self._key != (details.kind, details.element_id):
            self._key = (details.kind, details.element_id)
            self._name = details.name
            self._description = details.description
            self._address = details.address
            self._error = ""
            self._medium_error = ""
            self._security_error = ""
        label_width = label_column_width(
            S.CONFIGURE_NAME,
            S.CONFIGURE_DESCRIPTION,
            S.GA_ADDRESS,
            S.STATUS,
            S.MEDIUM,
            S.DOMAIN_ADDRESS,
            S.GA_CENTRAL,
            S.GA_SECURITY,
        )
        if not imgui.begin_table("##element_details", 2):
            return
        imgui.table_setup_column(
            "##label", imgui.TableColumnFlags_.width_fixed, label_width
        )
        imgui.table_setup_column("##field", imgui.TableColumnFlags_.width_stretch)
        self._row(S.CONFIGURE_NAME)
        self._name_field(details)
        if details.has_address:
            self._row(S.GA_ADDRESS)
            self._address_field(details)
        if details.medium is not None:
            self._row(S.MEDIUM)
            self._medium_field(details, details.medium)
            if details.domain_address:
                self._row(S.DOMAIN_ADDRESS)
                imgui.text_unformatted(details.domain_address)
        self._row(S.CONFIGURE_DESCRIPTION)
        self._description_field(details)
        if details.status is not None:
            self._row(S.STATUS)
            chosen = status_combo("##element_status", details.status)
            if chosen is not None:
                self._on_set_status(details.kind, details.element_id, chosen)
        if details.central is not None:
            self._row(S.GA_CENTRAL)
            changed, central = imgui.checkbox("##element_central", details.central)
            if changed:
                self._on_set_central(details.element_id, central)
        if details.security is not None:
            self._row(S.GA_SECURITY)
            self._security_field(details, details.security)
        imgui.end_table()

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
        options = line_media() if details.kind == "line" else segment_media()
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
