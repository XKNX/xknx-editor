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

    @property
    def MENU_AREAS(self) -> str:
        return _("Floors and Areas…")

    @property
    def AREAS_TITLE(self) -> str:
        return _("Floors and Areas in Home Assistant")

    @property
    def AREAS_INTRO(self) -> str:
        return _(
            "The floors of the buildings become floors of Home Assistant, their rooms, "
            "corridors and stairways its areas. Chosen ones are created, linked to one of the "
            "same name, or renamed as here; nothing is removed."
        )

    @property
    def AREAS_NONE(self) -> str:
        return _("The project has no floors, rooms, corridors or stairways.")

    @property
    def AREAS_COLUMN_SPACE(self) -> str:
        return _("Building Part")

    @property
    def AREAS_COLUMN_KIND(self) -> str:
        return _("Becomes")

    @property
    def AREAS_COLUMN_STATE(self) -> str:
        return _("In Home Assistant")

    @property
    def AREAS_FLOOR(self) -> str:
        return _("Floor")

    @property
    def AREAS_AREA(self) -> str:
        return _("Area")

    @property
    def AREAS_NEW(self) -> str:
        return _("New")

    @property
    def AREAS_MATCH(self) -> str:
        return _('Exists as "{name}"')

    @property
    def AREAS_RENAMED(self) -> str:
        return _('Named "{name}"')

    @property
    def AREAS_LINKED(self) -> str:
        return _("Linked")

    @property
    def AREAS_APPLYING(self) -> str:
        return _("Applying…")

    @property
    def AREAS_FAILED(self) -> str:
        return _("Home Assistant did not take them: {error}")

    @property
    def AREAS_APPLIED(self) -> str:
        return _(
            "Floors and areas in Home Assistant created, linked or renamed: {count}"
        )

    @property
    def BTN_APPLY(self) -> str:
        return _("Apply")

    @property
    def MENU_ENTITIES(self) -> str:
        return _("Entities from Functions…")

    @property
    def ENTITIES_TITLE(self) -> str:
        return _("Entities in Home Assistant")

    @property
    def ENTITIES_INTRO(self) -> str:
        return _(
            "The building functions become entities of Home Assistant's KNX integration: a "
            "light, a switch, a cover or a climate entity with the group addresses of their "
            "roles, named like the function, in the area its room became (see Floors and "
            "Areas). Home Assistant checks each one first."
        )

    @property
    def ENTITIES_NONE(self) -> str:
        return _(
            "The project has no lights, switches, sun protection or heating among its "
            "functions."
        )

    @property
    def ENTITIES_COLUMN_FUNCTION(self) -> str:
        return _("Function")

    @property
    def ENTITIES_COLUMN_ADDRESSES(self) -> str:
        return _("Group Addresses")

    @property
    def ENTITIES_NO_AREA(self) -> str:
        return _("No area")

    @property
    def ENTITIES_CREATED(self) -> str:
        return _("Created: {entity}")

    @property
    def ENTITIES_EXISTING(self) -> str:
        return _("Its address is used by {entities}")

    @property
    def ENTITIES_CHECKING(self) -> str:
        return _("Being checked…")

    @property
    def ENTITIES_NOT_POSSIBLE(self) -> str:
        return _("Not taken: {error}")

    @property
    def ENTITIES_DONE(self) -> str:
        return _("Entities created in Home Assistant: {count}")

    @property
    def ENTITIES_CHANGED(self) -> str:
        return _("Its group addresses changed: {entity} gets the new ones")

    @property
    def ENTITIES_CHANGED_WAIT(self) -> str:
        return _("Its group addresses changed: download its devices first")

    @property
    def ENTITIES_ORPHANS(self) -> str:
        return _("Entities of Removed Functions")

    @property
    def DONE_UPDATED(self) -> str:
        return _("Entities updated in Home Assistant: {count}")

    @property
    def DONE_REMOVED(self) -> str:
        return _("Entities removed from Home Assistant: {count}")

    @property
    def MENU_TIME_SERVER(self) -> str:
        return _("Time Server…")

    @property
    def TIME_TITLE(self) -> str:
        return _("Time Server in Home Assistant")

    @property
    def TIME_INTRO(self) -> str:
        return _(
            "Home Assistant's KNX integration can send the time and the date to the bus. "
            "Choose the group addresses for them: those of the project with the data type of "
            "a time, a date, or a date with time."
        )

    @property
    def TIME_TIME(self) -> str:
        return _("Time")

    @property
    def TIME_DATE(self) -> str:
        return _("Date")

    @property
    def TIME_DATETIME(self) -> str:
        return _("Date and Time")

    @property
    def TIME_NONE(self) -> str:
        return _("None")

    @property
    def DONE_TIME_SERVER(self) -> str:
        return _("Home Assistant's time server sends to the chosen group addresses")

    @property
    def MENU_KEYS(self) -> str:
        return _("Send Keys to Home Assistant…")

    @property
    def KEYS_TITLE(self) -> str:
        return _("Keys in Home Assistant")

    @property
    def KEYS_INTRO(self) -> str:
        return _(
            "The keys of the project's Data Secure group addresses become the keyring of Home "
            "Assistant's KNX integration, so it reads and sends their telegrams. The KNX "
            "integration is loaded again for it."
        )

    @property
    def KEYS_COUNT(self) -> str:
        return _("Data Secure group addresses: {count}")

    @property
    def MENU_CONNECT_LIKE(self) -> str:
        return _("Connect Like Home Assistant")

    @property
    def CONNECT_LIKE_HINT(self) -> str:
        return _(
            "The editor connects to the bus over the same interface or router as Home "
            "Assistant's KNX integration (with a tunnel of its own). Without a connection of its "
            "own it reads and sends group telegrams over Home Assistant's."
        )

    @property
    def CONNECT_LIKE_UNAVAILABLE(self) -> str:
        return _(
            "Home Assistant's KNX integration connects with KNX Secure or finds its interface "
            "itself: connect the editor in the Gateway menu."
        )

    @property
    def KEYS_REFUSED(self) -> str:
        return _(
            "Home Assistant's KNX integration is not set up, or it connects with KNX Secure: "
            "then its keyring holds its own connection's keys, and stays as it is (change it "
            "in the KNX integration's options)."
        )

    @property
    def BTN_SEND(self) -> str:
        return _("Send")

    @property
    def DONE_KEYS(self) -> str:
        return _("Home Assistant's KNX integration has the project's keys")

    @property
    def FROM_HOME_ASSISTANT(self) -> str:
        return _("From Home Assistant")

    @property
    def SPACES_FROM_INTRO(self) -> str:
        return _(
            "Floors and areas of Home Assistant that the project has no floor or room for. "
            "They become floors and rooms of the building, linked to what they were made from."
        )

    @property
    def SPACES_BUILDING(self) -> str:
        return _("Building")

    @property
    def SPACES_NEW_BUILDING(self) -> str:
        return _("New building")

    @property
    def DEFAULT_BUILDING(self) -> str:
        return _("Home")

    @property
    def BTN_ADD_TO_PROJECT(self) -> str:
        return _("Add to Project")

    @property
    def UNDO_ADD_SPACES(self) -> str:
        return _("Floors and areas from Home Assistant")

    @property
    def SPACES_ADDED(self) -> str:
        return _("Floors and rooms added to the project: {count}")

    @property
    def MENU_ADDRESSES(self) -> str:
        return _("Group Addresses from Home Assistant…")

    @property
    def ADDRESSES_TITLE(self) -> str:
        return _("Group Addresses from Home Assistant")

    @property
    def ADDRESSES_INTRO(self) -> str:
        return _(
            "The group addresses Home Assistant's KNX integration uses that the project does not "
            "have. They are added with the name of what uses them and the datapoint type Home "
            "Assistant uses them with."
        )

    @property
    def ADDRESSES_NONE(self) -> str:
        return _("Home Assistant uses no group addresses the project does not have.")

    @property
    def COLUMN_ADDRESS(self) -> str:
        return _("Address")

    @property
    def COLUMN_DATAPOINT_TYPE(self) -> str:
        return _("Datapoint Type")

    @property
    def COLUMN_USED_BY(self) -> str:
        return _("Used By")

    @property
    def UNDO_ADD_GROUP_ADDRESSES(self) -> str:
        return _("Group addresses from Home Assistant")

    @property
    def ADDRESSES_ADDED(self) -> str:
        return _("Group addresses added to the project: {count}")


S = HomeAssistantStrings()
