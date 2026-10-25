"""The media of lines and segments as the editor shows them: the choices of their "Medium"
drop-downs and the translated text of a change the media do not allow."""

from __future__ import annotations

from collections.abc import Callable

from editor_gui.plugins.project.strings import S
from xknxeditor.proj.core.medium import MEDIUM_NAMES, SEGMENT_MEDIA, MediumError

_ERRORS: dict[str, Callable[[], str]] = {
    "segment_has_devices": lambda: S.MEDIUM_ERROR_SEGMENT_HAS_DEVICES,
    "no_sub_segments": lambda: S.MEDIUM_ERROR_NO_SUB_SEGMENTS,
    "sub_segment_iot": lambda: S.MEDIUM_ERROR_SUB_SEGMENT_IOT,
    "sub_segment_medium": lambda: S.MEDIUM_ERROR_SUB_SEGMENT_MEDIUM,
    "line_has_sub_segments": lambda: S.MEDIUM_ERROR_LINE_HAS_SUB_SEGMENTS,
    "too_many_segments": lambda: S.MEDIUM_ERROR_TOO_MANY_SEGMENTS,
    "main_segment": lambda: S.MEDIUM_ERROR_MAIN_SEGMENT,
    "device_line_medium": lambda: S.MEDIUM_ERROR_DEVICE_LINE,
    "device_segment_medium": lambda: S.MEDIUM_ERROR_DEVICE_SEGMENT,
}


def medium_error_text(error: MediumError) -> str:
    """The text of ``error`` in the language shown."""
    text = _ERRORS.get(error.reason)
    return str(error) if text is None else text().format(**error.fields)


def line_media() -> list[tuple[str, str]]:
    """The media a line offers, as (id, name)."""
    return list(MEDIUM_NAMES.items())


def segment_media() -> list[tuple[str, str]]:
    """The media a sub-segment offers, as (id, name)."""
    return [(medium, MEDIUM_NAMES[medium]) for medium in SEGMENT_MEDIA]
