"""The media of lines and segments as the editor shows them: the choices of their "Medium"
drop-downs and the translated text of a change the media do not allow."""

from __future__ import annotations

from editor_gui.plugins.project.strings import S
from xknxeditor.proj.core.medium import (
    BACKBONE_MEDIA,
    MEDIUM_NAMES,
    SEGMENT_MEDIA,
    MediumError,
)

# The texts whose name does not follow the reason (MEDIUM_ERROR_<REASON>).
_NAMES = {
    "device_line_medium": "MEDIUM_ERROR_DEVICE_LINE",
    "device_segment_medium": "MEDIUM_ERROR_DEVICE_SEGMENT",
}


def medium_error_text(error: MediumError) -> str:
    """The text of ``error`` in the language shown: the ``MEDIUM_ERROR_<REASON>`` string; the
    English one for a reason without a string."""
    name = _NAMES.get(error.reason, f"MEDIUM_ERROR_{error.reason.upper()}")
    text = getattr(S, name, None)
    return str(error) if not isinstance(text, str) else text.format(**error.fields)


def line_media() -> list[tuple[str, str]]:
    """The media a line offers, as (id, name)."""
    return list(MEDIUM_NAMES.items())


def backbone_media() -> list[tuple[str, str]]:
    """The media a backbone offers, as (id, name)."""
    return [(medium, MEDIUM_NAMES[medium]) for medium in BACKBONE_MEDIA]


def segment_media() -> list[tuple[str, str]]:
    """The media a sub-segment offers, as (id, name)."""
    return [(medium, MEDIUM_NAMES[medium]) for medium in SEGMENT_MEDIA]
