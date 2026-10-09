"""Project information view: the project metadata carried over on import."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import _ProjectInfo


class ProjectInfoPanel:
    def __init__(self, get_project_info: "Callable[[], _ProjectInfo | None]") -> None:
        self._get_project_info = get_project_info

    def render(self) -> None:
        info = self._get_project_info()
        if info is None:
            imgui.text_disabled(S.PROJECT_INFO_EMPTY)
            return
        rows = [
            (S.PROJECT_INFO_NAME, info.name),
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
        for label, value in rows:
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.text_disabled(label)
            imgui.table_set_column_index(1)
            imgui.text_wrapped(value or "-")
        imgui.end_table()

    @staticmethod
    def _artifact(size: int) -> str:
        """Presence + size for an embedded protection artifact (certificate/validation/master)."""
        if size <= 0:
            return S.PROJECT_INFO_ARTIFACT_NONE
        return S.PROJECT_INFO_ARTIFACT_PRESENT.format(size=size)
