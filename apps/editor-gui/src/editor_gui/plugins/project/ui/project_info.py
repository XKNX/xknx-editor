"""Project information view: the project metadata carried over on import, with the project name,
status, codepage and password editable."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.code_page import PROJECT_CODE_PAGES, code_page_text
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.status_field import choice_combo, status_combo
from editor_gui.strings import S as APP_S
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.password_form import NewPasswordForm
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from xknxeditor.proj.core.addressing import GroupAddressStyle

if TYPE_CHECKING:
    from pathlib import Path

    from editor_gui.plugins.project.service import ProjectInfo


def _style_text(style: str) -> str:
    """The shown name of a group address style (stored as Free, TwoLevel or ThreeLevel)."""
    names = {
        GroupAddressStyle.FREE.value: APP_S.NEW_PROJECT_STYLE_FREE,
        GroupAddressStyle.TWO_LEVEL.value: APP_S.NEW_PROJECT_STYLE_TWO_LEVEL,
        GroupAddressStyle.THREE_LEVEL.value: APP_S.NEW_PROJECT_STYLE_THREE_LEVEL,
    }
    return names.get(style, style)


class ProjectInfoPanel:
    def __init__(
        self,
        get_project_info: "Callable[[], ProjectInfo | None]",
        on_rename: Callable[[str], None] | None = None,
        on_set_password: Callable[[str | None], None] | None = None,
        needs_password: Callable[[], bool] = lambda: False,
        get_certificates: Callable[[], list[tuple[bytes, str]]] | None = None,
        on_remove_certificate: Callable[[bytes], None] | None = None,
        on_add_certificate: Callable[[], None] | None = None,
        get_status: Callable[[], str | None] = lambda: None,
        on_set_status: Callable[[str], None] = lambda _s: None,
        get_path: "Callable[[], Path | None]" = lambda: None,
        on_set_code_page: Callable[[str | None], None] | None = None,
    ) -> None:
        self._get_project_info = get_project_info
        self._on_set_code_page = on_set_code_page
        self._get_path = get_path
        self._get_status = get_status
        self._on_set_status = on_set_status
        self._on_rename = on_rename
        self._on_set_password = on_set_password
        self._needs_password = needs_password
        self._name_buffer = ""
        self._name_editing = False
        self._password_dialog_open = False
        self._password_form = NewPasswordForm()
        self._get_certificates = get_certificates
        self._on_remove_certificate = on_remove_certificate
        self._on_add_certificate = on_add_certificate
        self._certificates_open = False

    def render(self) -> None:
        info = self._get_project_info()
        if info is None:
            imgui.text_disabled(S.PROJECT_INFO_EMPTY)
            return
        path = self._get_path()
        rows = [
            (S.PROJECT_INFO_FILE, str(path) if path is not None else None),
            (S.PROJECT_INFO_INSTALLATION, info.installation_name),
            (S.PROJECT_INFO_GA_STYLE, _style_text(info.group_address_style)),
            (S.PROJECT_INFO_CREATED_BY, info.created_by),
            (S.PROJECT_INFO_TOOL_VERSION, info.tool_version),
            (S.PROJECT_INFO_SCHEMA_VERSION, info.schema_version),
            (S.PROJECT_INFO_LAST_MODIFIED, info.last_modified),
            (S.PROJECT_INFO_GUID, info.guid),
            (S.PROJECT_INFO_ID, info.id),
            (S.PROJECT_INFO_ORIGINAL_ID, info.original_project_id),
            (S.PROJECT_INFO_MASTER_DATA, self._artifact(info.master_data_size)),
            (S.PROJECT_INFO_VALIDATION, self._artifact(info.validation_size)),
            (S.PROJECT_INFO_CERTIFICATE, self._artifact(info.certificate_size)),
        ]
        # Laid out like the Device panel's fields: grey labels in a column as wide as the longest
        # of them, the values beside them, no lines between the rows.
        labels = [
            S.PROJECT_INFO_NAME,
            S.STATUS,
            S.PROJECT_INFO_CODE_PAGE,
            S.PROJECT_INFO_PASSWORD,
            S.PROJECT_INFO_CERTIFICATES,
            *(label for label, _value in rows),
        ]
        label_w = max(imgui.calc_text_size(label).x for label in labels) + px(12)
        if not imgui.begin_table("##project_info", 2):
            return
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed, label_w)
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_stretch, 1.0)
        self._render_name(info)
        self._render_status()
        self._render_code_page(info)
        self._render_password(info)
        self._render_certificates_row()
        for label, value in rows:
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.align_text_to_frame_padding()
            imgui.text_disabled(label)
            imgui.table_set_column_index(1)
            imgui.align_text_to_frame_padding()
            imgui.text_wrapped(value or "-")
        imgui.end_table()
        self._render_password_dialog()
        self._render_certificates_dialog()

    def _render_name(self, info: "ProjectInfo") -> None:
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.PROJECT_INFO_NAME)
        imgui.table_set_column_index(1)
        if self._on_rename is None:
            imgui.text_wrapped(info.name or "-")
            return
        # The field shows the project's name except while it is being edited.
        if not self._name_editing:
            self._name_buffer = info.name
        imgui.set_next_item_width(-1)
        _, self._name_buffer = imgui.input_text("##project_name", self._name_buffer)
        self._name_editing = imgui.is_item_active()
        # Renamed when the field is left; an empty name keeps the current one.
        if imgui.is_item_deactivated_after_edit():
            self._on_rename(self._name_buffer)

    def _render_status(self) -> None:
        status = self._get_status()
        if status is None:
            return
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.STATUS)
        imgui.table_set_column_index(1)
        chosen = status_combo("##project_status", status)
        if chosen is not None:
            self._on_set_status(chosen)

    def _render_code_page(self, info: "ProjectInfo") -> None:
        """The codepage some devices encode their texts with; "" stands for the system language."""
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.PROJECT_INFO_CODE_PAGE)
        imgui.table_set_column_index(1)
        if self._on_set_code_page is None:
            imgui.text_wrapped(code_page_text(info.code_page))
            return
        options = [(code or "", code_page_text(code)) for code in PROJECT_CODE_PAGES]
        current = info.code_page or ""
        if all(value != current for value, _text in options):
            options.insert(0, (current, code_page_text(current)))
        chosen = choice_combo("##project_code_page", options, current)
        if imgui.is_item_hovered():
            imgui.set_tooltip(S.PROJECT_INFO_CODE_PAGE_HINT)
        if chosen is not None:
            self._on_set_code_page(chosen or None)

    def _render_certificates_row(self) -> None:
        """The number of device certificates, with the dialog that manages them."""
        if self._get_certificates is None:
            return
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.PROJECT_INFO_CERTIFICATES)
        imgui.table_set_column_index(1)
        imgui.align_text_to_frame_padding()
        imgui.text(str(len(self._get_certificates())))
        imgui.same_line()
        if imgui.button(S.PROJECT_CERTIFICATES_MANAGE):
            self._certificates_open = True

    def _render_certificates_dialog(self) -> None:
        """The project's device certificates: serial number and the device with it, each
        removable, and "Add Device Certificate…"."""
        from editor_gui.device_info import serial_text

        title = f"{S.PROJECT_CERTIFICATES_TITLE}###device_certificates"
        if self._certificates_open:
            imgui.open_popup(title)
            self._certificates_open = False
        if not begin_dialog(title, width=520.0):
            return
        certificates = self._get_certificates() if self._get_certificates else []
        if not certificates:
            imgui.text_disabled(S.PROJECT_CERTIFICATES_EMPTY)
        elif begin_data_table(
            "##certificates",
            3,
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.reorderable,
        ):
            imgui.table_setup_column(S.PROJECT_CERTIFICATES_SERIAL)
            imgui.table_setup_column(S.PROJECT_CERTIFICATES_DEVICE)
            imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed)
            data_table_headers()
            for serial, device in certificates:
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.align_text_to_frame_padding()
                imgui.text(serial_text(serial) or serial.hex().upper())
                imgui.table_next_column()
                imgui.text(device or "-")
                imgui.table_next_column()
                if self._on_remove_certificate is not None and imgui.button(
                    f"{S.PROJECT_CERTIFICATES_REMOVE}##{serial.hex()}"
                ):
                    self._on_remove_certificate(serial)
            imgui.end_table()
        labels = [S.PROJECT_CERTIFICATES_ADD, S.BTN_CLOSE]
        clicked = dialog_buttons(*labels)
        if clicked == 0 and self._on_add_certificate is not None:
            imgui.close_current_popup()
            self._on_add_certificate()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_password(self, info: "ProjectInfo") -> None:
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.PROJECT_INFO_PASSWORD)
        imgui.table_set_column_index(1)
        imgui.align_text_to_frame_padding()
        imgui.text(
            S.PROJECT_INFO_PROTECTED
            if info.password_protected
            else S.PROJECT_INFO_NOT_PROTECTED
        )
        if self._on_set_password is None:
            return
        label = (
            S.PROJECT_CHANGE_PASSWORD
            if info.password_protected
            else S.PROJECT_SET_PASSWORD
        )
        # The buttons go on in the next line where the panel is narrow.
        same_line_if_fits(button_width(label))
        if imgui.button(label):
            self._password_form.reset()
            self._password_dialog_open = True
        if info.password_protected:
            same_line_if_fits(button_width(S.PROJECT_REMOVE_PASSWORD))
            # A project with KNX Secure data keeps its password: its keys are only protected by it.
            required = self._needs_password()
            imgui.begin_disabled(required)
            if imgui.button(S.PROJECT_REMOVE_PASSWORD):
                self._on_set_password(None)
            imgui.end_disabled()
            if required and imgui.is_item_hovered(
                imgui.HoveredFlags_.allow_when_disabled
            ):
                imgui.set_tooltip(S.PROJECT_PASSWORD_REQUIRED)

    def _render_password_dialog(self) -> None:
        if self._password_dialog_open:
            imgui.open_popup(S.PROJECT_PASSWORD_TITLE)
            self._password_dialog_open = False
        if not begin_dialog(S.PROJECT_PASSWORD_TITLE, width=420.0):
            return
        imgui.text_wrapped(S.PROJECT_PASSWORD_PROMPT)
        imgui.spacing()
        form = self._password_form
        submitted = form.render(
            S.PROJECT_INFO_PASSWORD,
            S.PROJECT_PASSWORD_REPEAT,
            S.PROJECT_PASSWORD_MISMATCH,
        )
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL, enabled=[form.valid, True])
        if (clicked == 0 or (submitted and form.valid)) and self._on_set_password:
            self._on_set_password(form.password)
            form.reset()
            imgui.close_current_popup()
        elif clicked == 1:
            form.reset()
            imgui.close_current_popup()
        end_dialog()

    @staticmethod
    def _artifact(size: int) -> str:
        """Presence + size for an embedded protection artifact (certificate/validation/master)."""
        if size <= 0:
            return S.PROJECT_INFO_ARTIFACT_NONE
        return S.PROJECT_INFO_ARTIFACT_PRESENT.format(size=size)
