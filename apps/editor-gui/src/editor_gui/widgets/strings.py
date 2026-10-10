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
        return _("Create group addresses ({count})")

    @property
    def GROUP_OBJECTS_SELECT_ALL(self) -> str:
        return _("All")

    @property
    def GROUP_OBJECTS_SELECT_NONE(self) -> str:
        return _("None")

    @property
    def GROUP_OBJECTS_COPY_LINKS(self) -> str:
        return _("Copy links ({count})")

    @property
    def GROUP_OBJECTS_PASTE_LINKS(self) -> str:
        return _("Paste links ({count})")

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
        return _("Copy links")

    @property
    def GROUP_OBJECTS_CTX_PASTE(self) -> str:
        return _("Paste links")

    @property
    def GROUP_OBJECTS_CTX_COPY_TEXT(self) -> str:
        return _("Copy addresses as text")

    @property
    def GROUP_OBJECTS_BATCH_TITLE(self) -> str:
        return _("Create group addresses")

    @property
    def GROUP_OBJECTS_BATCH_HINT(self) -> str:
        return _(
            "Name template placeholders: {object}, {device}, {room}, {function}, "
            "{number}, {dpt}, {n} (index; {n:02} zero-pads)"
        )

    @property
    def GROUP_OBJECTS_BATCH_NAME(self) -> str:
        return _("Name template")

    @property
    def GROUP_OBJECTS_BATCH_START(self) -> str:
        return _("Start address")

    @property
    def GROUP_OBJECTS_BATCH_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def GA_SENDING(self) -> str:
        return _("Sending group address")

    @property
    def GA_RECEIVING(self) -> str:
        return _("Receiving group address")

    @property
    def GA_LINK_TITLE(self) -> str:
        return _("Link group address")

    @property
    def GA_CREATE_NEW(self) -> str:
        return _("New group address")

    @property
    def GA_CREATE_ADDR_HINT(self) -> str:
        return _("Address")

    @property
    def GA_CREATE_NAME_HINT(self) -> str:
        return _("Name")

    @property
    def GA_CREATE_BUTTON(self) -> str:
        return _("Create & link")

    @property
    def PARAM_CHANGED_TOOLTIP(self) -> str:
        return _("Changed from default ({default}) — right-click to reset")

    @property
    def PARAM_RESET_DEFAULT(self) -> str:
        return _("Reset to default")

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
        return _("Theme colors")

    @property
    def COLOR_STANDARD_COLORS(self) -> str:
        return _("Standard colors")

    @property
    def CALENDAR_WEEKDAYS(self) -> str:
        """Abbreviated weekday names, Monday first, separated by spaces."""
        return _("Mo Tu We Th Fr Sa Su")

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


S = WidgetStrings()
