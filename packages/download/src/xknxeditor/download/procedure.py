"""Interpret a Load Procedure and execute it over a device connection.

A Load Procedure is an ordered list of Load Controls parsed from the application
program (Load Controls are described in KNX Standard v3.0.0, Volume 2 Cookbook,
02_03_01 "Load Controls"). :class:`LoadProcedureRunner` walks that list and turns
each control into the corresponding bus operation, reading bulk data from the
:class:`DownloadImage` where a control refers to it. The download procedures
themselves - complete download, partial download and unload - follow Chapter 3/5/3
"Configuration Procedures", sections 3.5.2, 3.5.3 and 3.5.4.

Load State Machine control follows the property based Realisation Type 1 (writing
load events to ``PID_LOAD_STATE_CONTROL`` of the addressed interface object, see
Chapter 3/5/1 "Resources", section 4.23); the memory mapped variant used by very
old (BCU) device models is out of scope.

Connection handling follows the point-to-point management connection lifecycle: the transport connection is
opened on a Connect control and closed on Disconnect; a Restart tears it down
(after a cooldown the next control reconnects); and any bus control encountered
without an open connection opens one first (auto-connect). This lifecycle is only
active when the runner is given a :class:`ConnectionManager`; with a fixed
programmer the connection is assumed to stay open (Connect/Disconnect are no-ops).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from xknx.profile.const import ResourceGenericPropertyId, ResourceObjectType

from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_base_t import LdCtrlBase
from xknxeditor.namespaces.intermediate.ld_ctrl_clear_lcfilter_table_t import (
    LdCtrlClearLcfilterTable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_base_t import LdCtrlCompareBase
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_mem_t import LdCtrlCompareMem
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_rel_mem_t import (
    LdCtrlCompareRelMem,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_control_variable_t import (
    LdCtrlControlVariable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_declare_prop_desc_t import (
    LdCtrlDeclarePropDesc,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_delay_t import LdCtrlDelay
from xknxeditor.namespaces.intermediate.ld_ctrl_disconnect_t import LdCtrlDisconnect
from xknxeditor.namespaces.intermediate.ld_ctrl_error_cause_t import LdCtrlErrorCause
from xknxeditor.namespaces.intermediate.ld_ctrl_invoke_function_prop_t import (
    LdCtrlInvokeFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_mem_t import (
    LdCtrlLoadImageMem,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
    LdCtrlLoadImageProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_rel_mem_t import (
    LdCtrlLoadImageRelMem,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_map_error_t import LdCtrlMapError
from xknxeditor.namespaces.intermediate.ld_ctrl_master_reset_t import LdCtrlMasterReset
from xknxeditor.namespaces.intermediate.ld_ctrl_max_length_t import LdCtrlMaxLength
from xknxeditor.namespaces.intermediate.ld_ctrl_mem_addr_space_t import (
    LdCtrlMemAddrSpace,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_read_function_prop_t import (
    LdCtrlReadFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_set_control_variable_t import (
    LdCtrlSetControlVariable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ctrl1_t import LdCtrlTaskCtrl1
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ctrl2_t import LdCtrlTaskCtrl2
from xknxeditor.namespaces.intermediate.ld_ctrl_task_ptr_t import LdCtrlTaskPtr
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem

from . import gaps, load_state
from .errors import (
    RESOURCE_READ_PROTECTED_ERROR,
    RESOURCE_WRITE_PROTECTED_ERROR,
    CompareMismatch,
    DownloadError,
    ImageError,
    PropertyAccessRejected,
    UnsupportedProcedureError,
    VerificationError,
)
from .group_communication import materialize_group_communication_controls
from .ip_config import IP_PARAMETER_OBJECT, IP_PROPERTY_WIDTHS
from .merge import resolve_download_controls
from .preflight import PreflightReport, PropertyDiff, SegmentDiff
from .programmer import DEFAULT_MAX_APDU_LENGTH, PID_TABLE_REFERENCE, DeviceProgrammer
from .property_layout import STANDARD_WRITE_WIDTHS, property_width
from .scope import (
    GROUP_COMMUNICATION_OBJECTS,
    DownloadScope,
    control_in_scope,
    target_object_type,
)
from .secure_config import (
    PID_TOOL_KEY,
    SECURITY_OBJECT,
    SECURITY_PROPERTY_WIDTHS,
    sequence_number_control,
)

if TYPE_CHECKING:
    from xknxeditor.prod import Application

    from .image import DownloadImage, PropertyValue
    from .programmer import ConnectionManager

logger = logging.getLogger(__name__)

# Default cooldown (seconds) to wait after a Restart before reconnecting.
DEFAULT_RESTART_COOLDOWN = 3.0

# Memory Control Block table property and its per-segment entry layout (KNX
# Standard v3.0.0, Chapter 3/5/1 "Resources", section 4.2.27 "PID_MCB_TABLE"):
# an 8 octet entry per segment, CRC-protected when bit 0 of octet 4 is clear, with
# the segment CRC in octets 6..7 (big-endian).
_PID_MCB_TABLE = ResourceGenericPropertyId.PID_MCB_TABLE
_MCB_ENTRY_SIZE = 8

# The Router object's relative memory holds the group-address filter table (System B), carried
# as the image's dedicated filter-table field.
_ROUTER_OBJECT_TYPE = ResourceObjectType.OBJECT_ROUTER

# Top bit of a load-procedure error value (the Original/Mapped fields of
# LdCtrlMapError): set means failure, clear means success. Mapping an error to a
# value with this bit clear turns a rejected access into a tolerated one. These
# 32-bit values and the flag convention are the tool's load-procedure
# representation, not a bus error code (see .references/ets_map.md).
_ERROR_FLAG = 0x80000000


def _inline_memory_data(data: bytes, size: int) -> bytes:
    if size < 0 or len(data) < size:
        raise ImageError(
            f"inline memory data has {len(data)} bytes for declared size {size}"
        )
    return data[:size]


def _bytes_match(
    read_back: bytes, expected: bytes, mask: bytes | None, length: int
) -> bool:
    """Whether the first ``length`` octets of ``read_back`` equal ``expected``.

    With a ``mask`` only the set bits of each octet have to match (KNX Standard v3.0.0, 3/5/1
    Compare controls); without a mask it is a plain equality over ``length``."""
    if mask:
        mask = mask.ljust(length, b"\xff")
        return all(
            read_back[i] & mask[i] == expected[i] & mask[i] for i in range(length)
        )
    return read_back[:length] == expected[:length]


def _significant_octets(expected: bytes, mask: bytes | None, *, start: int) -> int:
    """How many octets from ``start`` onwards the compare would actually have checked.

    Used to tell a device's zero padding apart from a truncated response. An octet past the end of
    a short reply is only ignorable when it carries nothing the compare cares about: a zero (the
    procedure padding a property to its maximum element size) or, with a mask, an octet whose
    checked bits are all clear. Anything else means the reply cut off real data.
    """
    if start >= len(expected):
        return 0
    if mask:
        return sum(
            1
            for i in range(start, len(expected))
            if expected[i] & (mask[i] if i < len(mask) else 0)
        )
    return sum(1 for octet in expected[start:] if octet)


def _unsupported_compare_reason(control: object) -> str | None:
    """A reason string if a Compare control uses a semantic this engine does not implement, else None.

    Plain (optionally masked) equality compares are executed. The Invert, Range and
    RetryInterval/TimeOut semantics (KNX Standard v3.0.0, 3/5/1 Compare controls) are NOT, so a
    control that sets any of them is rejected up front rather than silently verified with the wrong
    result (Invert would invert the outcome; Range/Retry change what "match" means)."""
    if not isinstance(control, LdCtrlCompareBase):
        return None
    unsupported: list[str] = []
    if control.invert:
        unsupported.append("Invert (compare passes on inequality)")
    if control.range is not None:
        unsupported.append("Range (value-in-interval compare)")
    if control.retry_interval or control.time_out:
        unsupported.append("RetryInterval/TimeOut (poll until the state is reached)")
    if not unsupported:
        return None
    return (
        f"compare control {type(control).__name__!r} uses "
        + ", ".join(unsupported)
        + "; only plain (optionally masked) equality compares are implemented, so this would "
        "verify with the wrong result and is not executed"
    )


# Load Controls handled entirely on the client side, without any bus effect.
# DeclarePropDesc updates the property layout cache without sending telegrams.
_CLIENT_SIDE = (
    "LdCtrlProgressText",
    "LdCtrlClearCachedObjectTypes",
    "LdCtrlDeclarePropDesc",
)

# Load Controls this engine executes on the bus (the isinstance branches of
# :meth:`LoadProcedureRunner._execute`). Used to pre-validate a resolved,
# scoped procedure before touching the device, so an unsupported control is
# reported up front instead of after earlier controls have already unloaded or
# written the device (a real master procedure can contain e.g.
# ``LdCtrlClearLCFilterTable``, which is not implemented).
_SUPPORTED_CONTROLS = frozenset(
    {
        "LdCtrlConnect",
        "LdCtrlDisconnect",
        "LdCtrlDelay",
        "LdCtrlRestart",
        "LdCtrlMasterReset",
        "LdCtrlUnload",
        "LdCtrlLoad",
        "LdCtrlLoadCompleted",
        "LdCtrlWriteMem",
        "LdCtrlLoadImageMem",
        "LdCtrlCompareMem",
        "LdCtrlWriteRelMem",
        "LdCtrlCompareRelMem",
        "LdCtrlLoadImageRelMem",
        "LdCtrlWriteProp",
        "LdCtrlLoadImageProp",
        "LdCtrlCompareProp",
        "LdCtrlInvokeFunctionProp",
        "LdCtrlReadFunctionProp",
        "LdCtrlAbsSegment",
        "LdCtrlRelSegment",
        "LdCtrlTaskSegment",
        "LdCtrlTaskPtr",
        "LdCtrlTaskCtrl1",
        "LdCtrlTaskCtrl2",
        # Arms/resets error tolerance around a bracketed access (see _execute).
        "LdCtrlMapError",
        "LdCtrlMaxLength",
        "LdCtrlSetControlVariable",
        # xsdata class name (type(control).__name__), not the XML "LdCtrlClearLCFilterTable".
        "LdCtrlClearLcfilterTable",
    }
)

# Controls that change device state - a memory/property write, a segment
# allocation, a task write, or a Load State Machine transition. The remaining
# supported controls only read (Compare*/Read*/LoadImage*), set up the transport
# (Connect/Disconnect/Delay) or arm client-side tolerance (MapError). Once one of
# these has run, a procedure that then fails may have left the device with
# partially rewritten (or unloaded) tables.
_STATE_MUTATING_CONTROLS = frozenset(
    {
        "LdCtrlRestart",
        "LdCtrlMasterReset",
        "LdCtrlUnload",
        "LdCtrlLoad",
        "LdCtrlLoadCompleted",
        "LdCtrlWriteMem",
        "LdCtrlWriteRelMem",
        "LdCtrlWriteProp",
        "LdCtrlInvokeFunctionProp",
        "LdCtrlAbsSegment",
        "LdCtrlRelSegment",
        "LdCtrlTaskSegment",
        "LdCtrlTaskPtr",
        "LdCtrlTaskCtrl1",
        "LdCtrlTaskCtrl2",
        "LdCtrlMaxLength",
    }
)


def _supports_extended_properties(application: Application) -> bool:
    """Whether the device takes the extended property services: the application says so,
    or it is a secure application."""
    program = getattr(application, "program", None)
    options = getattr(getattr(program, "static", None), "options", None)
    return bool(
        getattr(options, "supports_extended_property_services", False)
        or getattr(program, "is_secure_enabled", False)
    )


def _application_id(application: Application) -> bytes:
    """Assemble the 5 octet application id (manufacturer, type, version)."""
    manufacturer = application.manufacturer_id.split("-")[-1]
    manufacturer_id = int(manufacturer, 16)
    program = application.program
    return (
        manufacturer_id.to_bytes(2, "big")
        + program.application_number.to_bytes(2, "big")
        + bytes([program.application_version & 0xFF])
    )


#: A Sequence Number Sending above this is close to the end of its 48 bits (3/5/3 1.5.4.1).
SEQUENCE_NUMBER_WARNING = 0xFF0000000000


class LoadProcedureRunner:
    """Execute an application's Load Procedure over a device connection."""

    def __init__(
        self,
        application: Application,
        image: DownloadImage,
        programmer: DeviceProgrammer | None = None,
        *,
        connection_manager: ConnectionManager | None = None,
        max_apdu_length: int = DEFAULT_MAX_APDU_LENGTH,
        restart_cooldown: float = DEFAULT_RESTART_COOLDOWN,
        controls: Sequence[object] | None = None,
        scope: DownloadScope = DownloadScope.FULL,
        object_types: Mapping[int, int] | None = None,
        expected_descriptor: int | None = None,
        negotiate_apdu: bool = False,
        apdu_overhead: int = 0,
        authorize_levels: int = 0,
        memory_service_count: int | None = None,
        property_services: bool = True,
    ) -> None:
        """Initialize the runner.

        Provide either a fixed ``programmer`` (the connection stays open for the
        whole run; Connect/Disconnect are no-ops) or a ``connection_manager``
        (the runner opens/closes the connection per Connect/Disconnect and after
        a Restart, and auto-connects before any bus control). ``controls`` is the
        resolved Load Control list; when omitted the application's own procedure
        is flattened. ``scope`` selects a full or partial download. Pass the
        mask's ``InterfaceObjects`` as ``object_types`` for indexed controls:
        classification and table-image lookup require that mapping before I/O.

        With a ``connection_manager``, when ``expected_descriptor`` is given the
        device's mask version (device descriptor type 0) is read once on the first
        connection and must match, guarding against programming the wrong device;
        when ``negotiate_apdu`` is set the device's maximum APDU length is read
        once and used for the rest of the run (chunked writes then use larger
        telegrams). Both are skipped for a fixed ``programmer``.
        """
        if programmer is None and connection_manager is None:
            raise DownloadError("provide a programmer or a connection manager")
        self.application = application
        self.image = image
        self.scope = scope
        self._object_types = dict(object_types or {})
        self._active_control: object | None = None
        # LoadImageProp fills missing runtime-image data; later CompareProp
        # controls overlay it onto InlineData. Key by resolved object, PID and element so
        # index/type addressing and overlapping ranges refer to the same bytes.
        # Keep this separate from the caller's immutable download image.
        # see .references/ets_map.md
        self._property_captures: dict[tuple[int, int, int], bytes] = {}
        self._memory_captures: dict[
            tuple[LdCtrlMemAddrSpace | int | None, int], int
        ] = {}
        self._enable_segment_write = True
        # Inline WriteMem (ADM1 WriteDirect) is read back when this stays true.
        # Procedures that never set the variable keep today's Verify behaviour.
        self._enable_verify_on_write_direct = True
        self._property_widths: dict[tuple[int, int], int] = {}
        # Property controls addressed by object type use the extended property services when
        # the application supports them; they get a negative key in place of an object index,
        # mapped here to (object type, instance).
        self._extended_properties = _supports_extended_properties(application)
        self._extended_targets: dict[int, tuple[int, int]] = {}
        # Object type of each object a control addressed by type.
        self._index_types: dict[int, int] = {}
        self.restarted = False
        # Set once a state-changing control has run, so a caller can tell a
        # failure that touched nothing from one that may have left the device
        # with partially rewritten or unloaded tables.
        self.state_mutated = False
        self._programmer = programmer
        self._manager = connection_manager
        self._max_apdu_length = (
            programmer.max_apdu_length if programmer is not None else max_apdu_length
        )
        self._apdu_overhead = (
            programmer.apdu_overhead if programmer is not None else apdu_overhead
        )
        self._restart_cooldown = restart_cooldown
        self._restart_at: float | None = None
        # A one-shot cooldown that overrides the default for the next reconnect
        # (used when a Master Reset reports a longer device process time).
        self._pending_cooldown: float | None = None
        self._expected_descriptor = expected_descriptor
        self._negotiate_apdu = negotiate_apdu
        self._descriptor_checked = False
        self._negotiated_apdu: int | None = None
        self._authorize_levels = authorize_levels
        # BCU1 has no interface-object properties. Leave this on for every other mask.
        self._property_services = property_services
        # Optional lowered cap on the classic memory-service payload (a no-ACK
        # fallback); None keeps the programmer's APDU-derived default.
        self._memory_service_count = memory_service_count
        # (legacy control filter, original error) -> mapped error; 0 is wildcard.
        self._error_mappings: dict[tuple[int, int], int] = {}
        self._controls = (
            list(controls)
            if controls is not None
            else resolve_download_controls(application, scope=scope)
        )
        # Position of the control currently being executed, for diagnostics.
        self._position: tuple[int, int] | None = None

    async def run(self, progress: Callable[[int, int], None] | None = None) -> None:
        """Execute the Load Procedure, honouring the selected download scope.

        ``progress`` (optional) is called ``progress(done, total)`` after each executed
        control, where ``total`` is the number of in-scope controls, so a UI can show
        download progress.
        """
        in_scope = materialize_group_communication_controls(
            self.image, self._controls, self._object_types, self.scope
        )
        self._prevalidate(in_scope)
        self._property_captures.clear()
        self._memory_captures.clear()
        self._enable_segment_write = True
        self._enable_verify_on_write_direct = True
        self._error_mappings.clear()
        total = len(in_scope)
        logger.info(
            "download run start: %s, %d of %d load controls in scope",
            self._target(),
            total,
            len(self._controls),
        )
        for done, control in enumerate(in_scope, start=1):
            self._position = (done, total)
            self._active_control = control
            if type(control).__name__ in _STATE_MUTATING_CONTROLS:
                self.state_mutated = True
            try:
                await self._execute(control)
            except (CompareMismatch, PropertyAccessRejected) as error:
                if not self._handle_control_error(control, error):
                    raise
            if progress is not None:
                progress(done, total)
        self._position = None

    def _handle_control_error(
        self, control: object, error: CompareMismatch | PropertyAccessRejected
    ) -> bool:
        """Continue only when this control explicitly handles the typed failure.

        Nested handlers take precedence over legacy mappings, even when no cause
        matches. The first matching handler decides whether to continue at the
        next control or fail with an optional application message.
        """
        # see .references/ets_map.md
        handlers = control.on_error if isinstance(control, LdCtrlBase) else []
        if handlers:
            cause = None
            if isinstance(error, CompareMismatch):
                cause = LdCtrlErrorCause.COMPARE_MISMATCH
            elif error.error_code in (
                RESOURCE_READ_PROTECTED_ERROR,
                RESOURCE_WRITE_PROTECTED_ERROR,
                0xC0042B28,
            ):
                cause = LdCtrlErrorCause.RESOURCE_NOT_FOUND
            handler = next((h for h in handlers if h.cause == cause), None)
            if handler is None:
                return False
            if not handler.ignore:
                static = getattr(self.application.program, "static", None)
                messages = static.messages if static is not None else None
                message = (
                    next(
                        (
                            m.text
                            for m in messages.message
                            if m.id == handler.message_ref
                        ),
                        None,
                    )
                    if messages is not None and handler.message_ref
                    else None
                )
                if message is not None:
                    if isinstance(error, PropertyAccessRejected):
                        raise PropertyAccessRejected(
                            message, error_code=error.error_code
                        ) from error
                    details = error.details
                    raise error.with_message(
                        f"{message} {details}" if details else message
                    ) from error
                return False
            logger.info(
                "continuing after OnError Ignore=true: %s cause=%s error=%s",
                self._diagnostic_context(),
                handler.cause.value,
                error,
            )
            return True

        code = 5 if isinstance(control, LdCtrlUnload) else -1
        mapped = next(
            (
                mapped
                for (control_filter, original), mapped in self._error_mappings.items()
                if control_filter in (0, code) and original == error.error_code
            ),
            error.error_code,
        )
        if mapped & _ERROR_FLAG:
            error.error_code = mapped
            return False
        logger.info(
            "continuing under an active error mapping: %s error=%s",
            self._diagnostic_context(),
            error,
        )
        return True

    async def preflight(self) -> PreflightReport:
        """Report what the download would change, without changing anything.

        Walks the same scoped Load Controls as :meth:`run` but performs no write
        and drives no Load State Machine: for each control that would write, the
        device's current bytes are read and compared against the data the download
        would write. Compare controls (the application fingerprint gate) still run
        - they only read. The connection is opened read-only and closed again.
        """
        segments: list[SegmentDiff] = []
        properties: list[PropertyDiff] = []
        in_scope = materialize_group_communication_controls(
            self.image, self._controls, self._object_types, self.scope
        )
        self._prevalidate(in_scope)
        self._property_captures.clear()
        self._memory_captures.clear()
        self._enable_segment_write = True
        self._enable_verify_on_write_direct = True
        self._error_mappings.clear()
        logger.info(
            "download preflight start: %s, %d of %d load controls in scope",
            self._target(),
            len(in_scope),
            len(self._controls),
        )
        try:
            for done, control in enumerate(in_scope, start=1):
                self._position = (done, len(in_scope))
                self._active_control = control
                try:
                    await self._preflight_control(control, segments, properties)
                except (CompareMismatch, PropertyAccessRejected) as error:
                    if not self._handle_control_error(control, error):
                        raise
        finally:
            self._position = None
            await self._close()
        return PreflightReport(segments=tuple(segments), properties=tuple(properties))

    def _in_scope(self, control: object) -> bool:
        """Whether a control participates in the requested download scope."""
        return control_in_scope(control, self.scope, self._object_types)

    def _memory_runs(
        self,
        address: int,
        size: int,
        space: LdCtrlMemAddrSpace = LdCtrlMemAddrSpace.STANDARD,
    ) -> list[tuple[int, bytes]] | None:
        runs = self.image.masked_writes(
            address,
            size,
            include_initialized=self.scope
            in (DownloadScope.FULL, DownloadScope.APPLICATION),
        )
        if (
            space is LdCtrlMemAddrSpace.LC_FILTER
            and self.image.filter_table is not None
        ):
            runs = [(address, self.image.filter_table[:size])]
        return self._overlay_memory_capture(space, address, size, runs)

    def _overlay_memory_capture(
        self,
        index: LdCtrlMemAddrSpace | int | None,
        address: int,
        size: int,
        runs: list[tuple[int, bytes]] | None,
    ) -> list[tuple[int, bytes]] | None:
        """Fill missing image bytes from LoadImage without replacing supplied data."""
        values = {
            offset: value
            for (obj, offset), value in self._memory_captures.items()
            if obj == index and address <= offset < address + size
        }
        if not values:
            return runs
        for start, data in runs or []:
            values.update({start + n: value for n, value in enumerate(data)})
        result: list[tuple[int, bytes]] = []
        for offset in sorted(values):
            if result and result[-1][0] + len(result[-1][1]) == offset:
                start, data = result[-1]
                result[-1] = (start, data + bytes([values[offset]]))
            else:
                result.append((offset, bytes([values[offset]])))
        return result

    def _capture_memory(
        self, index: LdCtrlMemAddrSpace | int, address: int, data: bytes
    ) -> None:
        for offset, value in enumerate(data, address):
            self._memory_captures.setdefault((index, offset), value)

    @staticmethod
    def _overlay_inline_memory(
        address: int, inline: bytes, runs: list[tuple[int, bytes]] | None
    ) -> bytes:
        """Use inline bytes only where the supplied or captured image has no data."""
        data = bytearray(inline)
        end = address + len(data)
        for start, overlay in runs or []:
            lo, hi = max(address, start), min(end, start + len(overlay))
            if lo < hi:
                data[lo - address : hi - address] = overlay[lo - start : hi - start]
        return bytes(data)

    def _memory_write_runs(
        self, control: LdCtrlWriteMem | LdCtrlWriteRelMem, index: int | None = None
    ) -> list[tuple[int, bytes]] | None:
        if isinstance(control, LdCtrlWriteRelMem):
            address = control.offset
            runs = self._relative_runs(
                control, index=index, allow_missing=control.inline_data is not None
            )
        else:
            address = control.address
            runs = self._memory_runs(address, control.size, control.address_space)
        if control.inline_data is None:
            return runs
        inline = _inline_memory_data(control.inline_data, control.size)
        return [(address, self._overlay_inline_memory(address, inline, runs))]

    def _prevalidate(self, in_scope: list[object]) -> None:
        """Reject an unsupported control before any device state is changed.

        Scans the resolved, scoped controls up front so a procedure containing a
        control this engine cannot execute fails before the connection is opened
        or any Load State Machine is unloaded, rather than partway through
        (leaving the device unloaded). Client-side no-ops are accepted.
        """
        captured_objects: set[int | None] = set()
        for position, control in enumerate(in_scope, start=1):
            if (
                isinstance(control, LdCtrlWriteProp)
                and control.prop_id == PID_TABLE_REFERENCE
            ):
                raise UnsupportedProcedureError(
                    "PID7 is a read-only runtime table reference"
                )
            if isinstance(control, LdCtrlDeclarePropDesc):
                property_width(control.prop_type)
            if isinstance(control, LdCtrlMapError) and control.ld_ctrl_filter not in (
                0,
                5,
            ):
                raise UnsupportedProcedureError(
                    f"unsupported MapError control filter {control.ld_ctrl_filter}"
                )
            if isinstance(control, LdCtrlLoadImageRelMem):
                captured_objects.add(control.obj_idx)
            if isinstance(control, LdCtrlSetControlVariable) and control.name not in (
                LdCtrlControlVariable.ENABLE_SEGMENT_WRITE,
                LdCtrlControlVariable.ENABLE_VERIFY_ON_WRITE_DIRECT,
            ):
                raise UnsupportedProcedureError(
                    f"unsupported control variable {control.name.value}"
                )
            if (
                isinstance(control, (LdCtrlWriteMem, LdCtrlWriteRelMem))
                and control.inline_data is not None
            ):
                _inline_memory_data(control.inline_data, control.size)
            if (
                isinstance(control, (LdCtrlCompareMem, LdCtrlCompareRelMem))
                and control.inline_data
            ):
                _inline_memory_data(control.inline_data, control.size)
            if isinstance(control, LdCtrlLoadImageProp) and (
                not 1 <= control.count <= 15
                or (control.start_element == 0 and control.count != 1)
            ):
                raise UnsupportedProcedureError(
                    f"LoadImageProp at control {position} requires a fixed range "
                    "of 1..15 elements (element zero must be read separately)"
                )
            if (
                isinstance(control, LdCtrlWriteRelMem)
                and control.inline_data is None
                and control.obj_idx not in captured_objects
            ):
                self._relative_runs(control)
            name = type(control).__name__
            if name in _SUPPORTED_CONTROLS or name in _CLIENT_SIDE:
                # A supported control may still use a compare semantic (Invert/Range/Retry) this
                # engine does not implement; reject it up front rather than mis-verify on the bus.
                reason = _unsupported_compare_reason(control)
                if reason is None:
                    continue
                self._position = (position, len(in_scope))
                error = self._unsupported(control, reason=reason)
                self._position = None
                raise error
            self._position = (position, len(in_scope))
            error = self._unsupported(control)
            self._position = None
            raise error

    def _target(self) -> str:
        """A short, log-friendly identity of the download target for diagnostics."""
        try:
            app = _application_id(self.application).hex()
        except (ValueError, AttributeError):
            app = "unknown"
        return f"app={app} scope={self.scope.name}"

    def _unsupported(
        self, control: object, *, reason: str | None = None
    ) -> UnsupportedProcedureError:
        """Build (and log) a diagnostic error for a control this engine can't run.

        The message names the KNX Standard service the control maps to (via
        :mod:`xknxeditor.download.gaps`), the position in the procedure and the
        target, so a bug report shows immediately what is still missing.
        """
        name = type(control).__name__
        detail = reason if reason is not None else gaps.describe_missing(name)
        where = ""
        if self._position is not None:
            where = f" at in-scope load control {self._position[0]}/{self._position[1]}"
        message = (
            f"{detail}{where} [{self._target()}]. Please file a bug report with this "
            f"message, the device order number and the mask version so the missing "
            f"step can be implemented."
        )
        logger.warning("unsupported load control: %s", message)
        return UnsupportedProcedureError(message)

    def _diagnostic_context(self) -> str:
        control = self._active_control
        index = getattr(control, "obj_idx", None)
        if index is None:
            index = getattr(control, "lsm_idx", None)
        object_type = getattr(control, "obj_type", None)
        if object_type is None and index is not None:
            object_type = self._object_types.get(index)
        return (
            f"control={self._position} kind={type(control).__name__} "
            f"target-index={index} target-type={object_type} "
            f"occurrence={getattr(control, 'occurrence', 0)}"
        )

    async def _bus(self) -> DeviceProgrammer:
        """Return the connected programmer, opening a connection if needed."""
        if self._programmer is not None:
            self._programmer.diagnostic_context = self._diagnostic_context()
            if self.application.program.mask_version == "MV-07B0":
                self._programmer.table_reference_width = 4
            if self._memory_service_count is not None:
                self._programmer.max_memory_service_count = self._memory_service_count
            return self._programmer
        if self._manager is None:
            raise DownloadError("no connection available")
        await self._await_restart_cooldown()
        logger.debug("opening device connection")
        connection = await self._manager.open()
        self._programmer = DeviceProgrammer(
            connection,
            max_apdu_length=self._max_apdu_length,
            apdu_overhead=self._apdu_overhead,
        )
        self._programmer.diagnostic_context = self._diagnostic_context()
        if self.application.program.mask_version == "MV-07B0":
            self._programmer.table_reference_width = 4
        if self._memory_service_count is not None:
            self._programmer.max_memory_service_count = self._memory_service_count
        if not self._property_services:
            self._programmer.skip_property_verify_probe()
        await self._prepare_device(self._programmer)
        return self._programmer

    async def _prepare_device(self, programmer: DeviceProgrammer) -> None:
        """Guard the device mask, negotiate the APDU length, and authorize.

        Runs on the first opened connection: reads the device descriptor to
        confirm the mask matches ``expected_descriptor`` (guarding against
        programming the wrong device) and reads the device's maximum APDU length
        to use larger telegrams. The negotiated length is remembered and reapplied
        after a reconnect, so it is read only once. When the mask uses access
        protection (``authorize_levels`` greater than zero) an A_Authorize
        handshake runs on every connection, before any write.
        """
        if self._expected_descriptor is not None and not self._descriptor_checked:
            actual = await programmer.read_device_descriptor()
            if actual != self._expected_descriptor:
                raise VerificationError(
                    f"device mask mismatch: expected descriptor "
                    f"{self._expected_descriptor:#06x}, device reports {actual:#06x}. "
                    f"Refusing to program - check the individual address points at "
                    f"the intended device."
                )
            self._descriptor_checked = True
            logger.info("device mask confirmed: descriptor %#06x", actual)
        if self._negotiate_apdu:
            if self._negotiated_apdu is None:
                if self._property_services:
                    device_max = await programmer.read_max_apdu_length()
                    self._negotiated_apdu = max(
                        DEFAULT_MAX_APDU_LENGTH,
                        min(device_max, self._max_apdu_length),
                    )
                    logger.info(
                        "negotiated APDU length: %d (device reports %d)",
                        self._negotiated_apdu,
                        device_max,
                    )
                else:
                    # BCU1 APDU length is the classic 15 octets. Property 56
                    # does not exist there; reading it times out.
                    self._negotiated_apdu = DEFAULT_MAX_APDU_LENGTH
            programmer.max_apdu_length = self._negotiated_apdu
        if self._authorize_levels > 0:
            # Access protection is per connection, so authorize on every open (a
            # Restart drops the connection and the next control reconnects). No
            # per-device access key is available here, so free access is used; a
            # device with no key set grants full access (level 0) for it.
            level = await programmer.authorize()
            if level == 0:
                logger.debug("authorized with free access (level 0)")
            else:
                logger.warning(
                    "device granted only access level %d for the free access key; "
                    "it may be locked with an access key and refuse writes",
                    level,
                )

    async def _close(self) -> None:
        """Close the current connection when the runner manages the lifecycle."""
        if self._manager is not None and self._programmer is not None:
            logger.debug("closing device connection")
            await self._manager.close()
            self._programmer = None

    async def _await_restart_cooldown(self) -> None:
        """Wait out the restart cooldown before reconnecting, if one is pending."""
        if self._restart_at is None:
            return
        cooldown = (
            self._pending_cooldown
            if self._pending_cooldown is not None
            else self._restart_cooldown
        )
        elapsed = time.monotonic() - self._restart_at
        remaining = cooldown - elapsed
        if remaining > 0:
            logger.debug("restart cooldown: waiting %.2fs before reconnect", remaining)
            await asyncio.sleep(remaining)
        self._restart_at = None
        self._pending_cooldown = None

    async def _resolve_index(self, control: object, *, extended: bool = False) -> int:
        """Resolve the interface object index a control addresses.

        A control identifies its object by explicit ``obj_idx``, by
        ``obj_type`` + zero-based ``occurrence`` (resolved on the device), or by
        ``lsm_idx`` which, for property based management, is the object index.
        With ``extended``, an object addressed by type gets a key for the extended
        property services instead (instance = occurrence + 1) when the application
        supports them; the device's object list is not read then.
        """
        obj_idx = getattr(control, "obj_idx", None)
        if obj_idx is not None:
            return obj_idx
        obj_type = getattr(control, "obj_type", None)
        if obj_type is not None:
            occurrence = getattr(control, "occurrence", 0)
            if extended and self._extended_properties:
                key = -1 - ((obj_type << 12) | (occurrence + 1))
                self._extended_targets[key] = (obj_type, occurrence + 1)
                self._index_types[key] = obj_type
                return key
            programmer = await self._bus()
            index = await programmer.locate_object(obj_type, occurrence)
            self._index_types[index] = obj_type
            return index
        lsm_idx = getattr(control, "lsm_idx", None)
        if lsm_idx is not None:
            return lsm_idx
        raise self._unsupported(
            control,
            reason=(
                f"load control {type(control).__name__!r} addresses no interface "
                f"object (neither obj_idx, obj_type nor lsm_idx is set)"
            ),
        )

    async def _execute(self, control: object) -> None:
        """Dispatch a single Load Control to the matching bus operation."""
        where = f" [{self._position[0]}/{self._position[1]}]" if self._position else ""
        logger.debug("execute load control: %s%s", type(control).__name__, where)
        if isinstance(control, LdCtrlDeclarePropDesc):
            index = await self._resolve_index(control)
            self._property_widths[index, control.prop_id] = property_width(
                control.prop_type
            )
            return
        if isinstance(control, LdCtrlSetControlVariable):
            if control.name is LdCtrlControlVariable.ENABLE_VERIFY_ON_WRITE_DIRECT:
                self._enable_verify_on_write_direct = control.value
                return
            if control.name is not LdCtrlControlVariable.ENABLE_SEGMENT_WRITE:
                raise UnsupportedProcedureError(
                    f"unsupported control variable {control.name.value}"
                )
            self._enable_segment_write = control.value
            return
        if isinstance(control, LdCtrlMaxLength):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            await programmer.send_load_event(
                index,
                load_state.relative_allocation(control.size),
                load_state.LoadState.LOADING,
            )
            return
        if isinstance(control, LdCtrlConnect):
            await self._bus()
            return
        if isinstance(control, LdCtrlDisconnect):
            await self._close()
            return
        if isinstance(control, LdCtrlDelay):
            await asyncio.sleep(control.milli_seconds / 1000)
            return
        if isinstance(control, LdCtrlRestart):
            programmer = await self._bus()
            await programmer.restart()
            self.restarted = True
            # A Restart tears down the connection device-side; drop it and
            # arm the cooldown so the next control reconnects after a pause.
            await self._close()
            self._restart_at = time.monotonic()
            return
        if isinstance(control, LdCtrlMasterReset):
            programmer = await self._bus()
            process_time = await programmer.master_reset(
                control.erase_code, control.channel_number
            )
            self.restarted = True
            # Like a Restart, the device drops the connection; additionally it
            # reports how long it stays unreachable, so honour that as the
            # one-shot reconnect cooldown when it exceeds the default.
            await self._close()
            self._restart_at = time.monotonic()
            # Process Time is a 2 octet unsigned value in *seconds* (DPT 7.005,
            # KNX 3/5/2 3.7.1.2.2), so use it directly - not milliseconds.
            self._pending_cooldown = max(self._restart_cooldown, process_time)
            return
        if isinstance(control, LdCtrlUnload):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            await programmer.send_load_event(
                index, load_state.unload(), load_state.LoadState.UNLOADED
            )
            return
        if isinstance(control, LdCtrlLoad):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            # Some present objects (DIMinBOX, issue #24) keep reporting LOADED
            # after accepting START_LOADING. Allow that result for this control;
            # allocation events and LoadCompleted still verify their own states.
            await programmer.send_load_event(
                index,
                load_state.start_loading(),
                load_state.LoadState.LOADING,
                also_accept=load_state.LoadState.LOADED,
            )
            return
        if isinstance(control, LdCtrlLoadCompleted):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            await programmer.send_load_event(
                index, load_state.load_complete(), load_state.LoadState.LOADED
            )
            return
        if isinstance(control, LdCtrlWriteMem):
            await self._write_mem(control)
            return
        if isinstance(control, LdCtrlLoadImageMem):
            # LoadImageMem reads device memory into the image (read-back /
            # compare), it does not write.
            _require_standard(control.address_space)
            programmer = await self._bus()
            self._capture_memory(
                control.address_space,
                control.address,
                await programmer.read_memory(control.address, control.size),
            )
            return
        if isinstance(control, LdCtrlCompareMem):
            _require_standard(control.address_space)
            await self._compare_mem(
                control.address, self._memory_compare_data(control), control.mask
            )
            return
        if isinstance(control, LdCtrlWriteRelMem):
            await self._write_rel_mem(control)
            return
        if isinstance(control, LdCtrlCompareRelMem):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            base = await programmer.read_table_reference(index)
            await self._compare_mem(
                base + control.offset,
                self._memory_compare_data(control, index),
                control.mask,
            )
            return
        if isinstance(control, LdCtrlLoadImageRelMem):
            # Read-back into the image (see LoadImageMem); it does not write.
            index = await self._resolve_index(control)
            programmer = await self._bus()
            base = await programmer.read_table_reference(index)
            self._capture_memory(
                index,
                control.offset,
                await programmer.read_memory(base + control.offset, control.size),
            )
            return
        if isinstance(control, LdCtrlWriteProp):
            await self._write_prop(control)
            return
        if isinstance(control, LdCtrlLoadImageProp):
            await self._load_image_prop(control)
            return
        if isinstance(control, LdCtrlCompareProp):
            await self._compare_prop(control)
            return
        if isinstance(control, LdCtrlInvokeFunctionProp):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            await programmer.invoke_function_property(
                index, control.prop_id, control.inline_data or b""
            )
            return
        if isinstance(control, LdCtrlReadFunctionProp):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            await programmer.read_function_property(index, control.prop_id)
            return
        if isinstance(control, LdCtrlAbsSegment):
            await self._abs_segment(control)
            return
        if isinstance(control, LdCtrlRelSegment):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            await programmer.send_load_event(
                index,
                load_state.data_relative_allocation(
                    control.size, mode=control.mode, fill=control.fill
                ),
                load_state.LoadState.LOADING,
            )
            return
        if isinstance(control, LdCtrlTaskSegment):
            await self._task_segment(control)
            return
        if isinstance(control, LdCtrlTaskPtr):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            await programmer.send_load_event(
                index,
                load_state.task_pointer(
                    control.init_ptr, control.save_ptr, control.serial_ptr
                ),
                load_state.LoadState.LOADING,
            )
            return
        if isinstance(control, LdCtrlTaskCtrl1):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            await programmer.send_load_event(
                index,
                load_state.task_control_1(control.address, control.count),
                load_state.LoadState.LOADING,
            )
            return
        if isinstance(control, LdCtrlTaskCtrl2):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            await programmer.send_load_event(
                index,
                load_state.task_control_2(
                    control.callback, control.address, control.seg0, control.seg1
                ),
                load_state.LoadState.LOADING,
            )
            return
        if isinstance(control, LdCtrlClearLcfilterTable):
            # Clear the line-coupler filter table. A coupler download writes the whole
            # filter table right after (LdCtrlRelSegment + LdCtrlWriteRelMem over the full
            # 8192/3584-byte resource, zero bits included — see the coupler load procedures
            # in the KNX master data, e.g. MV-2920/MV-0900), so an explicit clear is
            # redundant for a full download and is a no-op here. The function-property /
            # memory clear only matters for a partial filter-table update, which this engine
            # does not perform. The filter-table bytes themselves must be supplied in the
            # image (see GroupCommunication.filter_table); otherwise the following
            # WriteRelMem/WriteMem fails with "no image data".
            return
        if isinstance(control, LdCtrlMapError):
            # Arm or reset tolerance of a specific error. The mapped value carries
            # the error flag in its top bit; mapping an error to a value with that
            # bit clear (0) turns the following access into a success, so the
            # bracketed access becomes optional. Mapping an error to itself removes
            # that mapping; a different error retains failure with the new code. The
            # bracketed access reports its rejection as a PropertyAccessRejected,
            # matched by both control filter and error identity in run().
            # see .references/ets_map.md
            key = (control.ld_ctrl_filter, control.original_error)
            self._error_mappings.pop(key, None)
            if control.mapped_error != control.original_error:
                self._error_mappings[key] = control.mapped_error
            return
        if type(control).__name__ in _CLIENT_SIDE:
            return
        raise self._unsupported(control)

    def _direct_memory_verify(self, control: LdCtrlWriteMem) -> bool:
        """Whether this WriteMem is read back after the write.

        ``EnableVerifyOnWriteDirect`` gates inline writes only (ADM1 WriteDirect).
        Image-backed writes keep the control's own Verify attribute. The flag
        defaults to enabled, so a procedure that never sets it still verifies.
        """
        if control.inline_data is not None and not self._enable_verify_on_write_direct:
            return False
        return bool(control.verify)

    async def _write_mem(self, control: LdCtrlWriteMem) -> None:
        """Write a WriteMem control to memory.

        Supplied and captured image bytes overlay inline defaults within Size.
        """
        _require_standard(control.address_space)
        programmer = await self._memory_programmer()
        runs = self._memory_write_runs(control)
        if runs is None:
            raise ImageError(
                f"no image data for address range {control.address:#06x}.."
                f"{control.address + control.size:#06x}"
            )
        verify = self._direct_memory_verify(control)
        logger.debug(
            "write_mem image-backed: addr=%#06x size=%d runs=%d verify=%s",
            control.address,
            control.size,
            len(runs),
            verify,
        )
        for address, data in runs:
            await programmer.write_memory(address, data, verify=verify)

    async def _write_rel_mem(self, control: LdCtrlWriteRelMem) -> None:
        """Write a WriteRelMem control relative to the object's table base.

        The image mirrors a relative segment in its own relative address space
        (the segment sits at its relative base, e.g. ``0``); only the device write
        adds the table base read from the object at run time. So the image is
        looked up at ``control.offset`` and each run is written at ``base + run``.
        """
        index = await self._resolve_index(control)
        programmer = await self._bus()
        base = await programmer.read_table_reference(index)
        logger.debug(
            "write_rel_mem: obj-index=%d table-base=%#06x offset=%#06x size=%d",
            index,
            base,
            control.offset,
            control.size,
        )
        runs = self._memory_write_runs(control, index=index)
        if runs is None:
            raise ImageError(
                f"no image data for relative range {control.offset:#06x}.."
                f"{control.offset + control.size:#06x}"
            )
        if runs:
            programmer = await self._memory_programmer()
        for run_offset, data in runs:
            await programmer.write_memory(
                base + run_offset, data, verify=control.verify
            )

    async def _memory_programmer(self) -> DeviceProgrammer:
        programmer = await self._bus()
        if self.application.program.mask_version == "MV-07B0":
            await programmer.enable_memory_auto_verify()
        return programmer

    def _relative_runs(
        self,
        control: LdCtrlWriteRelMem,
        *,
        index: int | None = None,
        allow_missing: bool = False,
    ) -> list[tuple[int, bytes]] | None:
        """Return the relative ``(offset, data)`` runs a WriteRelMem writes.

        Resolve indexed controls through the same mask mapping used by scoping.
        Known tables require their own image. Only other objects may use the flat
        parameter image at ``control.offset``.
        """
        object_type = target_object_type(control, self._object_types)
        if index is None:
            index = control.obj_idx
        if index is None:
            indices = sorted(
                i for i, t in self._object_types.items() if t == object_type
            )
            if 0 <= control.occurrence < len(indices):
                index = indices[control.occurrence]
        if (
            self.image.application_segments
            and object_type not in GROUP_COMMUNICATION_OBJECTS
            and object_type != _ROUTER_OBJECT_TYPE
        ):
            if index is None:
                raise ImageError(f"cannot resolve image object type {object_type}")
            from .image import DownloadImage

            memory = tuple(
                s.memory
                for s in self.image.application_segments
                if s.object_index == index
            )
            runs = DownloadImage(memory, ()).masked_writes(
                control.offset,
                control.size,
                include_initialized=self.scope
                in (DownloadScope.FULL, DownloadScope.APPLICATION),
            )
            return self._overlay_memory_capture(
                index, control.offset, control.size, runs
            )
        # A coupler's filter table (Router object type 6) is carried as its own image field.
        if object_type == _ROUTER_OBJECT_TYPE and self.image.filter_table is not None:
            return self._overlay_memory_capture(
                index,
                control.offset,
                control.size,
                [
                    (
                        control.offset,
                        self.image.filter_table[
                            control.offset : control.offset + control.size
                        ],
                    )
                ],
            )
        if object_type is not None:
            segment = self.image.relative_segment(object_type)
            if segment is not None:
                start, end = control.offset, control.offset + control.size
                runs = [
                    (max(offset, start), data[max(0, start - offset) : end - offset])
                    for offset, data in segment.masked_runs()
                    if offset < end and offset + len(data) > start
                ]
                return self._overlay_memory_capture(
                    index, control.offset, control.size, runs
                )
        if object_type in GROUP_COMMUNICATION_OBJECTS:
            captured = self._overlay_memory_capture(
                index, control.offset, control.size, None
            )
            if captured is not None or allow_missing:
                return captured
            raise ImageError(f"missing table image for object type {object_type}")
        runs = self.image.masked_writes(
            control.offset,
            control.size,
            include_initialized=self.scope
            in (DownloadScope.FULL, DownloadScope.APPLICATION),
        )
        return self._overlay_memory_capture(index, control.offset, control.size, runs)

    async def _compare_mem(
        self, address: int, expected: bytes, mask: bytes | None = None
    ) -> None:
        """Read memory and compare it against expected data (mask-aware, like _compare_prop)."""
        programmer = await self._bus()
        read_back = await programmer.read_memory(address, len(expected))
        matched = _bytes_match(read_back, expected, mask, len(expected))
        logger.debug(
            "compare_mem: addr=%#06x len=%d masked=%s -> %s",
            address,
            len(expected),
            mask is not None,
            "match" if matched else "MISMATCH",
        )
        if not matched:
            raise CompareMismatch(
                f"memory compare failed at {address:#06x}: "
                f"expected {expected.hex()} read {read_back.hex()}"
            )

    def _property_write_data(self, index: int, control: LdCtrlWriteProp) -> bytes:
        """Overlay image elements onto inline data, keeping device-owned CRCs zero."""
        if control.start_element == 0:
            # Element 0 of an array property is its number of valid elements, 2 octets; writing
            # 0 clears the array.
            return (control.inline_data or b"").ljust(2 * control.count, b"\x00")[
                : 2 * control.count
            ]
        object_type = (
            control.obj_type
            if control.obj_idx is None and control.obj_type is not None
            else self._object_types.get(index)
        )
        if control.prop_id == 13 and object_type in (3, 4):
            if control.start_element != 1 or control.count != 1:
                raise ImageError("application identity requires element 1, count 1")
            return _application_id(self.application)
        width = self._property_widths.get(
            (index, control.prop_id), STANDARD_WRITE_WIDTHS.get(control.prop_id)
        )
        if width is None:
            raise ImageError(
                f"missing element width for object {index} property {control.prop_id}"
            )
        size = control.count * width
        if (
            control.count <= 0
            or width <= 0
            or control.start_element < 0
            or control.start_element + control.count > 0x1000
        ):
            raise ImageError("invalid property element range")
        if control.prop_id == PID_TABLE_REFERENCE:
            raise UnsupportedProcedureError(
                "PID7 is a read-only runtime table reference"
            )
        inline = (control.inline_data or b"")[:size]
        data = bytearray(inline.ljust(size, b"\x00"))
        covered = bytearray(b"\x01" * len(inline) + bytes(size - len(inline)))
        for n in range(control.count):
            captured = self._property_captures.get(
                (index, control.prop_id, control.start_element + n)
            )
            if captured is not None:
                if len(captured) != width:
                    raise ImageError("captured property element width mismatch")
                data[n * width : (n + 1) * width] = captured
                covered[n * width : (n + 1) * width] = b"\x01" * width
        for prop in self.image.properties:
            if (
                prop.property_id == control.prop_id
                and self._image_property_applies(prop, index, control)
                and control.start_element > 0
            ):
                start = (control.start_element - 1) * width
                overlay = prop.data[start : start + size]
                data[: len(overlay)] = overlay
                covered[: len(overlay)] = b"\x01" * len(overlay)
                break
        if not all(covered):
            raise ImageError(
                f"incomplete property image for object {index} property {control.prop_id}: need {size}, got {sum(covered)}"
            )
        if control.prop_id == _PID_MCB_TABLE:
            data = data[: control.count * _MCB_ENTRY_SIZE]
            if len(data) != control.count * _MCB_ENTRY_SIZE:
                raise ImageError("incomplete MCB entry")
            entries = bytearray(data)
            for start in range(0, len(entries), _MCB_ENTRY_SIZE):
                entries[start + 6 : start + 8] = b"\x00\x00"
            data = bytes(entries)
        return bytes(data)

    def _image_property_applies(
        self,
        prop: PropertyValue,
        index: int,
        control: LdCtrlWriteProp | LdCtrlCompareProp | LdCtrlLoadImageProp,
    ) -> bool:
        """Whether an image property belongs to the object ``control`` addresses at ``index``.

        A property of an object addressed by type matches the same occurrence of that type; an
        object whose type is not known is not excluded.
        """
        if prop.object_index is not None:
            return prop.object_index == index
        if prop.occurrence != control.occurrence:
            return False
        if prop.object_type is None:
            return True
        object_type = (
            control.obj_type
            if control.obj_idx is None and control.obj_type is not None
            else self._object_types.get(index)
        )
        return object_type is None or object_type == prop.object_type

    def captured_property(self, object_type: int, property_id: int) -> bytes | None:
        """The first element a LoadImageProp captured from ``property_id`` of an object of
        ``object_type``, or ``None``."""
        for (index, pid, element), data in self._property_captures.items():
            if (
                pid == property_id
                and element == 1
                and self._index_types.get(index) == object_type
            ):
                return data
        return None

    async def _object_index(self, index: int) -> int:
        """The device's object index for ``index``, locating an extended target."""
        target = self._extended_targets.get(index)
        if target is None:
            return index
        programmer = await self._bus()
        return await programmer.locate_object(target[0], target[1] - 1)

    def _property_width(self, index: int, property_id: int) -> int | None:
        return self._property_widths.get(
            (index, property_id), STANDARD_WRITE_WIDTHS.get(property_id)
        )

    async def _extended_fits(self, index: int, property_id: int) -> bool:
        """Whether ``index`` is an extended target whose elements fit an extended frame;
        otherwise the standard services are used with the located object index."""
        if index not in self._extended_targets:
            return False
        width = self._property_width(index, property_id)
        programmer = await self._bus()
        return programmer.fits_extended_property(width or 0)

    async def _read_property(
        self,
        index: int,
        control: LdCtrlCompareProp | LdCtrlLoadImageProp,
    ) -> bytes:
        """Read every element, splitting a range that would not fit one APDU.

        A device answers an oversized property read with zero elements. The
        Weinzierl application captures seven 8-octet MCB entries in one control;
        at an APDU of 55 that response is 61 octets and the device rejects it.
        """
        programmer = await self._bus()
        if index in self._extended_targets:
            await self._ensure_property_width(index, control)
        width = self._property_width(index, control.prop_id) or 0
        extended = await self._extended_fits(index, control.prop_id)
        per_frame = (
            (
                programmer.extended_property_chunk_size(width)
                if extended
                else programmer.property_chunk_size(width)
            )
            if width
            else control.count
        )
        parts: list[bytes] = []
        remaining = control.count
        start = control.start_element
        while remaining:
            frame = min(per_frame, remaining)
            parts.append(
                await self._read_property_frame(
                    programmer,
                    index,
                    control,
                    count=frame,
                    start_index=start,
                    extended=extended,
                )
            )
            remaining -= frame
            start += frame
        return b"".join(parts)

    async def _read_property_frame(
        self,
        programmer: DeviceProgrammer,
        index: int,
        control: LdCtrlCompareProp | LdCtrlLoadImageProp,
        *,
        count: int,
        start_index: int,
        extended: bool,
    ) -> bytes:
        if extended:
            object_type, instance = self._extended_targets[index]
            return await programmer.read_property_ext(
                object_type,
                instance,
                control.prop_id,
                count=count,
                start_index=start_index,
                require_nonzero=True,
            )
        return await programmer.read_property(
            await self._object_index(index),
            control.prop_id,
            count=count,
            start_index=start_index,
            require_nonzero=True,
        )

    async def _ensure_property_width(
        self,
        index: int,
        control: LdCtrlWriteProp | LdCtrlCompareProp | LdCtrlLoadImageProp,
    ) -> None:
        key = (index, control.prop_id)
        if key in self._property_widths:
            return
        if (
            control.obj_idx is None
            and control.obj_type == SECURITY_OBJECT
            and control.prop_id in SECURITY_PROPERTY_WIDTHS
        ):
            # Before the standard widths: PID 56 is the Tool Key here, not the maximum APDU length.
            self._property_widths[key] = SECURITY_PROPERTY_WIDTHS[control.prop_id]
            return
        if control.prop_id in STANDARD_WRITE_WIDTHS:
            return
        if (
            control.obj_idx is None
            and control.obj_type == IP_PARAMETER_OBJECT
            and control.prop_id in IP_PROPERTY_WIDTHS
        ):
            self._property_widths[key] = IP_PROPERTY_WIDTHS[control.prop_id]
            return
        for prop in self.image.properties:
            if (
                prop.property_id == control.prop_id
                and self._image_property_applies(prop, index, control)
                and prop.element_size is not None
            ):
                self._property_widths[key] = prop.element_size
                return
        programmer = await self._bus()
        target = self._extended_targets.get(index)
        if target is not None and programmer.max_apdu_length > DEFAULT_MAX_APDU_LENGTH:
            self._property_widths[
                key
            ] = await programmer.read_property_ext_element_size(
                target[0], target[1], control.prop_id
            )
            return
        self._property_widths[key] = await programmer.read_property_element_size(
            await self._object_index(index), control.prop_id
        )

    async def _write_prop(self, control: LdCtrlWriteProp) -> None:
        """Write addressed image elements and verify each property response."""
        index = await self._resolve_index(control, extended=True)
        await self._ensure_property_width(index, control)
        data = self._property_write_data(index, control)
        programmer = await self._bus()
        tool_key = (
            control.obj_idx is None
            and control.obj_type == SECURITY_OBJECT
            and control.prop_id == PID_TOOL_KEY
        )
        if tool_key and not await self._announce_tool_key(data):
            return
        if await self._extended_fits(index, control.prop_id):
            object_type, instance = self._extended_targets[index]
            await programmer.write_property_ext(
                object_type,
                instance,
                control.prop_id,
                data,
                count=control.count,
                start_index=control.start_element,
                verify=control.verify,
            )
            return
        await programmer.write_property(
            await self._object_index(index),
            control.prop_id,
            data,
            count=control.count,
            start_index=control.start_element,
            # The Tool Key is write-only: its response carries no elements (3/5/1 6.3.10).
            verify=control.verify and not tool_key,
        )

    async def _announce_tool_key(self, key: bytes) -> bool:
        """Prepare the secured connection for a Tool Key write; ``False`` when the device already
        uses ``key``, so there is nothing to write."""
        if getattr(self._manager, "active_key", None) == key:
            logger.info("download: %s already uses its Tool Key", self._target())
            return False
        change = getattr(self._manager, "change_tool_key", None)
        if change is None:
            raise DownloadError(
                "the Tool Key can only be written over a secured connection"
            )
        await self._initialize_sequence_number()
        change(key)
        logger.info("download: writing the Tool Key of %s", self._target())
        return True

    async def _initialize_sequence_number(self) -> None:
        """On the initial secure configuration (the device does not use its Tool Key yet), set a
        Sequence Number Sending below the current timestamp to it, and warn about one close to
        its end (3/5/3 1.5.4.1)."""
        from .data_secure import sequence_number_timestamp

        current = getattr(self._manager, "device_sequence_number", None)
        if not isinstance(current, int):
            return
        if current > SEQUENCE_NUMBER_WARNING:
            logger.warning(
                "download: the Sequence Number of %s is close to its end (%#014x)",
                self._target(),
                current,
            )
        now = sequence_number_timestamp()
        if current >= now:
            return
        logger.info("download: initialising the Sequence Number of %s", self._target())
        await self._write_prop(sequence_number_control(now))

    async def _load_image_prop(self, control: LdCtrlLoadImageProp) -> None:
        """Capture property elements for subsequent image-backed comparisons.

        Capture the wire bytes without converting their width or byte order.
        Repeated captures fill only missing elements. Allocation and writes must not replace this
        baseline: the par procedure uses it to detect a moved table reference.
        """
        # see .references/ets_map.md
        index = await self._resolve_index(control, extended=True)
        data = await self._read_property(index, control)
        logger.debug(
            "property capture: %s object-index=%d pid=%d count=%d start-index=%d "
            "octets=%d data=%s",
            self._diagnostic_context(),
            index,
            control.prop_id,
            control.count,
            control.start_element,
            len(data),
            data.hex(),
        )
        if not data or len(data) % control.count:
            raise VerificationError(
                f"property capture for object {index} property {control.prop_id}: "
                f"received {len(data)} octet(s) for {control.count} element(s)"
            )
        width = len(data) // control.count
        if (
            control.prop_id == PID_TABLE_REFERENCE
            and control.start_element > 0
            and width not in (2, 4)
        ):
            raise VerificationError(
                f"invalid table reference capture for object {index}: "
                f"expected 2 or 4 octets per element, received {width}"
            )
        for offset in range(control.count):
            key = (index, control.prop_id, control.start_element + offset)
            element = data[offset * width : (offset + 1) * width]
            previous = self._property_captures.get(key)
            if previous is not None and len(previous) != width:
                raise VerificationError(
                    f"property capture width changed for object {index} "
                    f"property {control.prop_id} element {key[2]}"
                )
            self._property_captures.setdefault(key, element)

    def _property_compare_data(
        self, index: int, control: LdCtrlCompareProp
    ) -> tuple[bytes, int | None, str]:
        """Overlay runtime and supplied image bytes onto the inline placeholder.

        Supplied image bytes take precedence over captured bytes, except for
        PID_TABLE_REFERENCE, whose value is allocated by the device. PropertyValue
        images begin at element one; element zero is a separate count resource.
        The returned width is authoritative only when an element was captured.
        """
        expected = bytearray(control.inline_data)
        captured = [
            self._property_captures.get((index, control.prop_id, element))
            for element in range(
                control.start_element, control.start_element + control.count
            )
        ]
        widths = {len(data) for data in captured if data is not None}
        if len(widths) > 1:
            raise VerificationError(
                f"inconsistent captured element widths for object {index} "
                f"property {control.prop_id}"
            )
        captured_width = next(iter(widths), None)
        width = captured_width or (
            len(expected) // control.count if control.count else 0
        )
        sources = ["inline"]
        if captured_width is not None:
            expected.extend(b"\x00" * max(0, width * control.count - len(expected)))
            for offset, data in enumerate(captured):
                if data is not None:
                    expected[offset * width : (offset + 1) * width] = data
            sources.append("captured")
        if (
            control.start_element > 0
            and width
            and control.prop_id != PID_TABLE_REFERENCE
        ):
            for prop in self.image.properties:
                if prop.property_id == control.prop_id and self._image_property_applies(
                    prop, index, control
                ):
                    start = (control.start_element - 1) * width
                    data = prop.data[
                        start : start + min(len(expected), width * control.count)
                    ]
                    if data:
                        expected[: len(data)] = data
                        sources.append("image")
                    break
        return bytes(expected), captured_width, "+".join(sources)

    def _memory_compare_data(
        self, control: LdCtrlCompareMem | LdCtrlCompareRelMem, index: int | None = None
    ) -> bytes:
        if isinstance(control, LdCtrlCompareRelMem):
            address = control.offset
            runs = self._relative_runs(
                LdCtrlWriteRelMem(
                    obj_idx=control.obj_idx,
                    obj_type=control.obj_type,
                    occurrence=control.occurrence,
                    offset=address,
                    size=control.size,
                    verify=False,
                ),
                index=index,
                allow_missing=True,
            )
        else:
            address = control.address
            runs = self._memory_runs(address, control.size, control.address_space)
        values = {
            address + n: value
            for n, value in enumerate(control.inline_data[: control.size])
        }
        for start, data in runs or []:
            values.update({start + n: value for n, value in enumerate(data)})
        if any(address + n not in values for n in range(control.size)):
            raise ImageError("incomplete memory comparison image")
        return bytes(values[address + n] for n in range(control.size))

    async def _compare_prop(self, control: LdCtrlCompareProp) -> None:
        """Read a property and compare it against expected data.

        PID_TABLE_REFERENCE is a runtime descriptor: compare it only against a
        preceding LoadImageProp capture, never a static inline/image value.

        The compare is mask driven when the control carries a ``mask`` (only the
        marked bits of each octet have to match, e.g. the application-number bytes
        of the application id while manufacturer and version are ignored). The
        device's property length is authoritative: a procedure often carries the
        property's maximum element size padded with trailing zeros while the device
        reports only its actual length, so only the overlapping prefix is compared - but only when
        the octets past the response are genuinely padding (zero, or excluded by the mask). A
        response that cuts off octets the compare was meant to check is a failure, not a shorter
        property: otherwise a truncated reply would pass this gate on a prefix.
        """
        index = await self._resolve_index(control, extended=True)
        expected, captured_width, source = self._property_compare_data(index, control)
        read_back = await self._read_property(index, control)
        logger.debug(
            "property compare: %s object-index=%d pid=%d count=%d start-index=%d "
            "source=%s captured-element-width=%s inline=%s expected=%s read=%s mask=%s",
            self._diagnostic_context(),
            index,
            control.prop_id,
            control.count,
            control.start_element,
            source,
            captured_width,
            control.inline_data.hex(),
            expected.hex(),
            read_back.hex(),
            control.mask.hex() if control.mask is not None else "none",
        )
        # A device that returns no data (absent property / rejected read) must
        # not pass the compare vacuously - the overlapping-prefix rule below
        # would otherwise match against an empty prefix.
        if not read_back:
            raise VerificationError(
                f"property compare for object {index} property {control.prop_id} "
                "read no data from the device"
            )
        if (
            captured_width is not None
            and len(read_back) != captured_width * control.count
        ):
            raise VerificationError(
                f"property compare for object {index} property {control.prop_id}: "
                f"device returned {len(read_back)} octet(s), captured range requires "
                f"{captured_width * control.count}; expected {expected.hex()} "
                f"read {read_back.hex()}"
            )

        if control.prop_id == PID_TABLE_REFERENCE and control.start_element > 0:
            if not control.count or len(read_back) not in (
                2 * control.count,
                4 * control.count,
            ):
                raise VerificationError(
                    f"invalid table reference for object {index}: "
                    f"expected 2 or 4 octets per element, received {len(read_back)} "
                    f"for {control.count} element(s)"
                )
            # The MV-07B0 ap1 capture reads 00003804 from object 4 PID7
            # despite the application's 00000000 placeholder. Only a prior
            # runtime capture provides a baseline (e.g. par detects relocation).
            if captured_width is None:
                logger.debug(
                    "runtime table reference: %s object-index=%d pid=%d data=%s; "
                    "no captured baseline, skipping static compare",
                    self._diagnostic_context(),
                    index,
                    control.prop_id,
                    read_back.hex(),
                )
                return
        mask = control.mask
        if mask is not None and len(mask) < len(expected):
            # An omitted mask suffix checks every bit; only the supplied prefix
            # limits the comparison.
            # see .references/ets_map.md
            mask += b"\xff" * (len(expected) - len(mask))
        # Shortening the compare to the overlap is only safe when the octets the response does not
        # cover carry nothing the compare was meant to check - i.e. the procedure's own trailing
        # padding, or octets the mask excludes anyway. Otherwise a truncated response would satisfy
        # the gate on a prefix: a device answering one octet of a five octet application id would
        # pass the very check that exists to stop us programming the wrong device.
        uncovered = _significant_octets(expected, mask, start=len(read_back))
        if uncovered:
            raise VerificationError(
                f"property compare for object {index} property {control.prop_id}: "
                f"device returned {len(read_back)} octet(s) but {len(expected)} were expected "
                f"and {uncovered} octet(s) beyond the response still carry data to check. "
                f"Expected {expected.hex()}, read {read_back.hex()}"
            )
        length = min(len(read_back), len(expected))
        matched = _bytes_match(read_back, expected, mask, length)
        logger.debug(
            "property compare result: %s object-index=%d pid=%d -> %s",
            self._diagnostic_context(),
            index,
            control.prop_id,
            "match" if matched else "MISMATCH",
        )
        if not matched:
            raise CompareMismatch(
                f"property compare failed for object {index} property "
                f"{control.prop_id}: expected {expected.hex()} "
                f"read {read_back.hex()}",
                object_index=index,
                property_id=control.prop_id,
                expected=expected,
                read=read_back,
                mask=mask,
            )

    async def _abs_segment(self, control: LdCtrlAbsSegment) -> None:
        """Allocate an absolute segment, then write the image data for its range.

        A procedure may carry no explicit memory-write controls (property based
        System B products): the download image is written into the segments the
        procedure allocates. So after allocating the segment we write the image
        slice that covers it - the segment address and size match an image
        segment exactly. Segments the image does not cover are only allocated.
        """
        segment_type = load_state.SegmentType(control.seg_type)
        index = await self._resolve_index(control)
        programmer = await self._bus()
        await programmer.send_load_event(
            index,
            load_state.alloc_absolute_segment(
                segment_type,
                control.address,
                control.size,
                access_attributes=control.access,
                memory_type=control.mem_type,
                memory_attributes=control.seg_flags,
            ),
            load_state.LoadState.LOADING,
        )
        # Per the KNX Load Controls (KNX Standard v3.0.0, 2/3/1) an AbsSegment
        # allocation is followed by a verified memory write of the segment data -
        # but only the bytes the image actually produced (its mask). Bytes the
        # encoder did not write stay at their current device value.
        runs = (
            self._memory_runs(control.address, control.size)
            if self._enable_segment_write
            else []
        )
        if runs:
            programmer = await self._memory_programmer()
            for address, data in runs:
                await programmer.write_memory(address, data, verify=True)

    async def _task_segment(self, control: LdCtrlTaskSegment) -> None:
        """Allocate the task segment via a load event."""
        index = await self._resolve_index(control)
        programmer = await self._bus()
        await programmer.send_load_event(
            index,
            load_state.alloc_task_segment(
                control.address,
                self.application.program.pei_type,
                _application_id(self.application),
            ),
            load_state.LoadState.LOADING,
        )

    async def _preflight_control(
        self,
        control: object,
        segments: list[SegmentDiff],
        properties: list[PropertyDiff],
    ) -> None:
        """Read-only preview of a single control (see :meth:`preflight`).

        Controls with a write effect are previewed by reading current bytes and
        recording a diff. Property-image captures and compare gates still run.
        Load state events, restart, delays and other read-back controls have no
        write to preview and are ignored.
        """
        if isinstance(control, LdCtrlConnect):
            await self._bus()
            return
        if isinstance(control, LdCtrlSetControlVariable):
            await self._execute(control)
            return
        if isinstance(control, LdCtrlMapError):
            await self._execute(control)
            return
        if isinstance(control, LdCtrlDeclarePropDesc):
            await self._execute(control)
            return
        if isinstance(control, LdCtrlDisconnect):
            await self._close()
            return
        if isinstance(control, LdCtrlWriteMem):
            _require_standard(control.address_space)
            runs = self._memory_write_runs(control)
            if runs is None:
                raise ImageError(
                    f"no image data for address range {control.address:#06x}"
                )
            for address, planned in runs:
                await self._diff_memory(address, planned, segments)
            return
        if isinstance(control, LdCtrlWriteRelMem):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            base = await programmer.read_table_reference(index)
            await self._diff_masked_rel(base, control, segments, index=index)
            return
        if isinstance(control, LdCtrlAbsSegment):
            # An allocation whose range the image covers is an image backed write;
            # preview only the bytes the image actually writes (its mask). A range the
            # image does NOT cover (a RAM/system segment) is allocated and left unwritten
            # by _abs_segment, so it is a clean allocate-only preview here too, not an error.
            if self._enable_segment_write:
                await self._diff_masked(
                    control.address, control.size, segments, missing_ok=True
                )
            return
        if isinstance(control, LdCtrlWriteProp):
            index = await self._resolve_index(control, extended=True)
            await self._ensure_property_width(index, control)
            planned = self._property_write_data(index, control)
            await self._diff_property(
                index,
                control.prop_id,
                control.count,
                control.start_element,
                planned,
                properties,
            )
            return
        if isinstance(control, LdCtrlCompareMem):
            _require_standard(control.address_space)
            await self._compare_mem(
                control.address, self._memory_compare_data(control), control.mask
            )
            return
        if isinstance(control, LdCtrlCompareRelMem):
            index = await self._resolve_index(control)
            programmer = await self._bus()
            base = await programmer.read_table_reference(index)
            await self._compare_mem(
                base + control.offset,
                self._memory_compare_data(control, index),
                control.mask,
            )
            return
        if isinstance(control, LdCtrlCompareProp):
            await self._compare_prop(control)
            return
        if isinstance(control, LdCtrlLoadImageProp):
            await self._load_image_prop(control)
            return
        if isinstance(control, (LdCtrlLoadImageMem, LdCtrlLoadImageRelMem)):
            await self._execute(control)
            return
        # Anything left is either a control with no write to preview (fine) or a
        # step this engine does not implement. Do not fail the read-only preview,
        # but log the gap so a bug report shows the preview was incomplete.
        name = type(control).__name__
        if name not in gaps.PREFLIGHT_NO_WRITE:
            logger.warning(
                "preflight cannot preview %s [%s]: %s",
                name,
                self._target(),
                gaps.describe_missing(name),
            )

    async def _diff_memory(
        self, address: int, planned: bytes, segments: list[SegmentDiff]
    ) -> None:
        """Read current memory at ``address`` and record a diff against ``planned``."""
        programmer = await self._bus()
        current = await programmer.read_memory(address, len(planned))
        segments.append(
            SegmentDiff(address=address, current=current, planned=bytes(planned))
        )

    async def _diff_masked(
        self,
        address: int,
        size: int,
        segments: list[SegmentDiff],
        *,
        missing_ok: bool = False,
    ) -> None:
        """Diff each masked write run the image would apply within ``[address, size)``.

        ``masked_writes`` returns ``None`` when no segment covers the range at all (the programming
        data is missing) and an empty list when a segment covers it but writes nothing there. Those
        mean opposite things, so they must not be collapsed: :meth:`_write_mem` raises ``ImageError``
        on ``None``, and a preflight that quietly recorded nothing would report a clean, no-change
        preview for a download that cannot run.

        ``missing_ok`` mirrors the caller's write path: an ``LdCtrlWriteMem`` write raises on missing
        data, but an ``LdCtrlAbsSegment`` allocation only writes the image slice *if there is one*
        (:meth:`_abs_segment` uses ``if runs:``) - a RAM/system segment the image never fills is
        allocated and left unwritten, not an error. So the AbsSegment diff passes ``missing_ok=True``
        and a ``None`` there is a clean, allocate-only preview, exactly as the download behaves.
        """
        runs = self._memory_runs(address, size)
        if runs is None:
            if missing_ok:
                return
            raise ImageError(
                f"no image data for address range {address:#06x}..{address + size:#06x}"
            )
        if not runs:
            return
        programmer = await self._bus()
        for run_address, planned in runs:
            current = await programmer.read_memory(run_address, len(planned))
            segments.append(
                SegmentDiff(
                    address=run_address, current=current, planned=bytes(planned)
                )
            )

    async def _diff_masked_rel(
        self,
        base: int,
        control: LdCtrlWriteRelMem,
        segments: list[SegmentDiff],
        *,
        index: int | None = None,
    ) -> None:
        """Diff a relative segment: image at ``offset``, device at ``base + run``.

        Mirrors :meth:`_write_rel_mem` - the image holds the segment in its own
        relative address space, and the device address adds the run-time table base.
        ``None`` (no image data) is an error there, so it must be one here too; see
        :meth:`_diff_masked`.
        """
        runs = self._memory_write_runs(control, index=index)
        if runs is None:
            raise ImageError(
                f"no image data for relative range {control.offset:#06x}.."
                f"{control.offset + control.size:#06x}"
            )
        if not runs:
            return
        programmer = await self._bus()
        for run_offset, planned in runs:
            current = await programmer.read_memory(base + run_offset, len(planned))
            segments.append(
                SegmentDiff(
                    address=base + run_offset,
                    current=current,
                    planned=bytes(planned),
                )
            )

    async def _diff_property(
        self,
        object_index: int,
        property_id: int,
        count: int,
        start_element: int,
        planned: bytes,
        properties: list[PropertyDiff],
    ) -> None:
        """Read the current property value and record a diff against ``planned``."""
        programmer = await self._bus()
        width = len(planned) // count
        current = bytearray()
        target = (
            self._extended_targets.get(object_index)
            if await self._extended_fits(object_index, property_id)
            else None
        )
        per_frame = (
            programmer.extended_property_chunk_size(width)
            if target is not None
            else programmer.property_chunk_size(width)
        )
        device_index = (
            object_index
            if target is not None
            else await self._object_index(object_index)
        )
        for offset in range(0, count, per_frame):
            frame_count = min(per_frame, count - offset)
            current.extend(
                await programmer.read_property_ext(
                    target[0],
                    target[1],
                    property_id,
                    count=frame_count,
                    start_index=start_element + offset,
                )
                if target is not None
                else await programmer.read_property(
                    device_index,
                    property_id,
                    count=frame_count,
                    start_index=start_element + offset,
                )
            )
        properties.append(
            PropertyDiff(
                object_index=device_index,
                property_id=property_id,
                current=bytes(current),
                planned=bytes(planned),
                object_type=target[0] if target is not None else None,
            )
        )


