"""Shared widget strings (decoupled from any single plugin)."""

from pathlib import Path

from editor_gui.strings import BaseStrings, create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("widgets", _locale_dir)


class WidgetStrings(BaseStrings):
    @property
    def NODE_IMAGE_PLACEHOLDER(self) -> str:
        return _("(image)")

    @property
    def SEARCH_HINT(self) -> str:
        return _("Search…")

    @property
    def TOOLTIP_LOCKED(self) -> str:
        return _("{name} (locked)")

    @property
    def GROUP_OBJECTS_AUTO_CREATE(self) -> str:
        return _("Create Group Addresses ({count})")

    @property
    def GROUP_OBJECTS_SELECT_ALL(self) -> str:
        return _("All")

    @property
    def GROUP_OBJECTS_SELECT_NONE(self) -> str:
        return _("None")

    @property
    def GROUP_OBJECTS_COPY_LINKS(self) -> str:
        return _("Copy Links ({count})")

    @property
    def GROUP_OBJECTS_PASTE_LINKS(self) -> str:
        return _("Paste Links ({count})")

    @property
    def GROUP_OBJECTS_PASTE_LINKS_HINT(self) -> str:
        return _(
            "Assigns the copied links and flags to the checked objects in order (1st to "
            "1st, 2nd to 2nd, …), replacing each target's current links."
        )

    @property
    def GROUP_OBJECTS_PASTE_MERGE(self) -> str:
        return _("Merge")

    @property
    def GROUP_OBJECTS_PASTE_MERGE_HINT(self) -> str:
        return _("Keep each target's existing links and only add the copied ones.")

    @property
    def GROUP_OBJECTS_CTX_COPY(self) -> str:
        return _("Copy Links")

    @property
    def GROUP_OBJECTS_DPT_DEFAULT(self) -> str:
        return _("Default (inherit)")

    @property
    def GROUP_OBJECTS_CTX_EDIT_DESCRIPTION(self) -> str:
        return _("Edit description…")

    @property
    def GROUP_OBJECTS_EDIT_DESCRIPTION(self) -> str:
        return _("Edit Description")

    @property
    def OK(self) -> str:
        return _("OK")

    @property
    def CANCEL(self) -> str:
        return _("Cancel")

    @property
    def GROUP_OBJECTS_CTX_PASTE(self) -> str:
        return _("Paste Links")

    @property
    def GROUP_OBJECTS_CTX_COPY_TEXT(self) -> str:
        return _("Copy Addresses as Text")

    @property
    def GROUP_OBJECTS_BATCH_TITLE(self) -> str:
        return _("Create Group Addresses")

    @property
    def GROUP_OBJECTS_BATCH_HINT(self) -> str:
        return _(
            "Name template placeholders: {object}, {device}, {room}, {function}, "
            "{number}, {dpt}, {n} (index; {n:02} zero-pads)"
        )

    @property
    def GROUP_OBJECTS_BATCH_NAME(self) -> str:
        return _("Name Template")

    @property
    def GROUP_OBJECTS_BATCH_START(self) -> str:
        return _("Start Address")

    @property
    def GROUP_OBJECTS_BATCH_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def GA_SENDING(self) -> str:
        return _("Sending Group Address")

    @property
    def GA_RECEIVING(self) -> str:
        return _("Receiving Group Address")

    @property
    def GA_LINK_TITLE(self) -> str:
        return _("Link Group Address")

    @property
    def GA_CREATE_NEW(self) -> str:
        return _("New Group Address")

    @property
    def GA_CREATE_ADDR_HINT(self) -> str:
        return _("Address")

    @property
    def GA_CREATE_NAME_HINT(self) -> str:
        return _("Name")

    @property
    def GA_CREATE_BUTTON(self) -> str:
        return _("Create & Link")

    @property
    def PARAM_CHANGED_TOOLTIP(self) -> str:
        return _("Changed from default ({default}) — right-click to reset")

    @property
    def PARAM_RESET_DEFAULT(self) -> str:
        return _("Reset to Default")

    @property
    def PARAM_DIFFERS(self) -> str:
        return _("<differs>")

    @property
    def PARAM_OFF(self) -> str:
        return _("Off")

    @property
    def PARAM_ON(self) -> str:
        return _("On")

    @property
    def COLOR_STANDARD(self) -> str:
        return _("Standard")

    @property
    def COLOR_ADVANCED(self) -> str:
        return _("Advanced")

    @property
    def COLOR_THEME_COLORS(self) -> str:
        return _("Theme Colors")

    @property
    def COLOR_STANDARD_COLORS(self) -> str:
        return _("Standard Colors")

    @property
    def CALENDAR_WEEKDAYS(self) -> str:
        """Abbreviated weekday names, Monday first, separated by spaces."""
        return _("Mo Tu We Th Fr Sa Su")

    @property
    def PAGE_GENERAL(self) -> str:
        """Page for parameters that belong to no channel or parameter page."""
        return _("General")

    def time_unit(self, unit: str) -> str:
        """Name of a time parameter's unit shown next to its value."""
        names = {
            "Hours": _("Hours"),
            "Minutes": _("Minutes"),
            "Seconds": _("Seconds"),
            "HundredMilliseconds": _("x 100 ms"),
            "TenMilliseconds": _("x 10 ms"),
            "Milliseconds": _("Milliseconds"),
        }
        return names.get(unit, unit)

    @property
    def DPT_VALUE_NOT_A_NUMBER(self) -> str:
        return _("Enter a number.")

    @property
    def DPT_VALUE_WHOLE_NUMBER(self) -> str:
        return _("Enter a whole number.")

    @property
    def DPT_VALUE_RANGE(self) -> str:
        return _("Enter a value from {minimum} to {maximum}.")

    @property
    def DPT_VALUE_TOO_LONG(self) -> str:
        return _("Enter at most {count} characters.")

    @property
    def DPT_VALUE_REQUIRED(self) -> str:
        return _("Enter a value.")

    @property
    def DPT_VALUE_INVALID(self) -> str:
        return _("This value cannot be sent with this data type.")

    @property
    def DPT_VALUE_UNSUPPORTED(self) -> str:
        return _("No value can be entered for this data type.")

    @property
    def DPT_VALUE_EDIT(self) -> str:
        return _("Edit…")

    @property
    def DPT_VALUE_TOO_LONG_BYTES(self) -> str:
        return _("The text may take at most {count} bytes.")


S = WidgetStrings()
