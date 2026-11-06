"""User-facing strings for the Home Assistant plugin."""

from __future__ import annotations

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("home_assistant", _locale_dir)


class HomeAssistantStrings:
    """Strings of the Home Assistant menu, status and question."""

    @property
    def MENU(self) -> str:
        return _("Home Assistant")

    @property
    def MENU_USE_PROJECT(self) -> str:
        return _("Use Project in Home Assistant")

    @property
    def MENU_UPDATE_NOW(self) -> str:
        return _("Update Home Assistant Now")

    @property
    def MENU_AUTO_UPDATE(self) -> str:
        return _("Update Home Assistant Automatically")

    @property
    def AUTO_UPDATE_HINT(self) -> str:
        return _(
            "After each change, the project is sent to Home Assistant's KNX integration again."
        )

    @property
    def STATUS_UP_TO_DATE(self) -> str:
        return _("Home Assistant: up to date")

    @property
    def STATUS_PENDING(self) -> str:
        return _("Home Assistant: changes not sent")

    @property
    def STATUS_SENDING(self) -> str:
        return _("Home Assistant: updating…")

    @property
    def STATUS_NOT_LINKED(self) -> str:
        return _("Home Assistant: other project")

    @property
    def STATUS_KNX_MISSING(self) -> str:
        return _("Home Assistant: KNX integration not set up")

    @property
    def STATUS_FAILED(self) -> str:
        return _("Home Assistant: update failed")

    @property
    def STATUS_UNAVAILABLE(self) -> str:
        return _("Home Assistant: not connected")

    @property
    def DETAIL_UP_TO_DATE(self) -> str:
        return _(
            "Home Assistant's KNX integration uses this project: its group monitor, project "
            "view and address pickers know its group addresses, devices and buildings."
        )

    @property
    def DETAIL_PENDING_AUTO(self) -> str:
        return _("The changes are sent to Home Assistant in a moment.")

    @property
    def DETAIL_PENDING_MANUAL(self) -> str:
        return _(
            "Automatic updates are off: Update Home Assistant Now in the Home Assistant menu "
            "sends the changes."
        )

    @property
    def DETAIL_NO_PROJECT(self) -> str:
        return _("Home Assistant's KNX integration holds no project.")

    @property
    def DETAIL_OTHER_PROJECT(self) -> str:
        return _(
            'Home Assistant\'s KNX integration holds the project "{name}" ({tool}, {date}).'
        )

    @property
    def DETAIL_KNX_MISSING(self) -> str:
        return _(
            "Set up the KNX integration in Home Assistant to use this project there "
            "(Settings > Devices & Services)."
        )

    @property
    def DETAIL_FAILED(self) -> str:
        return _("Home Assistant did not take the project: {error}")

    @property
    def DETAIL_UNAVAILABLE(self) -> str:
        return _(
            "The XKNX Editor integration is not connected. Update the integration in Home "
            "Assistant if this stays."
        )

    @property
    def QUESTION_TITLE(self) -> str:
        return _("Use Project in Home Assistant")

    @property
    def QUESTION_NONE(self) -> str:
        return _(
            'Home Assistant\'s KNX integration holds no project yet. Use "{name}" as its project?'
        )

    @property
    def QUESTION_OTHER(self) -> str:
        return _(
            'Home Assistant\'s KNX integration holds the project "{other}" ({tool}, {date}). '
            'Replace it with "{name}"?'
        )

    @property
    def QUESTION_EFFECT(self) -> str:
        return _(
            "Its group monitor, project view, address pickers and entity suggestions then know "
            "this project's group addresses, devices and buildings. Entities, automations and "
            "the KNX integration's other settings stay as they are."
        )

    @property
    def QUESTION_AUTO(self) -> str:
        return _("Your changes reach Home Assistant a moment after you make them.")

    @property
    def QUESTION_MANUAL(self) -> str:
        return _(
            "Automatic updates are off: Update Home Assistant Now in the Home Assistant menu "
            "sends your changes."
        )

    @property
    def BTN_USE(self) -> str:
        return _("Use in Home Assistant")

    @property
    def BTN_REPLACE(self) -> str:
        return _("Replace")

    @property
    def BTN_NOT_NOW(self) -> str:
        return _("Not Now")

    @property
    def UNKNOWN_DATE(self) -> str:
        return _("date unknown")

    @property
    def MENU_LOAD_TELEGRAMS(self) -> str:
        return _("Load Telegrams from Home Assistant")

    @property
    def LOAD_TELEGRAMS_HINT(self) -> str:
        return _(
            "The telegrams Home Assistant recorded in the last {hours} hours, in the Group "
            "Monitor in place of its entries."
        )

    @property
    def HISTORY_LOADED(self) -> str:
        return _("{count} telegrams from Home Assistant in the Group Monitor")

    @property
    def HISTORY_FAILED(self) -> str:
        return _("The telegrams could not be loaded: {error}")

    @property
    def USAGE_UNKNOWN(self) -> str:
        return _("Shown once the XKNX Editor integration is connected.")

    @property
    def USAGE_NONE(self) -> str:
        return _("Not used in Home Assistant.")

    @property
    def USAGE_SENDS(self) -> str:
        return _("Home Assistant sends to this group address.")

    @property
    def COLUMN_NAME(self) -> str:
        return _("Name")

    @property
    def COLUMN_USE(self) -> str:
        return _("Used By")

    @property
    def COLUMN_STATE(self) -> str:
        return _("State")

    @property
    def USER_EXPOSE(self) -> str:
        return _("Expose")

    @property
    def USER_TIME_SERVER(self) -> str:
        return _("Time server")

    @property
    def USER_YAML(self) -> str:
        return _("{entity} (YAML)")

    @property
    def USER_DISABLED(self) -> str:
        return _("Disabled")


S = HomeAssistantStrings()
