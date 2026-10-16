from pathlib import Path

from editor_gui.strings import BaseStrings, create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("logger", _locale_dir)


class LoggerStrings(BaseStrings):
    @property
    def PANEL_LOGGER(self) -> str:
        return _("Logs")

    @property
    def FILTER_PLACEHOLDER(self) -> str:
        return _("Filter…")

    @property
    def COL_TIME(self) -> str:
        return _("Time")

    @property
    def COL_LEVEL(self) -> str:
        return _("Level")

    @property
    def COL_MESSAGE(self) -> str:
        return _("Message")

    @property
    def COPY_LOG(self) -> str:
        return _("Copy Log")


S = LoggerStrings()
