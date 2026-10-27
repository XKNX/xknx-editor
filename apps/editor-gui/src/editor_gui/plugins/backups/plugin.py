"""Versions and backups of the open project: the "Versions and Backups" submenu of the File menu,
the window that lists a project's backups, and the settings.

A backup is made, restored and checked in the background (see ``service``); the window shows
how far one being made is, and a toast tells when it is done."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.plugins.backups.service import BackupJob, BackupService
from editor_gui.plugins.backups.settings import (
    EXPIRE_DAYS,
    INTERVAL_HOURS,
    KEEP_NEWEST,
    BackupSettings,
)
from editor_gui.plugins.backups.store import Backup, BackupError
from editor_gui.plugins.backups.strings import S
from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.strings import S as app_strings
from editor_gui.widgets.checkbox import checkbox_text_indent, wrapped_checkbox
from editor_gui.widgets.dialog import (
    begin_dialog,
    dialog_buttons,
    end_dialog,
    focus_when_appearing,
)
from editor_gui.widgets.dpi import px
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.tables import begin_data_table, data_table_headers

_ERR_COLOR = imgui.ImVec4(0.9, 0.4, 0.4, 1.0)
# Where a number field starts beside its label in the settings (unscaled pixels).
_FIELD_X = 210.0


def when(moment: datetime) -> str:
    """A backup's time, in local time."""
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def size_text(size: int) -> str:
    return S.SIZE_MB.format(size=f"{size / 1_000_000:.1f}")


def _comment_line(backup: Backup) -> None:
    """The comment given to ``backup``, below the question about it."""
    if backup.info.comment:
        imgui.spacing()
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(S.COMMENT_LINE.format(comment=backup.info.comment))
        imgui.pop_text_wrap_pos()


def _button_rows(labels: list[str]) -> int:
    """How many rows buttons with ``labels`` take in a row that goes on in the next line where
    the window is too narrow (see ``same_line_if_fits``)."""
    room = imgui.get_content_region_avail().x
    gap = imgui.get_style().item_spacing.x
    rows, used = 1, 0.0
    for label in labels:
        width = button_width(label)
        if used and used + gap + width > room:
            rows, used = rows + 1, width
        else:
            used += (gap if used else 0.0) + width
    return rows


