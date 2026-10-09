"""The recover window: scan range, results table, and add-to-project actions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui
from imgui_bundle import portable_file_dialogs as pfd

from editor_gui.plugins.recover.strings import S
from editor_gui.widgets.dpi import px
from xknxeditor.recover.recover import STAGE_GROUP_COMMUNICATION, STAGE_PARAMETERS

if TYPE_CHECKING:
    from editor_gui.plugins.recover.service import RecoverService


def _stage_label(stage: str) -> str:
    """Localized label for a recovery stage id (evaluated per frame for i18n)."""
    return {
        STAGE_GROUP_COMMUNICATION: S.STAGE_GROUP_COMMUNICATION,
        STAGE_PARAMETERS: S.STAGE_PARAMETERS,
    }.get(stage, "...")


class RecoverPanel:
    """Renders the recover window contents against a :class:`RecoverService`."""

    def __init__(self, service: RecoverService) -> None:
        self._service = service
        self._start = "1.1.1"
        self._end = "1.1.20"
        self._save_dialog: pfd.save_file | None = None
        self._snapshot_dialog: pfd.save_file | None = None
        self._focus_requested = False

    def request_focus(self) -> None:
        """Bring the docked recover panel to the front on the next frame."""
        self._focus_requested = True

    def render(self) -> None:
        service = self._service
        if self._focus_requested:
            imgui.set_window_focus()  # focus this docked window
            self._focus_requested = False
        service.poll()
        self._poll_save_dialog()

        if not service.connected():
            imgui.text_colored(imgui.ImVec4(0.9, 0.6, 0.2, 1.0), S.STATUS_NOT_CONNECTED)
            return

        self._render_scan_controls()
        if service.error:
            imgui.text_colored(imgui.ImVec4(0.9, 0.3, 0.3, 1.0), service.error)
        imgui.separator()
        self._render_status()
        self._render_results()
        self._render_actions()

    def _render_scan_controls(self) -> None:
        service = self._service
        busy = service.busy
        imgui.set_next_item_width(px(120))
        _, self._start = imgui.input_text(S.RANGE_START, self._start)
        imgui.same_line()
        imgui.set_next_item_width(px(120))
        _, self._end = imgui.input_text(S.RANGE_END, self._end)
        imgui.same_line()
        imgui.begin_disabled(busy)
        if imgui.button(S.BTN_SCAN):
            service.start_scan(self._start, self._end)
        imgui.end_disabled()
        # While a bus operation runs, offer Stop (cooperative cancel).
        if busy:
            imgui.same_line()
            if imgui.button(S.BTN_STOP):
                service.stop()
        imgui.same_line()
        _, service.auto_apply = imgui.checkbox(S.AUTO_APPLY, service.auto_apply)

    def _render_status(self) -> None:
        service = self._service
        if service.phase == "scanning":
            if service.scan_total:
                imgui.text_disabled(
                    S.STATUS_SCAN_PROGRESS
                    % {
                        "address": service.scan_current or "-",
                        "done": service.scan_done,
                        "total": service.scan_total,
                        "found": len(service.entries),
                    }
                )
            else:
                imgui.text_disabled(S.STATUS_SCANNING)
        elif service.phase == "recovering":
            if service.recover_total:
                imgui.text_disabled(
                    S.STATUS_RECOVER_PROGRESS
                    % {
                        "address": service.recover_current or "-",
                        "done": service.recover_done,
                        "total": service.recover_total,
                        "stage": _stage_label(service.recover_stage),
                    }
                )
            else:
                imgui.text_disabled(S.STATUS_RECOVERING)
        elif service.phase == "verifying":
            imgui.text_disabled(S.STATUS_VERIFYING)
        elif service.phase == "idle":
            imgui.text_disabled(S.STATUS_IDLE)
        if service.phase == "recovered":
            imgui.text_colored(
                imgui.ImVec4(0.6, 0.8, 1.0, 1.0),
                S.OVERVIEW_RECOVERED % service.recovery_totals(),
            )
            warnings = service.link_warnings()
            if warnings:
                imgui.text_colored(
                    imgui.ImVec4(0.9, 0.7, 0.3, 1.0),
                    S.OVERVIEW_WARNINGS % {"count": len(warnings)},
                )
        if service.fetch_status:
            imgui.text_disabled(S.STATUS_FETCHING % {"what": service.fetch_status})
        if service.apply_status:
            imgui.text_colored(imgui.ImVec4(0.4, 0.8, 0.4, 1.0), service.apply_status)

    def _render_results(self) -> None:
        service = self._service
        if not service.entries:
            return
        flags = imgui.TableFlags_.borders_inner | imgui.TableFlags_.sizing_stretch_prop
        if not imgui.begin_table("##recover_results", 6, flags):
            return
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed)
        for header in (
            S.COL_ADDRESS,
            S.COL_MASK,
            S.COL_PRODUCT,
            S.COL_STATUS,
            S.COL_DETAILS,
        ):
            imgui.table_setup_column(header)
        imgui.table_headers_row()
        for index, entry in enumerate(service.entries):
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.begin_disabled(not entry.recoverable or service.busy)
            _, entry.selected = imgui.checkbox(f"##sel{index}", entry.selected)
            imgui.end_disabled()
            imgui.table_set_column_index(1)
            imgui.text(entry.address)
            imgui.table_set_column_index(2)
            imgui.text_disabled(f"{entry.mask_version:#06x}")
            imgui.table_set_column_index(3)
            imgui.text(
                entry.product_name
                or (entry.app_id.manufacturer_id if entry.app_id else "-")
            )
            imgui.table_set_column_index(4)
            imgui.text_disabled(entry.state)
            imgui.table_set_column_index(5)
            imgui.text_disabled(service.entry_detail(entry))
        imgui.end_table()

    def _render_actions(self) -> None:
        service = self._service
        if service.phase not in ("scanned", "recovered"):
            return
        recoverable = any(e.selected and e.recoverable for e in service.entries)
        imgui.begin_disabled(service.busy or not recoverable)
        if imgui.button(S.BTN_RECOVER_SELECTED):
            service.start_recover()
        imgui.end_disabled()

        has_recovered = any(e.recovered is not None for e in service.entries)
        if not has_recovered:
            return
        recovered_count = sum(1 for e in service.entries if e.recovered is not None)
        imgui.begin_disabled(service.busy)
        if imgui.button(S.BTN_VERIFY):
            service.start_verify()
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button(S.BTN_EXPORT_SNAPSHOT):
            self._snapshot_dialog = pfd.save_file(
                S.BTN_EXPORT_SNAPSHOT, "recover-snapshot.json"
            )
        imgui.same_line()
        if imgui.button(f"{S.BTN_ADD_NEW} ({recovered_count})"):
            self._save_dialog = pfd.save_file(S.BTN_ADD_NEW, "recovered.xknx")
        imgui.same_line()
        imgui.begin_disabled(not service.has_project())
        if imgui.button(f"{S.BTN_ADD_MERGE} ({recovered_count})"):
            service.apply_to_open_project()
        imgui.end_disabled()
        if not service.has_project():
            imgui.text_disabled(S.STATUS_NO_PROJECT)

    def _poll_save_dialog(self) -> None:
        if self._save_dialog is not None and self._save_dialog.ready():
            path = self._save_dialog.result()
            self._save_dialog = None
            if path:
                target = Path(path)
                if target.suffix != ".xknx":
                    target = target.with_suffix(".xknx")
                self._service.create_project_and_apply(target)
        if self._snapshot_dialog is not None and self._snapshot_dialog.ready():
            path = self._snapshot_dialog.result()
            self._snapshot_dialog = None
            if path:
                target = Path(path)
                if target.suffix != ".json":
                    target = target.with_suffix(".json")
                target.write_text(self._service.snapshot_text(), encoding="utf-8")

    def has_results(self) -> bool:
        return bool(self._service.entries)
