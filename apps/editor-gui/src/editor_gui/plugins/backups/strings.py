"""Backup plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("backups", _locale_dir)


class BackupStrings:
    @property
    def MENU(self) -> str:
        return _("Backups")

    @property
    def MENU_CREATE_BACKUP(self) -> str:
        return _("Create Backup…")

    @property
    def MENU_SHOW(self) -> str:
        return _("Show Backups…")

    @property
    def COMMENT_LINE(self) -> str:
        return _("Comment: {comment}")

    @property
    def MENU_SETTINGS(self) -> str:
        return _("Settings…")

    @property
    def WINDOW_TITLE(self) -> str:
        return _("Backups")

    @property
    def NO_PROJECT(self) -> str:
        return _("Open a project to see its backups.")

    @property
    def NO_BACKUPS(self) -> str:
        return _("This project has no backups yet.")

    @property
    def FOLDER(self) -> str:
        return _("Folder: {path}")

    @property
    def BTN_CREATE(self) -> str:
        return _("Create Backup…")

    @property
    def BTN_OPEN_FOLDER(self) -> str:
        return _("Open Folder")

    @property
    def BTN_COMPARE(self) -> str:
        return _("Compare")

    @property
    def BTN_RESTORE(self) -> str:
        return _("Restore…")

    @property
    def BTN_EXPORT(self) -> str:
        return _("Export as .xknx…")

    @property
    def BTN_PIN(self) -> str:
        return _("Pin")

    @property
    def BTN_UNPIN(self) -> str:
        return _("Unpin")

    @property
    def BTN_CHECK(self) -> str:
        return _("Check")

    @property
    def BTN_DELETE(self) -> str:
        return _("Delete…")

    @property
    def TIP_COMPARE(self) -> str:
        return _("Show what differs between the project now and this backup")

    @property
    def TIP_PIN(self) -> str:
        return _("A pinned backup is never deleted automatically")

    @property
    def TIP_CHECK(self) -> str:
        return _("Check that the backup can be restored")

    @property
    def COL_CREATED(self) -> str:
        return _("Created")

    @property
    def COL_KIND(self) -> str:
        return _("Kind")

    @property
    def COL_COMMENT(self) -> str:
        return _("Comment")

    @property
    def COL_COMPUTER(self) -> str:
        return _("Computer")

    @property
    def COL_USER(self) -> str:
        return _("User")

    @property
    def COL_SIZE(self) -> str:
        return _("Size")

    @property
    def KIND(self) -> dict[str, str]:
        """The kinds of backups, by ``store.KIND_*``."""
        return {
            "manual": _("Manual"),
            "close": _("On close"),
            "interval": _("Interval"),
            "restore": _("Before restore"),
        }

    @property
    def PINNED(self) -> str:
        return _("Pinned")

    @property
    def SIZE_MB(self) -> str:
        return _("{size} MB")

    @property
    def STEP(self) -> dict[str, str]:
        """What a backup being made does now."""
        return {
            "copy": _("Creating a backup: copying the project…"),
            "check": _("Creating a backup: checking the project…"),
            "write": _("Creating a backup: compressing…"),
        }

    @property
    def CREATE_TITLE(self) -> str:
        return _("Create Backup")

    @property
    def CREATE_TEXT(self) -> str:
        return _("The backup holds the project as it is now.")

    @property
    def CREATE_COMMENT(self) -> str:
        return _("Comment")

    @property
    def BTN_CREATE_OK(self) -> str:
        return _("Create")

    @property
    def BTN_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def BTN_OK(self) -> str:
        return _("OK")

    @property
    def CREATED(self) -> str:
        return _("Backup created: {name}")

    @property
    def FAILED(self) -> str:
        return _("The backup failed: {error}")

    @property
    def RESTORE_TITLE(self) -> str:
        return _("Restore Backup")

    @property
    def RESTORE_TEXT(self) -> str:
        return _(
            "Restore the project to its state of {created}?\n\nThe project as it is now is "
            "saved as a backup first, so you can go back to it."
        )

    @property
    def BTN_RESTORE_OK(self) -> str:
        return _("Restore")

    @property
    def RESTORE_PASSWORD(self) -> str:
        return _(
            "The project had another password when this backup was made. Enter that password:"
        )

    @property
    def WRONG_PASSWORD(self) -> str:
        return _("Wrong password")

    @property
    def PROGRESS_RESTORE(self) -> str:
        return _("Restoring the backup…")

    @property
    def RESTORED(self) -> str:
        return _("Project restored to its state of {created}")

    @property
    def RESTORE_FAILED(self) -> str:
        return _("The backup could not be restored: {error}")

    @property
    def DELETE_TITLE(self) -> str:
        return _("Delete Backup")

    @property
    def DELETE_TEXT(self) -> str:
        return _("Delete the backup of {created}? This cannot be undone.")

    @property
    def BTN_DELETE_OK(self) -> str:
        return _("Delete")

    @property
    def EXPORT_TITLE(self) -> str:
        return _("Export Backup as Project File")

    @property
    def EXPORT_FILTER(self) -> str:
        return _("XKNX Editor projects")

    @property
    def PROGRESS_EXPORT(self) -> str:
        return _("Exporting the backup…")

    @property
    def EXPORTED(self) -> str:
        return _("Backup exported: {name}")

    @property
    def PROGRESS_CHECK(self) -> str:
        return _("Checking the backup…")

    @property
    def CHECK_OK(self) -> str:
        return _("The backup is intact and can be restored.")

    @property
    def CHECK_FAILED(self) -> str:
        return _("The backup is damaged: {error}")

    @property
    def COMPARE_NAME(self) -> str:
        return _("Backup of {created}")

    @property
    def DAMAGED_TITLE(self) -> str:
        return _("Restore from Backup")

    @property
    def DAMAGED_TEXT(self) -> str:
        return _("The project {name} could not be opened: {error}")

    @property
    def MISSING_TEXT(self) -> str:
        return _("The project file {name} no longer exists.")

    @property
    def DAMAGED_OFFER(self) -> str:
        return _(
            "Its newest backup is from {created}. Restore the project from it? A file that is "
            "there now is kept under another name."
        )

    @property
    def DAMAGED_KEPT(self) -> str:
        return _("The damaged file was kept as {name}.")

    @property
    def SETTINGS_TITLE(self) -> str:
        return _("Backup Settings")

    @property
    def BACKUPS_HINT(self) -> str:
        return _(
            "A backup is one file with the complete project, which can be restored on its "
            "own. Each project has a folder of its own in the backup folder. A network drive or a "
            "folder synchronized with a cloud service keeps the backups off this computer."
        )

    @property
    def BACKUP_FOLDER(self) -> str:
        return _("Backup Folder")

    @property
    def BTN_CHOOSE_FOLDER(self) -> str:
        return _("Choose Folder…")

    @property
    def BTN_DEFAULT_FOLDER(self) -> str:
        return _("Default")

    @property
    def CHOOSE_FOLDER_TITLE(self) -> str:
        return _("Choose the Backup Folder")

    @property
    def ON_CLOSE(self) -> str:
        return _("Create a backup when a project is closed")

    @property
    def INTERVAL(self) -> str:
        return _("Create backups at regular intervals while a project is open")

    @property
    def INTERVAL_HOURS(self) -> str:
        return _("Interval in Hours")

    @property
    def EXPIRE(self) -> str:
        return _("Delete old automatic backups")

    @property
    def EXPIRE_DAYS(self) -> str:
        return _("Delete After (Days)")

    @property
    def KEEP_NEWEST(self) -> str:
        return _("Always Keep the Newest")

    @property
    def AUTOMATIC_HINT(self) -> str:
        return _(
            "An automatic backup is only made when the project changed since its newest "
            "backup. Backups you create yourself and pinned backups are never deleted "
            "automatically."
        )


S = BackupStrings()
