"""
What ``xknx.io.usb`` takes from other parts of xknx that xknx 4.1.1 lacks.

These come with the xknx PR #2025 (KNX USB interfaces): ``xknx.exceptions.CouldNotParseUSBFrame``
and ``ResourceCEMIServerPropertyId.PID_CEMI_SERVER_SNA``. Drop this module for them once the
editor requires an xknx release with that PR.
"""

from __future__ import annotations

from xknx.exceptions import XKNXException
from xknx.profile.const import ResourcePropertyId


class CouldNotParseUSBFrame(XKNXException):
    """Exception class for a wrong HID report or KNX USB transfer frame."""

    def __init__(self, description: str = "") -> None:
        """Initialize CouldNotParseUSBFrame class."""
        super().__init__()
        self.description = description

    def __str__(self) -> str:
        """Return object as readable string."""
        return f'<CouldNotParseUSBFrame description="{self.description}" />'


class CEMIServerPropertyId(ResourcePropertyId):
    """The cEMI Server Object property xknx 4.1.1's ``ResourceCEMIServerPropertyId`` lacks."""

    PID_CEMI_SERVER_SNA = 57  # PDT_UNSIGNED_CHAR