# Memory address spaces this engine writes as a flat absolute A_Memory access. STANDARD is the
# normal device memory; LC_FILTER (a BCU1 coupler's filter-table region) and LC_SLAVE (its
# slave-side BCU config region) both live in ordinary EEPROM addressed absolutely and are written by
# the same A_Memory_Write (KNX Standard v3.0.0, 3/5/1 Resources; both are plain memory, distinct
# from properties/relative memory). USER memory needs the A_UserMemory services and is not
# implemented.
_ABSOLUTE_MEMORY_SPACES = frozenset(
    {
        LdCtrlMemAddrSpace.STANDARD,
        LdCtrlMemAddrSpace.LC_FILTER,
        LdCtrlMemAddrSpace.LC_SLAVE,
    }
)


def _require_standard(address_space: LdCtrlMemAddrSpace) -> None:
    """Reject memory operations outside the flat absolute address spaces (STANDARD/LcFilter/LcSlave)."""
    if address_space not in _ABSOLUTE_MEMORY_SPACES:
        message = (
            f"memory address space {address_space.value!r} is not implemented; only the standard, "
            f"LcFilter and LcSlave (all flat absolute A_Memory) address spaces are handled (KNX "
            f"Standard v3.0.0, 3/5/1 Resources). User memory needs the A_UserMemory services; "
            f"please file a bug report with the product data so it can be added."
        )
        logger.warning("unsupported load control: %s", message)
        raise UnsupportedProcedureError(message)
