"""Project information view: the project metadata carried over on import, with the project name and
password editable."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.password_form import NewPasswordForm

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import _ProjectInfo


class ProjectInfoPanel:
    def __init__(
        self,
        get_project_info: "Callable[[], _ProjectInfo | None]",
        on_rename: Callable[[str], None] | None = None,
        on_set_password: Callable[[str | None], None] | None = None,
        needs_password: Callable[[], bool] = lambda: False,
    ) -> None:
        self._get_project_info = get_project_info
        self._on_rename = on_rename
        self._on_set_password = on_set_password
        self._needs_password = needs_password
        self._name_buffer = ""
        self._name_editing = False
        self._password_dialog_open = False
        self._password_form = NewPasswordForm()

    def render(self) -> None:
        info = self._get_project_info()
        if info is None:
            imgui.text_disabled(S.PROJECT_INFO_EMPTY)
            return
        rows = [
            (S.PROJECT_INFO_INSTALLATION, info.installation_name),
            (S.PROJECT_INFO_GA_STYLE, info.group_address_style),
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
        flags = imgui.TableFlags_.borders_inner | imgui.TableFlags_.resizable
        if not imgui.begin_table("##project_info", 2, flags):
            return
        # Fixed, compact label column so the value column gets the rest — wide enough for a GUID.
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed, px(130.0))
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_stretch, 1.0)
        self._render_name(info)
        self._render_password(info)
        for label, value in rows:
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.text_disabled(label)
            imgui.table_set_column_index(1)
            imgui.text_wrapped(value or "-")
        imgui.end_table()
        self._render_password_dialog()

    def _render_name(self, info: "_ProjectInfo") -> None:
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

    def _render_password(self, info: "_ProjectInfo") -> None:
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
        imgui.same_line()
        label = (
            S.PROJECT_CHANGE_PASSWORD
            if info.password_protected
            else S.PROJECT_SET_PASSWORD
        )
        if imgui.button(label):
            self._password_form.reset()
            self._password_dialog_open = True
        if info.password_protected:
            imgui.same_line()
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