class BackupsPlugin:
    name = "backups"

    def __init__(
        self,
        api: PluginAPI,
        *,
        open_folder: Callable[[Path], None] | None = None,
        open_project: Callable[[str], None] | None = None,
        compare: Callable[[str, str, str | None], None] | None = None,
    ) -> None:
        """``open_folder`` shows a folder in the file manager, ``open_project`` opens a project
        file, ``compare`` shows Project Diff with a file (path, its name, its password)."""
        self._api = api
        self._log = Logger(api.log, "backups")
        self._service = BackupService(api.project, api.catalog.io_lock, self._log)
        self._open_folder = open_folder
        self._open_project = open_project
        self._compare = compare
        # The list of backups asked for (a dialog), and asked to close (for Project Diff).
        self._list_requested = False
        self._close_list = False
        # What a restore, export or check started from the list is doing (shown there).
        self._busy = ""
        self._selected: Path | None = None
        # The dialog to open on the next frame.
        self._request: str | None = None
        self._comment = ""
        # The backup a dialog is about, and the password asked for to restore it.
        self._target: Backup | None = None
        self._password = ""
        self._password_error = ""
        # The settings being edited in the settings dialog.
        self._draft = BackupSettings()
        self._folder_dialog: file_dialogs.SelectFolderDialog | None = None
        self._export_dialog: file_dialogs.SaveFileDialog | None = None
        # A project that could not be opened, why, and its newest backup.
        self._damaged: tuple[Path, str, Backup] | None = None
        # A message to show in a dialog (the outcome of a check).
        self._message = ""

    @property
    def service(self) -> BackupService:
        return self._service

    @property
    def panels(self) -> list[PanelDefinition]:
        return []

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def shutdown(self, timeout: float = 600.0) -> None:
        """Wait for the backups still being written (the one of a project closed at exit)."""
        if not self._service.wait_idle(timeout):
            self._log.warning("backups still being written at exit")

    # --- menu ---------------------------------------------------------------

    def render_file_menu(self) -> None:
        """The "Versions and Backups" submenu of the File menu."""
        if not imgui.begin_menu(S.MENU):
            return
        is_open = self._api.project.is_open
        if imgui.menu_item(S.MENU_CREATE_BACKUP, "", False, is_open)[0]:
            self.ask_create()
        if imgui.menu_item(S.MENU_SHOW, "", False)[0]:
            self.show_window()
        imgui.separator()
        if imgui.menu_item(S.MENU_SETTINGS, "", False)[0]:
            self.ask_settings()
        imgui.end_menu()

    def ask_create(self) -> None:
        self._comment = ""
        self._request = "create"

    def ask_settings(self) -> None:
        self._draft = self._service.settings
        self._request = "settings"

    def show_window(self) -> None:
        """Show the list of the open project's backups (a dialog)."""
        self._list_requested = True

    # --- a project file that cannot be opened ----------------------------------

    def offer_restore(self, path: str, error: str) -> None:
        """Offer the newest backup of the project file ``path`` that could not be opened (or no
        longer exists, ``error`` empty), once it is found. Any thread."""
        # The first line, without the statement SQLite was running.
        reason = error.split("\n")[0].split(" [SQL")[0].strip()

        def look() -> None:
            found = self._service.backups_of_file(Path(path))
            if not found:
                return

            def offer() -> None:
                self._damaged = (Path(path), reason, found[0])
                self._request = "damaged"

            self._on_ui(offer)

        threading.Thread(target=look, name="backup-lookup", daemon=True).start()

    # --- every frame ------------------------------------------------------------

    def render_window(self) -> None:
        """The list of backups, the dialogs and the outcome of finished backups; called every
        frame. A dialog asked for while the list is shown opens on top of it."""
        self._service.tick()
        self._report_finished()
        self._poll_dialogs()
        title = f"{S.WINDOW_TITLE}###backups_list"
        if self._list_requested:
            imgui.open_popup(title)
            self._list_requested = False
        if not begin_dialog(title, width=760.0, height=460.0):
            self._render_dialogs()
            return
        self._render_contents()
        clicked = dialog_buttons(app_strings.BTN_CLOSE)
        if clicked is not None or self._close_list:
            self._close_list = False
            imgui.close_current_popup()
        self._render_dialogs()
        end_dialog()

    @property
    def list_shown(self) -> bool:
        """Whether the list of backups is shown (for tests)."""
        return imgui.is_popup_open(f"{S.WINDOW_TITLE}###backups_list")

    def _report_finished(self) -> None:
        for job in self._service.take_finished():
            error = job.future.exception()
            if error is not None:
                self._warn(S.FAILED.format(error=error))
                continue
            backup = job.future.result()
            if backup is not None and job.kind == "manual":
                self._notify(S.CREATED.format(name=backup.path.name))

    def _notify(self, text: str) -> None:
        if self._api.notify is not None:
            self._api.notify(text)

    def _warn(self, text: str) -> None:
        if self._api.warn is not None:
            self._api.warn(text)

    def _on_ui(self, fn: Callable[[], None]) -> None:
        if self._api.main_thread is not None:
            self._api.main_thread.submit(fn)
        else:
            fn()

    def _background(self, label: str, fn: Callable[[], None]) -> None:
        """Run ``fn`` behind the app's progress dialog (no list of backups shown)."""
        if self._api.run_background is not None:
            self._api.run_background(label, fn)
        else:
            fn()

    def _in_list(self, label: str, fn: Callable[[], None]) -> None:
        """Run ``fn`` on a worker thread while the list of backups shows ``label`` with a
        progress bar (the app's progress dialog would close the list); without a UI at once."""
        if self._api.main_thread is None:
            fn()
            return
        self._busy = label

        def run() -> None:
            try:
                fn()
            except Exception as e:
                self._log.error("backup task failed", error=f"{type(e).__name__}: {e}")
            finally:
                self._busy = ""

        threading.Thread(target=run, name="backup-task", daemon=True).start()

    # --- the window -------------------------------------------------------------

    def _render_contents(self) -> None:
        if not self._api.project.is_open:
            imgui.text_disabled(S.NO_PROJECT)
            return
        backups = self._service.backups()
        job = self._service.current
        imgui.begin_disabled(job is not None)
        if imgui.button(S.BTN_CREATE):
            self.ask_create()
        imgui.end_disabled()
        same_line_if_fits(button_width(S.BTN_OPEN_FOLDER))
        if imgui.button(S.BTN_OPEN_FOLDER) and self._open_folder is not None:
            self._open_folder(self._service.folder())
        if job is not None:
            self._render_progress(job)
        if self._busy:
            imgui.progress_bar(-1.0 * imgui.get_time(), imgui.ImVec2(-1, 0), self._busy)
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(S.FOLDER.format(path=self._service.folder()))
        imgui.pop_text_wrap_pos()
        if not backups:
            imgui.spacing()
            imgui.text_disabled(S.NO_BACKUPS)
            return
        selected = next((b for b in backups if b.path == self._selected), None)
        actions = self._actions(selected)
        rows = _button_rows([label for label, _tip, _action in actions])
        style = imgui.get_style()
        frame, spacing = imgui.get_frame_height(), style.item_spacing.y
        # The rows of buttons below the table, and the dialog's own row of buttons below them.
        reserved = rows * frame + (rows + 1) * spacing
        reserved += 4 * spacing + style.separator_size + frame
        height = max(imgui.get_content_region_avail().y - reserved, px(80))
        self._render_table(backups, height)
        self._render_actions(None if self._busy else selected, actions)

    def _render_progress(self, job: BackupJob) -> None:
        fraction = job.done / job.total if job.total else -1.0 * imgui.get_time()
        imgui.progress_bar(
            fraction, imgui.ImVec2(-1, 0), S.STEP.get(job.step, S.STEP["copy"])
        )

    def _render_table(self, backups: list[Backup], height: float) -> None:
        flags = (
            imgui.TableFlags_.borders_inner_v
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
        )
        if not begin_data_table("##backups", 6, flags, imgui.ImVec2(0.0, height)):
            return
        imgui.table_setup_scroll_freeze(0, 1)
        for title in (
            S.COL_CREATED,
            S.COL_KIND,
            S.COL_COMMENT,
            S.COL_COMPUTER,
            S.COL_USER,
            S.COL_SIZE,
        ):
            imgui.table_setup_column(title)
        data_table_headers()
        for backup in backups:
            info = backup.info
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            chosen = backup.path == self._selected
            if imgui.selectable(
                f"{when(info.created)}##{backup.path.name}",
                chosen,
                imgui.SelectableFlags_.span_all_columns
                | imgui.SelectableFlags_.allow_overlap,
            )[0]:
                self._selected = backup.path
            if imgui.is_item_hovered() and imgui.is_mouse_double_clicked(0):
                self._selected = backup.path
                self._ask_restore(backup)
            kind = S.KIND.get(info.kind, info.kind)
            cells = (
                f"{kind}, {S.PINNED}" if backup.pinned else kind,
                info.comment,
                info.machine,
                info.user,
                size_text(backup.size),
            )
            for column, text in enumerate(cells, start=1):
                imgui.table_set_column_index(column)
                imgui.text(text)
        imgui.end_table()

    def _actions(
        self, backup: Backup | None
    ) -> list[tuple[str, str, Callable[[Backup], None]]]:
        """The buttons for the chosen backup: (label, tooltip, what it does)."""
        pinned = backup is not None and backup.pinned
        return [
            (S.BTN_COMPARE, S.TIP_COMPARE, self._compare_with),
            (S.BTN_RESTORE, "", self._ask_restore),
            (S.BTN_EXPORT, "", self._ask_export),
            (
                S.BTN_UNPIN if pinned else S.BTN_PIN,
                S.TIP_PIN,
                lambda b: self._pin(b, not b.pinned),
            ),
            (S.BTN_CHECK, S.TIP_CHECK, self._check),
            (S.BTN_DELETE, "", self._ask_delete),
        ]

    def _render_actions(
        self,
        backup: Backup | None,
        actions: list[tuple[str, str, Callable[[Backup], None]]],
    ) -> None:
        """The buttons in as many rows as the window's width needs."""
        imgui.begin_disabled(backup is None)
        for index, (label, tip, action) in enumerate(actions):
            if index:
                same_line_if_fits(button_width(label))
            if imgui.button(label) and backup is not None:
                action(backup)
            if tip and imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
                imgui.set_tooltip(tip)
        imgui.end_disabled()

    def _ask_delete(self, backup: Backup) -> None:
        self._target = backup
        self._request = "delete"

    # --- actions ------------------------------------------------------------------

    def _compare_with(self, backup: Backup) -> None:
        if self._compare is None:
            return
        name = S.COMPARE_NAME.format(created=when(backup.info.created))
        self._compare(str(backup.path), name, self._api.project.project_password)
        # Project Diff shows in the Tools panel, behind the list.
        self._close_list = True

    def _ask_restore(self, backup: Backup) -> None:
        self._target = backup
        self._request = "restore"

    def _restore(self, backup: Backup, password: str | None) -> None:
        from xknxeditor.proj import InvalidProjectPassword, ProjectPasswordRequired

        def work() -> None:
            try:
                self._service.restore(backup, password)
            except (ProjectPasswordRequired, InvalidProjectPassword):
                self._on_ui(lambda: self._ask_password(backup, password is not None))
                return
            except (BackupError, OSError, ValueError) as e:
                text = S.RESTORE_FAILED.format(error=e)
                self._on_ui(lambda: self._warn(text))
                return
            text = S.RESTORED.format(created=when(backup.info.created))
            self._on_ui(lambda: self._notify(text))

        self._in_list(S.PROGRESS_RESTORE, work)

    def _ask_password(self, backup: Backup, wrong: bool) -> None:
        self._target = backup
        self._password = ""
        self._password_error = S.WRONG_PASSWORD if wrong else ""
        self._request = "password"

    def _ask_export(self, backup: Backup) -> None:
        self._target = backup
        name = f"{backup.path.stem}.xknx"
        self._export_dialog = file_dialogs.save_file(
            S.EXPORT_TITLE, name, [S.EXPORT_FILTER, "*.xknx"]
        )

    def _export(self, backup: Backup, dest: Path) -> None:
        def work() -> None:
            try:
                self._service.export(backup, dest)
            except (BackupError, OSError) as e:
                text = S.FAILED.format(error=e)
                self._on_ui(lambda: self._warn(text))
                return
            text = S.EXPORTED.format(name=dest.name)
            self._on_ui(lambda: self._notify(text))

        self._in_list(S.PROGRESS_EXPORT, work)

    def _pin(self, backup: Backup, pinned: bool) -> None:
        try:
            self._service.set_pinned(backup, pinned)
        except BackupError as e:
            self._warn(S.FAILED.format(error=e))

    def _check(self, backup: Backup) -> None:
        def work() -> None:
            problem = self._service.check(backup)
            text = (
                S.CHECK_OK if problem is None else S.CHECK_FAILED.format(error=problem)
            )

            def show() -> None:
                self._message = text
                self._request = "message"

            self._on_ui(show)

        self._in_list(S.PROGRESS_CHECK, work)

    def _restore_damaged(self, path: Path, backup: Backup) -> None:
        def work() -> None:
            try:
                kept = self._service.restore_file(backup, path)
            except (BackupError, OSError) as e:
                text = S.RESTORE_FAILED.format(error=e)
                self._on_ui(lambda: self._warn(text))
                return

            def reopen() -> None:
                if kept is not None:
                    self._notify(S.DAMAGED_KEPT.format(name=kept.name))
                if self._open_project is not None:
                    self._open_project(str(path))

            self._on_ui(reopen)

        self._background(S.PROGRESS_RESTORE, work)

    def _poll_dialogs(self) -> None:
        if self._folder_dialog is not None and self._folder_dialog.ready():
            folder = self._folder_dialog.result()
            self._folder_dialog = None
            if folder:
                self._draft = replace(self._draft, folder=folder)
        if self._export_dialog is not None and self._export_dialog.ready():
            dest = self._export_dialog.result()
            self._export_dialog = None
            backup, self._target = self._target, None
            if dest and backup is not None:
                path = Path(dest)
                self._export(backup, path if path.suffix else path.with_suffix(".xknx"))

    # --- dialogs ----------------------------------------------------------------

    def _render_dialogs(self) -> None:
        titles = {
            "create": f"{S.CREATE_TITLE}###backup_create",
            "restore": f"{S.RESTORE_TITLE}###backup_restore",
            "password": f"{S.RESTORE_TITLE}###backup_password",
            "delete": f"{S.DELETE_TITLE}###backup_delete",
            "settings": f"{S.SETTINGS_TITLE}###backup_settings",
            "damaged": f"{S.DAMAGED_TITLE}###backup_damaged",
            "message": f"{S.WINDOW_TITLE}###backup_message",
        }
        # The offer for a project that could not be opened waits for the progress of the open
        # (another dialog) to end.
        waits = self._request == "damaged" and imgui.is_popup_open(
            "", imgui.PopupFlags_.any_popup_id
        )
        if self._request is not None and not waits:
            imgui.open_popup(titles[self._request])
            self._request = None
        self._render_create(titles["create"])
        self._render_restore(titles["restore"])
        self._render_password(titles["password"])
        self._render_delete(titles["delete"])
        self._render_settings(titles["settings"])
        self._render_damaged(titles["damaged"])
        self._render_message(titles["message"])

    def _render_create(self, title: str) -> None:
        if not begin_dialog(title, width=440.0):
            return
        imgui.text_wrapped(S.CREATE_TEXT)
        imgui.spacing()
        imgui.text(S.CREATE_COMMENT)
        imgui.set_next_item_width(-1)
        focus_when_appearing()
        submitted, self._comment = imgui.input_text(
            "##backup_comment",
            self._comment,
            imgui.InputTextFlags_.enter_returns_true,
        )
        clicked = dialog_buttons(S.BTN_CREATE_OK, S.BTN_CANCEL)
        if clicked == 0 or submitted:
            imgui.close_current_popup()
            try:
                self._service.create(self._comment.strip())
            except BackupError as e:
                self._warn(S.FAILED.format(error=e))
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_restore(self, title: str) -> None:
        if not begin_dialog(title, width=460.0):
            return
        backup = self._target
        if backup is not None:
            imgui.text_wrapped(S.RESTORE_TEXT.format(created=when(backup.info.created)))
            _comment_line(backup)
        clicked = dialog_buttons(S.BTN_RESTORE_OK, S.BTN_CANCEL)
        if clicked == 0 and backup is not None:
            imgui.close_current_popup()
            self._restore(backup, None)
        elif clicked is not None:
            imgui.close_current_popup()
        end_dialog()

    def _render_password(self, title: str) -> None:
        if not begin_dialog(title, width=440.0):
            return
        imgui.text_wrapped(S.RESTORE_PASSWORD)
        imgui.set_next_item_width(-1)
        focus_when_appearing()
        submitted, self._password = imgui.input_text(
            "##backup_password",
            self._password,
            imgui.InputTextFlags_.password | imgui.InputTextFlags_.enter_returns_true,
        )
        if self._password_error:
            imgui.text_colored(_ERR_COLOR, self._password_error)
        clicked = dialog_buttons(
            S.BTN_RESTORE_OK, S.BTN_CANCEL, enabled=[bool(self._password), True]
        )
        backup = self._target
        if (clicked == 0 or submitted) and self._password and backup is not None:
            imgui.close_current_popup()
            password, self._password = self._password, ""
            self._restore(backup, password)
        elif clicked == 1:
            self._password = ""
            imgui.close_current_popup()
        end_dialog()

    def _render_delete(self, title: str) -> None:
        if not begin_dialog(title, width=420.0):
            return
        backup = self._target
        if backup is not None:
            imgui.text_wrapped(S.DELETE_TEXT.format(created=when(backup.info.created)))
            _comment_line(backup)
        clicked = dialog_buttons(S.BTN_DELETE_OK, S.BTN_CANCEL)
        if clicked == 0 and backup is not None:
            imgui.close_current_popup()
            try:
                self._service.delete(backup)
            except BackupError as e:
                self._warn(S.FAILED.format(error=e))
        elif clicked is not None:
            imgui.close_current_popup()
        end_dialog()

    def _render_damaged(self, title: str) -> None:
        if not begin_dialog(title, width=480.0):
            return
        damaged = self._damaged
        if damaged is not None:
            path, error, backup = damaged
            if error:
                imgui.text_wrapped(S.DAMAGED_TEXT.format(name=path.name, error=error))
            else:
                imgui.text_wrapped(S.MISSING_TEXT.format(name=path.name))
            imgui.spacing()
            imgui.text_wrapped(
                S.DAMAGED_OFFER.format(created=when(backup.info.created))
            )
        clicked = dialog_buttons(S.BTN_RESTORE_OK, S.BTN_CANCEL)
        if clicked == 0 and damaged is not None:
            imgui.close_current_popup()
            self._damaged = None
            self._restore_damaged(damaged[0], damaged[2])
        elif clicked is not None:
            self._damaged = None
            imgui.close_current_popup()
        end_dialog()

    def _render_message(self, title: str) -> None:
        if not begin_dialog(title, width=420.0):
            return
        imgui.text_wrapped(self._message)
        if dialog_buttons(S.BTN_OK) == 0:
            imgui.close_current_popup()
        end_dialog()

    def _render_settings(self, title: str) -> None:
        if not begin_dialog(title, width=520.0):
            return
        draft = self._draft.to_dict()
        imgui.separator_text(S.SECTION_BACKUPS)
        imgui.text_wrapped(S.BACKUPS_HINT)
        imgui.spacing()
        imgui.text(S.BACKUP_FOLDER)
        imgui.set_next_item_width(-1)
        shown = draft["folder"] or str(self._draft.root)
        changed, text = imgui.input_text("##backup_folder", shown)
        if changed:
            draft["folder"] = text
        if imgui.button(S.BTN_CHOOSE_FOLDER) and self._folder_dialog is None:
            self._folder_dialog = file_dialogs.select_folder(
                S.CHOOSE_FOLDER_TITLE, str(self._draft.root)
            )
        imgui.same_line()
        imgui.begin_disabled(not draft["folder"])
        if imgui.button(S.BTN_DEFAULT_FOLDER):
            draft["folder"] = ""
        imgui.end_disabled()
        imgui.spacing()
        draft["on_close"] = wrapped_checkbox(S.ON_CLOSE, draft["on_close"])[1]
        draft["interval"] = wrapped_checkbox(S.INTERVAL, draft["interval"])[1]
        imgui.begin_disabled(not draft["interval"])
        draft["interval_hours"] = self._number(
            S.INTERVAL_HOURS,
            "##interval_hours",
            draft["interval_hours"],
            INTERVAL_HOURS,
        )
        imgui.end_disabled()
        draft["expire"] = wrapped_checkbox(S.EXPIRE, draft["expire"])[1]
        imgui.begin_disabled(not draft["expire"])
        draft["expire_days"] = self._number(
            S.EXPIRE_DAYS, "##expire_days", draft["expire_days"], EXPIRE_DAYS
        )
        draft["keep_newest"] = self._number(
            S.KEEP_NEWEST, "##keep_newest", draft["keep_newest"], KEEP_NEWEST
        )
        imgui.end_disabled()
        imgui.spacing()
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(S.AUTOMATIC_HINT)
        imgui.pop_text_wrap_pos()
        self._draft = BackupSettings.from_dict(draft)
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        if clicked == 0:
            imgui.close_current_popup()
            self._service.set_settings(self._draft)
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    @staticmethod
    def _number(label: str, key: str, value: int, bounds: tuple[int, int]) -> int:
        """A number field beside its label (below it where the dialog is too narrow), kept
        within ``bounds``; the label starts where the text of the checkbox above does."""
        indent = checkbox_text_indent()
        imgui.indent(indent)
        imgui.align_text_to_frame_padding()
        imgui.text(label)
        style = imgui.get_style()
        # The field with its - and + buttons.
        field = px(110.0) + 2 * (imgui.get_frame_height() + style.item_inner_spacing.x)
        start = imgui.get_cursor_pos_x()
        beside = max(
            px(_FIELD_X), start + imgui.get_item_rect_size().x + style.item_spacing.x
        )
        if beside - start + field <= imgui.get_content_region_avail().x:
            imgui.same_line(beside)
        imgui.set_next_item_width(px(110.0))
        _, value = imgui.input_int(key, value)
        imgui.unindent(indent)
        return min(max(value, bounds[0]), bounds[1])
