"""The backup settings: where backups go, when they are made and how long they are kept. They
apply to every project and are kept in ``backups.json`` in the app's data folder."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

from editor_gui.settings import config_dir, load_settings, save_settings

_NAME = "backups"

# The bounds of the numbers, so a hand-edited file cannot make a backup every second.
INTERVAL_HOURS = (1, 24 * 365)
EXPIRE_DAYS = (1, 365 * 100)
KEEP_NEWEST = (0, 1000)


def default_folder() -> Path:
    return config_dir() / "backups"


def _clamp(value: int, bounds: tuple[int, int]) -> int:
    return min(max(value, bounds[0]), bounds[1])


@dataclass(frozen=True)
class BackupSettings:
    """``folder`` empty: the default folder. Automatic backups are made when a project is
    closed (``on_close``) and every ``interval_hours`` while it changed (``interval``); with
    ``expire`` the automatic ones older than ``expire_days`` days are deleted, all but the newest
    ``keep_newest``."""

    folder: str = ""
    on_close: bool = False
    interval: bool = False
    interval_hours: int = 24
    expire: bool = True
    expire_days: int = 90
    keep_newest: int = 3

    @property
    def root(self) -> Path:
        return Path(self.folder) if self.folder else default_folder()

    def clamped(self) -> BackupSettings:
        """The settings with their numbers within bounds and the folder without surrounding
        spaces (see :func:`save`; a folder being typed keeps them)."""
        return BackupSettings(
            folder=self.folder,
            on_close=self.on_close,
            interval=self.interval,
            interval_hours=_clamp(self.interval_hours, INTERVAL_HOURS),
            expire=self.expire,
            expire_days=_clamp(self.expire_days, EXPIRE_DAYS),
            keep_newest=_clamp(self.keep_newest, KEEP_NEWEST),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> BackupSettings:
        """The settings in ``data``; a missing or mistyped value keeps its default."""
        defaults = BackupSettings()
        values: dict[str, Any] = {}
        for f in fields(BackupSettings):
            value, default = data.get(f.name), getattr(defaults, f.name)
            same_type = type(value) is type(default)
            values[f.name] = value if same_type else default
        return BackupSettings(**values).clamped()


def load() -> BackupSettings:
    return BackupSettings.from_dict(load_settings(_NAME))


def save(settings: BackupSettings) -> BackupSettings:
    """Keep ``settings``, with the folder trimmed and the numbers within bounds; returns them as
    kept."""
    kept = replace(settings.clamped(), folder=settings.folder.strip())
    save_settings(_NAME, kept.to_dict())
    return kept
