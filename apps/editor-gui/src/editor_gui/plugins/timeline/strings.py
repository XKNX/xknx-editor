"""Timeline plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("timeline", _locale_dir)


class TimelineStrings:
    @property
    def PANEL_TIMELINE(self) -> str:
        return _("Timeline")

    @property
    def TIMELINE_EMPTY(self) -> str:
        return _("No project open")

    @property
    def TIMELINE_SEARCH(self) -> str:
        return _("Filter group addresses…")

    @property
    def TIMELINE_NO_PINS(self) -> str:
        return _("Tick group addresses on the left to plot their values over time.")

    @property
    def TIMELINE_NO_DATA(self) -> str:
        return _("No numeric values captured yet for the selected addresses.")

    @property
    def TIMELINE_WINDOW(self) -> str:
        return _("Window")

    @property
    def TIMELINE_WINDOW_ALL(self) -> str:
        return _("All")


S = TimelineStrings()
