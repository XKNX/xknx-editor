"""Project timestamps in local time and the regional short date format."""

from datetime import UTC, datetime

from editor_gui.regional import regional_format
from editor_gui.widgets.value_pickers import format_date


def timestamp_text(stamp: str | None) -> str:
    """Show an ISO timestamp to the minute; an absent time zone means UTC."""
    if not stamp:
        return "-"
    try:
        moment = datetime.fromisoformat(stamp.strip())
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        local = moment.astimezone()
    except (ValueError, OverflowError, OSError):
        return "-"
    day = format_date(local.date().isoformat(), True, regional_format())
    return f"{day} {local:%H:%M}"
