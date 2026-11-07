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

    def HEALTH_ERRORS(self, count: int) -> str:
        return _.plural("{count} error", "{count} errors", count)

    def HEALTH_WARNINGS(self, count: int) -> str:
        return _.plural("{count} warning", "{count} warnings", count)

    @property
    def HEALTH_NO_ADDRESS(self) -> str:
        return _("{device}: no individual address")

    @property
    def HEALTH_DUPLICATE_ADDRESS(self) -> str:
        return _("Duplicate individual address {address} ({device})")

    def HEALTH_SKIPPED(self, count: int) -> str:
        return _.plural(
            "{count} device skipped: application not in catalog",
            "{count} devices skipped: application not in catalog",
            count,
        )

    def HEALTH_UNLINKED(self, count: int) -> str:
        return _.plural(
            "{device}: {count} unlinked group object",
            "{device}: {count} unlinked group objects",
            count,
        )

    @property
    def HEALTH_NO_DPT(self) -> str:
        return _("{address} {name}: no datapoint type")

    @property
    def HEALTH_NO_SENDER(self) -> str:
        return _("{address} {name}: no sending group object")

    def HEALTH_SENDERS(self, count: int) -> str:
        return _.plural(
            "{address} {name}: {count} sending group object",
            "{address} {name}: {count} sending group objects",
            count,
        )


S = HealthStrings()
