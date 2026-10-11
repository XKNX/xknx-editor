"""Exceptions raised while downloading data into a KNX device."""

from __future__ import annotations


class DownloadError(Exception):
    """Base class for all download errors."""


class LoadStateError(DownloadError):
    """A Load State Machine did not reach the expected state."""


class PartialDownloadError(DownloadError):
    """A download failed after it had begun changing the device's load state.

    The Load Procedure unloads a device's tables before rewriting them, so a
    failure partway through can leave the device without a valid address /
    association / group-object table - it answers memory reads but no longer
    reacts to its group addresses. This wraps the underlying failure to signal
    that the device is likely left non-functional and needs a successful
    re-download; the original error is the exception's cause (``__cause__``).
    """


class VerificationError(DownloadError):
    """Data read back from the device does not match the data written."""


class CompareMismatch(VerificationError):
    """Valid response data differs from the comparison or verification image.

    Unlike malformed responses, this failure can match a control's
    ``OnError(Cause=CompareMismatch)`` handler. See .references/ets_map.md.
    """

    error_code = 0xC0042B09


# Error reported for a rejected write to a protected or non-existing resource.
# A device answers such a write with a
# 0-element response; a Load Procedure can map this error to success with an
# LdCtrlMapError bracket. See .references/ets_map.md for provenance.
RESOURCE_WRITE_PROTECTED_ERROR = 3221498632
RESOURCE_READ_PROTECTED_ERROR = 0xC0042B07


class PropertyAccessRejected(VerificationError):
    """A property access was rejected by the device (0 elements returned).

    A device answers a property access to a protected or non-existing resource
    (e.g. an absent interface object) with a 0-element response. A Load Procedure
    can bracket such an access with ``LdCtrlMapError`` to tolerate it, so this is a
    distinct, catchable subtype rather than a bare verification failure. The
    ``error_code`` is the value the rejection maps to, matched against a procedure's
    armed error mappings.
    """

    def __init__(
        self, message: str, *, error_code: int = RESOURCE_WRITE_PROTECTED_ERROR
    ) -> None:
        """Record the error value the rejection corresponds to."""
        super().__init__(message)
        self.error_code = error_code


class UnsupportedProcedureError(DownloadError):
    """The Load Procedure contains a step that is not supported."""


class ImageError(DownloadError):
    """The download image could not be assembled from the application data."""


class ProgrammingModeError(DownloadError):
    """No device, or more than one, is in programming mode for an address download."""


class AddressInUseError(DownloadError):
    """The individual address to download is already used by another device."""


class SerialNumberNotFoundError(DownloadError):
    """No device answers to the serial number an address download is addressed to."""
