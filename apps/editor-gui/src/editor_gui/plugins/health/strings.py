"""Health plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("health", _locale_dir)


class HealthStrings:
    @property
    def PANEL_HEALTH(self) -> str:
        return _("Health")

    @property
    def HEALTH_EMPTY(self) -> str:
        return _("No project open")

    @property
    def HEALTH_ALL_GOOD(self) -> str:
        return _("No issues found")

    @property
    def HEALTH_SUMMARY(self) -> str:
        return _("{errors} errors, {warnings} warnings")

    @property
    def HEALTH_NO_ADDRESS(self) -> str:
        return _("{device}: no individual address")

    @property
    def HEALTH_DUPLICATE_ADDRESS(self) -> str:
        return _("Duplicate individual address {address} ({device})")

    @property
    def HEALTH_SKIPPED(self) -> str:
        return _("{count} device(s) skipped: application not in catalog")

    @property
    def HEALTH_UNLINKED(self) -> str:
        return _("{device}: {count} unlinked group object(s)")

    @property
    def HEALTH_NO_DPT(self) -> str:
        return _("{address} {name}: no datapoint type")

    @property
    def HEALTH_NO_SENDER(self) -> str:
        return _("{address} {name}: no sending group object")

    @property
    def HEALTH_SENDERS(self) -> str:
        return _("{address} {name}: {count} sending group objects")


S = HealthStrings()
