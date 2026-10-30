"""Exceptions raised while downloading data into a KNX device."""

from __future__ import annotations


class DownloadError(Exception):
    """Base class for all download errors."""


class LoadStateError(DownloadError):
    """A Load State Machine did not reach the expected state."""


class MemoryWriteNotAcknowledged(DownloadError):
    """A classic memory write received no acknowledgement from the device.

    Some firmwares advertise a large APDU yet only accept short memory services;
    a write sized to the advertised APDU then goes unacknowledged. The download
    retries with a lowered memory-service cap when this is raised.
    """


class MemoryReadNotAnswered(DownloadError):
    """A memory read that needs a long frame received no response.

    The device may answer it, yet the interface or a coupler on the way drops a frame that
    long. The download and its preflight retry with a lowered memory-service cap, so every
    response fits a standard frame, when this is raised.
    """


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
    ``OnError(Cause=CompareMismatch)`` handler.
    """

    error_code = 0xC0042B09

    def __init__(
        self,
        message: str,
        *,
        object_index: int | None = None,
        property_id: int | None = None,
        expected: bytes | None = None,
        read: bytes | None = None,
        mask: bytes | None = None,
        object_label: str | None = None,
    ) -> None:
        super().__init__(message)
        self.object_index = object_index
        self.property_id = property_id
        self.expected = expected
        self.read = read
        self.mask = mask
        # The object as messages show it (e.g. "type 40960 instance 1" for an object addressed
        # by type); None shows the object index.
        self.object_label = object_label

    def with_message(self, message: str) -> CompareMismatch:
        """The same mismatch with another message."""
        return CompareMismatch(
            message,
            object_index=self.object_index,
            property_id=self.property_id,
            expected=self.expected,
            read=self.read,
            mask=self.mask,
            object_label=self.object_label,
        )

    @property
    def details(self) -> str | None:
        """What the device reported against what the application expects, for a property
        compare; octets the mask excludes are shown as ``xx``."""
        if self.property_id is None or self.expected is None or self.read is None:
            return None
        name = _PROPERTY_NAMES.get(self.property_id) if self.object_index == 0 else None
        if self.property_id == _PID_PROGRAM_VERSION:
            name = "application version"
        obj = self.object_label if self.object_label is not None else self.object_index
        what = name or f"property {self.property_id} of object {obj}"
        mask = self.mask or b""
        mask += b"\xff" * max(0, len(self.expected) - len(mask))
        expected = " ".join(
            "xx" if m == 0 else f"{b:02X}"
            for b, m in zip(self.expected, mask, strict=False)
        )
        return (
            f"The device reports {what} {self.read.hex(' ').upper()}, "
            f"the application expects {expected}."
        )


_PID_PROGRAM_VERSION = 13
# Device object (index 0) properties a load procedure compares to identify a device.
_PROPERTY_NAMES = {11: "serial number", 12: "manufacturer ID", 78: "hardware type"}


# Error reported for a rejected write to a protected or non-existing resource.
# A device answers such a write with a
# 0-element response; a Load Procedure can map this error to success with an
# LdCtrlMapError bracket.
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
