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
Chapter 3/5/1 "Resources", section 4.23) when the mask's ``PropertyMappedLsms``
feature is set. That includes MV-0705, which also lists a standard-memory
``LoadControl_M112`` address; ETS still writes the property. Masks that leave the
feature unset (BIM M112 MV-0701) write the 11-octet record at the shared control
address and poll the matching LoadStatus byte.

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
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknx.profile.const import ResourceGenericPropertyId, ResourceObjectType

from xknxeditor.namespaces.intermediate.hawk_configuration_data_t_features_feature_name import (
    HawkConfigurationDataFeaturesFeatureName as MaskFeature,
)
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
from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName

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
from .factory_image import FactoryLocation, FactorySegment, FactoryStamp, PropertyStamp
from .group_communication import materialize_group_communication_controls
from .ip_config import IP_PARAMETER_OBJECT, IP_PROPERTY_WIDTHS
from .merge import device_mask_version, resolve_download_controls
from .preflight import PreflightReport, PropertyDiff, SegmentDiff
from .programmer import (
    DEFAULT_MAX_APDU_LENGTH,
    PID_TABLE_REFERENCE,
    DeviceProgrammer,
    negotiated_apdu_length,
)
from .property_layout import STANDARD_WRITE_WIDTHS, property_width
from .resources import (
    bundled_mask_resources,
    property_group_tables,
    property_mapped_lsms,
)
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
from .tables_system300 import (
    ADDRESS_TABLE,
    ASSOCIATION_TABLE,
    GROUP_OBJECT_ELEMENT_SIZE,
    GROUP_OBJECT_REFERENCE_SIZE,
    GROUP_OBJECT_REFERENCES,
    GROUP_OBJECT_TABLE,
    PropertyTables,
)

if TYPE_CHECKING:
    from xknxeditor.prod import Application

    from .image import DownloadImage, PackedGroupTables, PropertyValue
    from .programmer import ConnectionManager

logger = logging.getLogger(__name__)

# Default cooldown (seconds) to wait after a Restart before reconnecting.
DEFAULT_RESTART_COOLDOWN = 3.0

# Cooldown (seconds) after a Master Reset the device did not confirm: a device has to report its
# Process Time only when it needs longer than this (KNX 3/5/2 section 3.7.1.2.2).
UNCONFIRMED_MASTER_RESET_COOLDOWN = 5.0

# Memory Control Block table property and its per-segment entry layout (KNX
# Standard v3.0.0, Chapter 3/5/1 "Resources", section 4.2.27 "PID_MCB_TABLE"):
# an 8 octet entry per segment, CRC-protected when bit 0 of octet 4 is clear, with
# the segment CRC in octets 6..7 (big-endian).
_PID_MCB_TABLE = ResourceGenericPropertyId.PID_MCB_TABLE
_MCB_ENTRY_SIZE = 8
# The memory type bits of an EEPROM segment, the one an allocation writes its data into.
_EEPROM_MEMORY = 0x03

# After a Master Reset that resets them, a device showing its factory stamp holds the factory
# image: of everything but the group object table after erase code 5 (parameters) or 7 (factory
# reset without the individual address), of the group object table after 6 (links) or 7.
_FACTORY_OTHER = "other"
_FACTORY_TABLE = "group object table"
_FACTORY_ERASE_CODES = {_FACTORY_OTHER: (5, 7), _FACTORY_TABLE: (6, 7)}
# A run of bytes that already hold the factory contents inside a write is left out only from
# this length on (and never longer than the frame budget): shorter gaps cost more frames than
# they save.
_FACTORY_MIN_GAP = 31

# The Router object's relative memory holds the group-address filter table (System B), carried
# as the image's dedicated filter-table field.
_ROUTER_OBJECT_TYPE = ResourceObjectType.OBJECT_ROUTER

# Top bit of a load-procedure error value (the Original/Mapped fields of
# LdCtrlMapError): set means failure, clear means success. Mapping an error to a
# value with this bit clear turns a rejected access into a tolerated one. These
# 32-bit values and the flag convention are the tool's load-procedure
# representation, not a bus error code.
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


_RANGE = re.compile(r"([\[(])(-?\d+)?,(-?\d+)?([\])])([su]?)")


def _value_in_range(text: str, read_back: bytes, mask: bytes | None) -> bool:
    """Whether ``read_back`` (masked) lies in the compare's ``Range``.

    The range is an interval as the product data writes it: ``[`` / ``]`` include a bound,
    ``(`` / ``)`` exclude it, a missing bound is open, and an ``s`` suffix reads the data as a
    signed number (``u`` or none: unsigned). The data is read most significant octet first."""
    match = _RANGE.fullmatch(text.strip())
    if match is None:
        raise UnsupportedProcedureError(f"invalid compare range {text!r}")
    opening, low, high, closing, kind = match.groups()
    data = bytes(read_back)
    if mask:
        mask = mask.ljust(len(data), b"\xff")
        data = bytes(octet & mask[i] for i, octet in enumerate(data))
    value = int.from_bytes(data, "big", signed=kind == "s")
    if low is not None and (value < int(low) or (opening == "(" and value == int(low))):
        return False
    return not (
        high is not None
        and (value > int(high) or (closing == ")" and value == int(high)))
    )


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

    Plain (optionally masked) equality compares are executed, and a property compare with a Range.
    The Invert and RetryInterval/TimeOut semantics (KNX Standard v3.0.0, 3/5/1 Compare controls),
    and a Range on other compares, are NOT, so a control that sets any of them is rejected up front
    rather than silently verified with the wrong result (Invert would invert the outcome; Range/Retry
    change what "match" means)."""
    if not isinstance(control, LdCtrlCompareBase):
        return None
    unsupported: list[str] = []
    if control.invert:
        unsupported.append("Invert (compare passes on inequality)")
    if control.range is not None and not isinstance(control, LdCtrlCompareProp):
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


# Applications whose devices fall back to the standard memory services when an extended one
# fails (by application id prefix).
_EXTENDED_MEMORY_FALLBACK = ("M-0071",)
# Applications whose devices take at most 12 octets per standard memory service: manufacturer
# and application numbers.
_SHORT_MEMORY_SERVICES = {"M-0083": frozenset({100, 101, 102, 103, 106, 107})}
_SHORT_MEMORY_SERVICE_COUNT = 12


@dataclass(frozen=True)
class MemoryServices:
    """How a device's memory is read and written, from its application: with the extended
    memory services (A_MemoryExtended_Read/Write) when it takes them, falling back to the
    standard ones when an extended one fails (``fallback``), and with at most ``max_count``
    octets per standard memory service (None: as the APDU allows)."""

    extended: bool = False
    fallback: bool = False
    max_count: int | None = None

    def cap(self, count: int | None) -> int | None:
        """A standard memory service count (None: as the APDU allows) within ``max_count``."""
        if self.max_count is None:
            return count
        return min(count or self.max_count, self.max_count)

    def apply(self, programmer: DeviceProgrammer) -> None:
        """Set up a programmer of a new connection."""
        programmer.extended_memory = self.extended
        programmer.extended_memory_fallback = self.fallback
        if self.max_count is not None:
            programmer.max_memory_service_count = min(
                programmer.max_memory_service_count, self.max_count
            )


def memory_services(application: Application) -> MemoryServices:
    """The memory services of a device of ``application``: extended when the application says
    so or is a secure one; the fallback and the short standard services for the applications
    that need them."""
    program = getattr(application, "program", None)
    options = getattr(getattr(program, "static", None), "options", None)
    extended = bool(
        getattr(options, "supports_extended_memory_services", False)
        or getattr(program, "is_secure_enabled", False)
    )
    app_id = getattr(application, "id", "") or getattr(program, "id", "") or ""
    manufacturer = getattr(application, "manufacturer_id", "") or app_id[:6]
    short = getattr(program, "application_number", None) in _SHORT_MEMORY_SERVICES.get(
        manufacturer, frozenset()
    )
    return MemoryServices(
        extended=extended,
        fallback=app_id.startswith(_EXTENDED_MEMORY_FALLBACK),
        max_count=_SHORT_MEMORY_SERVICE_COUNT if short else None,
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
        application_loaded: bool = False,
        application2: Application | None = None,
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

        ``application_loaded`` runs a full procedure on a device that already has
        the application loaded (``PreferPartialDownloadIfApplicationLoaded``): the
        segments' code seed is not written, only the tables and the parameters.

        ``application2`` is the product's second application program: its id is the one
        written to the second program's object.
        """
        if programmer is None and connection_manager is None:
            raise DownloadError("provide a programmer or a connection manager")
        self.application = application
        self.application2 = application2
        self.image = image
        self.scope = scope
        self.application_loaded = application_loaded
        self._object_types = dict(object_types or {})
        self._active_control: object | None = None
        # LoadImageProp fills missing runtime-image data; later CompareProp
        # controls overlay it onto InlineData. Key by resolved object, PID and element so
        # index/type addressing and overlapping ranges refer to the same bytes.
        # Keep this separate from the caller's immutable download image.
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
        # fallback); None keeps the programmer's APDU-derived default. Devices that take only
        # short memory services never get more.
        self._memory_services = memory_services(application)
        self._memory_service_count = self._memory_services.cap(memory_service_count)
        # (legacy control filter, original error) -> mapped error; 0 is wildcard.
        self._error_mappings: dict[tuple[int, int], int] = {}
        self._controls = (
            list(controls)
            if controls is not None
            else resolve_download_controls(
                application, scope=scope, parameter_values=image.procedure_parameters
            )
        )
        # Position of the control currently being executed, for diagnostics.
        self._position: tuple[int, int] | None = None
        # Memoized result of _packed_group_tables(): every AbsSegment and task
        # control on a packed mask asks for the same placement, computed from
        # the (unchanging) application and image for this run.
        self._packed_group_tables_computed = False
        self._packed_group_tables_cache: PackedGroupTables | None = None
        # Absolute addresses a preflight found the device rewriting at runtime.
        self._device_written: set[int] = set()
        # The factory image segments a Master Reset left in place (see _register_factory):
        # _FACTORY_TABLE for the group object table, _FACTORY_OTHER for the rest.
        self._factory_registered: set[str] = set()
        # The properties a preflight previews as procedure steps (see _procedure_steps).
        self._procedure_steps: set[_PropertyKey] = set()

    async def run(self, progress: Callable[[int, int], None] | None = None) -> None:
        """Execute the Load Procedure, honouring the selected download scope.

        ``progress`` (optional) is called ``progress(done, total)`` after each executed
        control, where ``total`` is the number of in-scope controls, so a UI can show
        download progress.
        """
        in_scope = materialize_group_communication_controls(
            self.image,
            self._controls,
            self._object_types,
            self.scope,
            property_tables=self._property_tables(),
        )
        self._prevalidate(in_scope)
        self._property_captures.clear()
        self._memory_captures.clear()
        self._factory_registered.clear()
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
        self._device_written = set()
        in_scope = materialize_group_communication_controls(
            self.image,
            self._controls,
            self._object_types,
            self.scope,
            property_tables=self._property_tables(),
        )
        self._prevalidate(in_scope)
        self._property_captures.clear()
        self._memory_captures.clear()
        self._factory_registered.clear()
        self._procedure_steps = _procedure_steps(in_scope)
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
        return PreflightReport(
            segments=tuple(segments),
            properties=tuple(properties),
            device_written=frozenset(self._device_written),
        )

    def _in_scope(self, control: object) -> bool:
        """Whether a control participates in the requested download scope."""
        return control_in_scope(control, self.scope, self._object_types)

    def _memory_runs(
        self,
        address: int,
        size: int,
        space: LdCtrlMemAddrSpace = LdCtrlMemAddrSpace.STANDARD,
        *,
        include_initialized: bool | None = None,
    ) -> list[tuple[int, bytes]] | None:
        if include_initialized is None:
            include_initialized = self._writes_seed()
        runs = self.image.masked_writes(
            address,
            size,
            include_initialized=include_initialized,
            partial=not self._writes_seed(),
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
        if control.inline_data is not None:
            inline = _inline_memory_data(control.inline_data, control.size)
            runs = [(address, self._overlay_inline_memory(address, inline, runs))]
        return self._leave_factory_memory(control, index, runs)

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
        # Per connection: a fallback turns the extended services off until the next one.
        self._memory_services.apply(self._programmer)
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
                    self._negotiated_apdu = negotiated_apdu_length(
                        device_max, self._max_apdu_length
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

    def _memory_mapped_mask(self) -> bool:
        """Whether this mask's product procedure places tables through absolute
        segments it reallocates on almost every load.

        True when the mask publishes a memory-mapped load control: MV-0700,
        MV-0701, MV-0705 and MV-5705. A partial download's parameter mask alone
        can then leave a reallocated segment empty or stale, so an AbsSegment
        write flushes the full code seed even outside a full or application
        download. MV-2705 is property-mapped M112 and publishes no memory
        load-control address, so this returns false. With dynamic table
        management it still packs its association table
        (``AssociationTable_M112``). MV-2705, System B and
        BCU2 keep the scope-based rule (:meth:`_memory_runs`'s default): only a
        full/application download writes the seed, a parameter download touches
        only the changed bytes.
        """
        resources = bundled_mask_resources(device_mask_version(self.application))
        return resources is not None and resources.memory_mapped_lsm() is not None

    def _writes_seed(self, *, segment: bool = False) -> bool:
        """Whether memory writes carry the code seed (the segments' initialized bytes).

        A full or application download writes it, an AbsSegment of a memory-mapped
        mask (``segment``) in any scope (see :meth:`_memory_mapped_mask`). With
        ``application_loaded`` the device holds the seed already: only the tables
        and the parameters are written.
        """
        if self.application_loaded:
            return False
        if segment and self._memory_mapped_mask():
            return True
        return self.scope in (DownloadScope.FULL, DownloadScope.APPLICATION)

    def _memory_lsm(self, index: int) -> tuple[int, int | None] | None:
        """Return ``(control_address, status_address)`` for a memory-mapped LSM.

        BIM M112 shares one load-control address. ``status_address`` is ``None``
        when the mask has no LoadStatus resource for ``index`` (PEI on MV-0701);
        the control write still goes through memory, without a status poll.
        Property-mapped masks (MV-0705 included; it also lists the memory
        address) and LSM index 0 return ``None``.
        """
        if index < 1:
            return None
        if property_mapped_lsms(device_mask_version(self.application)):
            return None
        resources = bundled_mask_resources(device_mask_version(self.application))
        if resources is None:
            return None
        mapped = resources.memory_mapped_lsm()
        if mapped is None:
            return None
        control, _status_lsm1 = mapped
        return control, resources.memory_mapped_lsm_status(index)

    def _packed_group_tables(self) -> PackedGroupTables | None:
        """ETS placement of the address and association tables, if this mask packs them.

        Memoized: every ``AbsSegment`` and task-segment control on a packed mask
        asks for the same placement, computed from this run's unchanging
        application and image.
        """
        if self._packed_group_tables_computed:
            return self._packed_group_tables_cache
        self._packed_group_tables_computed = True
        self._packed_group_tables_cache = self._compute_packed_group_tables()
        return self._packed_group_tables_cache

    def _compute_packed_group_tables(self) -> PackedGroupTables | None:
        from .image import packed_group_tables

        static = getattr(self.application.program, "static", None)
        address_table = getattr(static, "address_table", None)
        code = getattr(static, "code", None)
        if address_table is None or code is None or address_table.code_segment is None:
            return None
        segments = {segment.id: segment for segment in code.absolute_segment}
        address_segment = segments.get(address_table.code_segment)
        if address_segment is None:
            return None
        base = address_segment.address + (address_table.offset or 0)
        # The address table the image built: its count and entries are written, the two
        # own-address octets after the count are left to individual-address programming.
        tables = [
            segment
            for segment in self.image.segments
            if segment.address == base
            and segment.mask is not None
            and segment.mask[:1] == b"\xff"
            and all(segment.mask[3:])
        ]
        if not tables:
            return None
        table = min(tables, key=lambda segment: len(segment.data))
        return packed_group_tables(self.application, len(table.data))

    def _segment_placement(self, control: LdCtrlAbsSegment) -> tuple[int, int]:
        """Address and length allocated for an absolute segment.

        A property-mapped mask declares the address table and association table
        at full capacity. The allocation uses the encoded address table and
        starts the association table on the next octet. A BCU2 download that places
        the tables allocates the address table (load state machine 1) at its length,
        plus the checksum octet of a checksummed segment, and moves the start of the
        association table's segment (load state machine 2).
        """
        address, size = control.address, control.size
        moved = getattr(self.image, "moved_tables", None)
        if moved is not None and control.lsm_idx == 1:
            checksum = 1 if control.seg_flags & 0x80 else 0
            return address, moved.address_length + checksum
        if moved is not None and control.lsm_idx == 2:
            return (
                moved.association_address,
                address + size - moved.association_address,
            )
        packed = self._packed_group_tables()
        if packed is None:
            return address, size
        if address == packed.address_address and size == packed.declared_address_size:
            return address, packed.address_length
        if (
            address == packed.declared_association_address
            and size == packed.declared_association_size
        ):
            return packed.association_address, packed.association_length
        return address, size

    def _task_address(self, control: LdCtrlTaskSegment) -> int:
        """Task-segment address, moved with the association table when the download
        packs or places it."""
        address = control.address
        moved = getattr(self.image, "moved_tables", None)
        if moved is not None and control.lsm_idx == 2:
            return moved.association_address
        packed = self._packed_group_tables()
        if packed is not None and address == packed.declared_association_address:
            return packed.association_address
        return address

    async def _emit_load_event(
        self,
        index: int,
        event: bytes,
        expected: load_state.LoadState,
        *,
        also_accept: load_state.LoadState | None = None,
    ) -> None:
        """Write a load event through the mask's LSM realisation."""
        resources = bundled_mask_resources(device_mask_version(self.application))
        if resources is not None and resources.omits_load_event(index):
            return
        programmer = await self._bus()
        mapped = self._memory_lsm(index)
        if mapped is not None:
            control_addr, status_addr = mapped
            await programmer.send_memory_load_event(
                index,
                control_addr,
                status_addr,
                event,
                expected,
                also_accept=also_accept,
            )
            return
        await programmer.send_load_event(
            index, event, expected, also_accept=also_accept
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
            await self._emit_load_event(
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
            # KNX 3/5/2 3.7.1.2.2), so use it directly - not milliseconds. A device
            # that restarted without confirming the reset reported none.
            if process_time is None:
                process_time = UNCONFIRMED_MASTER_RESET_COOLDOWN
            self._pending_cooldown = max(self._restart_cooldown, process_time)
            await self._register_factory(control.erase_code)
            return
        if isinstance(control, LdCtrlUnload):
            index = await self._resolve_index(control)
            await self._emit_load_event(
                index, load_state.unload(), load_state.LoadState.UNLOADED
            )
            return
        if isinstance(control, LdCtrlLoad):
            index = await self._resolve_index(control)
            # Some present objects (DIMinBOX, issue #24) keep reporting LOADED
            # after accepting START_LOADING. Allow that result for this control;
            # allocation events and LoadCompleted still verify their own states.
            await self._emit_load_event(
                index,
                load_state.start_loading(),
                load_state.LoadState.LOADING,
                also_accept=load_state.LoadState.LOADED,
            )
            return
        if isinstance(control, LdCtrlLoadCompleted):
            index = await self._resolve_index(control)
            await self._emit_load_event(
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
            await self._emit_load_event(
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
            await self._emit_load_event(
                index,
                load_state.task_pointer(
                    control.init_ptr, control.save_ptr, control.serial_ptr
                ),
                load_state.LoadState.LOADING,
            )
            return
        if isinstance(control, LdCtrlTaskCtrl1):
            index = await self._resolve_index(control)
            await self._emit_load_event(
                index,
                load_state.task_control_1(control.address, control.count),
                load_state.LoadState.LOADING,
            )
            return
        if isinstance(control, LdCtrlTaskCtrl2):
            index = await self._resolve_index(control)
            await self._emit_load_event(
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
        if (
            self._property_services
            and self.application.program.mask_version == "MV-07B0"
        ):
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
                include_initialized=self._writes_seed(),
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
            include_initialized=self._writes_seed(),
            partial=not self._writes_seed(),
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

    def _property_write_plan(
        self, index: int, control: LdCtrlWriteProp
    ) -> tuple[bytes, bytes | None]:
        """The data ``control`` writes and the bits of it to write, ``None`` for all of it.

        Image elements are laid over inline data, keeping device-owned CRCs zero. Every
        download, a full one too, writes only the bits the inline data, captured elements and
        parameters cover, and the device keeps the rest.
        """
        if control.start_element == 0:
            # Element 0 of an array property is its number of valid elements, 2 octets; writing
            # 0 clears the array.
            return (control.inline_data or b"").ljust(2 * control.count, b"\x00")[
                : 2 * control.count
            ], None
        object_type = (
            control.obj_type
            if control.obj_idx is None and control.obj_type is not None
            else self._object_types.get(index)
        )
        if control.prop_id == 13 and object_type in (3, 4):
            if control.start_element != 1 or control.count != 1:
                raise ImageError("application identity requires element 1, count 1")
            # The second program's object (type 4) holds that program's id.
            if object_type == 4 and self.application2 is not None:
                return _application_id(self.application2), None
            return _application_id(self.application), None
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
        data, covered = self._property_image_data(
            index, object_type, control, width, size
        )
        inline = control.inline_data or b""
        if len(inline) >= size:
            # Inline data that fills the write is the property's data where nothing in the
            # image covers it, and the whole property is written. Shorter inline data is not
            # used.
            data = bytearray(
                (byte & ~bits) | (image & bits)
                for byte, image, bits in zip(inline[:size], data, covered, strict=True)
            )
            covered = bytearray(b"\xff" * size)
        if control.prop_id != _PID_MCB_TABLE:
            data, covered = self._leave_factory_property(
                index, object_type, control, width, data, covered
            )
        if control.prop_id == _PID_MCB_TABLE:
            data = data[: control.count * _MCB_ENTRY_SIZE]
            if len(data) != control.count * _MCB_ENTRY_SIZE:
                raise ImageError("incomplete MCB entry")
            entries = bytearray(data)
            for start in range(0, len(entries), _MCB_ENTRY_SIZE):
                entries[start + 6 : start + 8] = b"\x00\x00"
            return bytes(entries), None
        if all(bits == 0xFF for bits in covered):
            return bytes(data), None
        return bytes(data), bytes(covered)

    def _property_image_data(
        self,
        index: int,
        object_type: int | None,
        control: LdCtrlWriteProp,
        width: int,
        size: int,
    ) -> tuple[bytearray, bytearray]:
        """The data the image holds for the elements ``control`` writes, and the bits of it
        something covers (captured elements, parameters)."""
        start = (control.start_element - 1) * width
        # The property's base: what the device leaves the factory with, else zeros. The
        # parameters' bits are laid over it; bytes no parameter writes keep it.
        base = self._factory_property(index, object_type, control)
        data = bytearray((base or b"")[start : start + size].ljust(size, b"\x00"))
        covered = bytearray(size)
        for n in range(control.count):
            captured = self._property_captures.get(
                (index, control.prop_id, control.start_element + n)
            )
            if captured is not None:
                if len(captured) != width:
                    raise ImageError("captured property element width mismatch")
                data[n * width : (n + 1) * width] = captured
                covered[n * width : (n + 1) * width] = b"\xff" * width
        for prop in self.image.properties:
            if prop.property_id == control.prop_id and self._image_property_applies(
                prop, index, control
            ):
                overlay = prop.data[start : start + size]
                mask = (
                    prop.mask[start : start + size]
                    if prop.mask is not None
                    else b"\xff" * len(overlay)
                )
                for i, (value, bits) in enumerate(zip(overlay, mask, strict=False)):
                    data[i] = (data[i] & ~bits) | (value & bits)
                cover = (
                    prop.covered[start : start + size]
                    if prop.covered is not None
                    else b"\xff" * len(overlay)
                )
                for i, bits in enumerate(cover):
                    covered[i] |= bits
                break
        tables = self.image.property_tables
        if tables is not None and self._property_tables():
            self._lay_table(tables, (object_type, control.prop_id), control, width)(
                data, covered
            )
        return data, covered

    def _property_tables(self) -> bool:
        """Whether the device holds its group communication tables as properties."""
        if (
            getattr(getattr(self.application, "program", None), "mask_version", None)
            is None
        ):
            return False
        return property_group_tables(device_mask_version(self.application))

    def _lay_table(
        self,
        tables: PropertyTables,
        target: tuple[int | None, int],
        control: LdCtrlWriteProp,
        width: int,
    ) -> Callable[[bytearray, bytearray], None]:
        """How the table ``control`` writes (object type, PID) is laid over the data the image
        holds for it: the group addresses and the links replace it; the group objects' flags
        go into octet 1 of each element, and a download with parameters also writes the octets
        no parameter covers (as zeros), one without them leaves them to the device."""
        start = (control.start_element - 1) * width

        def replace_all(table: bytes) -> Callable[[bytearray, bytearray], None]:
            def apply(data: bytearray, covered: bytearray) -> None:
                data[:] = table[start : start + len(data)].ljust(len(data), b"\x00")
                covered[:] = b"\xff" * len(covered)

            return apply

        if target == ADDRESS_TABLE:
            if width != 2:
                raise ImageError(
                    f"group address table elements of {width} octets are not supported"
                )
            return replace_all(tables.address_data)
        if target == ASSOCIATION_TABLE:
            return replace_all(tables.association_data(width))
        if target == GROUP_OBJECT_TABLE:
            parameters = self.scope in (
                DownloadScope.FULL,
                DownloadScope.APPLICATION,
                DownloadScope.PARAMETERS,
                DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
            )

            def objects(data: bytearray, covered: bytearray) -> None:
                covered[:] = (b"\xff" if parameters else b"\x00") * len(covered)
                tables.lay_object_flags(data, covered, control.start_element)

            return objects
        return lambda data, covered: None

    def _factory_property(
        self,
        index: int,
        object_type: int | None,
        control: LdCtrlWriteProp,
    ) -> bytes | None:
        """The factory contents of the property ``control`` writes (from element 1), if the
        product has a factory image holding it."""
        factory = self.image.factory_image
        if factory is None:
            return None
        resources = bundled_mask_resources(device_mask_version(self.application))
        first_app_object = (
            resources.feature(MaskFeature.FIRST_APP_OBJECT_IDX)
            if resources is not None
            else None
        )
        return factory.property_data(
            index,
            object_type,
            control.occurrence,
            control.prop_id,
            first_app_object,
        )

    async def _register_factory(self, erase_code: int) -> None:
        """After a Master Reset with ``erase_code``, take the device to hold its factory image
        where that code resets it, if it shows the product's factory stamp (or the product has
        none). Writes of what the factory image already holds are then left out."""
        factory = self.image.factory_image
        if factory is None:
            return
        kinds = {
            kind for kind, codes in _FACTORY_ERASE_CODES.items() if erase_code in codes
        }
        if not kinds or kinds <= self._factory_registered:
            return
        if factory.stamp is not None and not await self._stamp_matches(factory.stamp):
            logger.warning(
                "download: %s does not show its factory stamp after the Master Reset with "
                "erase code %d; the factory contents are written too",
                self._target(),
                erase_code,
            )
            return
        self._factory_registered |= kinds
        logger.info(
            "download: %s holds its factory image (%s) after the Master Reset with erase "
            "code %d; writes of it are left out",
            self._target(),
            ", ".join(sorted(kinds)),
            erase_code,
        )

    async def _stamp_matches(self, stamp: FactoryStamp) -> bool:
        """Whether the device shows ``stamp``: its property values, or the CRCs in the memory
        control blocks of its objects."""
        programmer = await self._bus()
        try:
            for prop in stamp.properties:
                index = await self._stamp_object(prop)
                if index is None:
                    return False
                width = await programmer.read_property_element_size(
                    index, prop.property_id
                )
                offset = prop.address & 0xFFFFFF
                if width <= 0 or len(prop.data) % width or offset % width:
                    return False
                value = await programmer.read_property(
                    index,
                    prop.property_id,
                    count=len(prop.data) // width,
                    start_index=offset // width + 1,
                )
                if value != prop.data:
                    return False
            for obj in stamp.objects:
                if not obj.segments:
                    continue
                blocks = await programmer.read_property(
                    obj.object_index,
                    _PID_MCB_TABLE,
                    count=len(obj.segments),
                    start_index=1,
                )
                if len(blocks) != _MCB_ENTRY_SIZE * len(obj.segments):
                    return False
                for n, (counts, crc) in enumerate(obj.segments):
                    block = blocks[n * _MCB_ENTRY_SIZE : (n + 1) * _MCB_ENTRY_SIZE]
                    if (not block[4] & 1) != counts or (
                        counts and int.from_bytes(block[6:8], "big") != crc
                    ):
                        return False
        except (PropertyAccessRejected, VerificationError) as error:
            logger.info(
                "download: factory stamp of %s not readable: %s", self._target(), error
            )
            return False
        return True

    async def _stamp_object(self, prop: PropertyStamp) -> int | None:
        """The index of the object a stamped property belongs to."""
        if prop.location is FactoryLocation.PROPERTY:
            return prop.object_ref
        if prop.location is FactoryLocation.APP_PROPERTY:
            resources = bundled_mask_resources(device_mask_version(self.application))
            first = (
                resources.feature(MaskFeature.FIRST_APP_OBJECT_IDX)
                if resources is not None
                else None
            )
            return None if first is None else first + prop.object_ref
        if prop.location is FactoryLocation.PROPERTY_BY_TYPE:
            programmer = await self._bus()
            return await programmer.locate_object(prop.object_ref, prop.address >> 24)
        return None

    def _factory_gap(self) -> int:
        """From which length a run of factory contents inside a write is left out."""
        apdu = self._negotiated_apdu or self._max_apdu_length
        return max(1, min(_FACTORY_MIN_GAP, apdu - 7))

    def _leave_factory_property(
        self,
        index: int,
        object_type: int | None,
        control: LdCtrlWriteProp,
        width: int,
        data: bytearray,
        covered: bytearray,
    ) -> tuple[bytearray, bytearray]:
        """``data`` and the bits of it to write once a Master Reset left the factory image in
        place: nothing where every covered byte already holds it, otherwise the property in one
        piece, partly covered bytes and short gaps completed from the factory contents."""
        if _FACTORY_OTHER not in self._factory_registered:
            return data, covered
        base = self._factory_property(index, object_type, control)
        start = (control.start_element - 1) * width
        size = len(data)
        if base is None or len(base) < start + size:
            return data, covered
        factory = base[start : start + size]
        if all(
            not bits or byte == held
            for byte, held, bits in zip(data, factory, covered, strict=True)
        ):
            logger.info(
                "download: object %d property %d of %s holds its factory contents; "
                "not written",
                index,
                control.prop_id,
                self._target(),
            )
            return data, bytearray(size)
        data, covered = bytearray(data), bytearray(covered)
        for i, bits in enumerate(covered):
            if bits and bits != 0xFF:
                data[i] = (data[i] & bits) | (factory[i] & ~bits & 0xFF)
                covered[i] = 0xFF
        if width > 1:
            # Whole elements: an element partly written gets the rest from the factory.
            for element in range(0, size, width):
                part = range(element, min(element + width, size))
                if any(covered[i] for i in part):
                    for i in part:
                        if not covered[i]:
                            data[i], covered[i] = factory[i], 0xFF
            return data, covered
        gap, i = self._factory_gap(), 0
        while i < size:
            if covered[i]:
                i += 1
                continue
            end = i
            while end < size and not covered[end]:
                end += 1
            # A short gap before more written bytes is filled; a long or trailing one is not.
            if end < size and end - i < gap:
                data[i:end] = factory[i:end]
                covered[i:end] = b"\xff" * (end - i)
            i = end
        return data, covered

    def _leave_factory_memory(
        self,
        control: LdCtrlWriteMem | LdCtrlWriteRelMem,
        index: int | None,
        runs: list[tuple[int, bytes]] | None,
    ) -> list[tuple[int, bytes]] | None:
        """``runs`` without the runs that only write what a Master Reset left in place."""
        if not runs or not self._factory_registered:
            return runs
        references = self._factory_memory(control, index)
        if not references:
            return runs
        start = min(address for address, _ in runs)
        end = max(address + len(data) for address, data in runs)
        buffer, written = bytearray(end - start), bytearray(end - start)
        for address, data in runs:
            buffer[address - start : address - start + len(data)] = data
            written[address - start : address - start + len(data)] = b"\x01" * len(data)
        for held_at, held in references:
            lo, hi = max(start, held_at), min(end, held_at + len(held))
            if lo < hi:
                _leave_factory_runs(
                    buffer,
                    written,
                    lo - start,
                    held[lo - held_at : hi - held_at],
                    self._factory_gap(),
                )
        result: list[tuple[int, bytes]] = []
        i = 0
        while i < len(written):
            if not written[i]:
                i += 1
                continue
            run_end = i
            while run_end < len(written) and written[run_end]:
                run_end += 1
            result.append((start + i, bytes(buffer[i:run_end])))
            i = run_end
        if len(result) != len(runs) or sum(len(d) for _, d in result) != sum(
            len(d) for _, d in runs
        ):
            logger.info(
                "download: %s holds part of %s's memory as it left the factory; "
                "those bytes are not written",
                self._target(),
                type(control).__name__,
            )
        return result

    def _factory_memory(
        self, control: LdCtrlWriteMem | LdCtrlWriteRelMem, index: int | None
    ) -> list[tuple[int, bytes]]:
        """The registered factory contents a memory write covers, as ``(address, data)`` in the
        write's own addresses (relative to the object's table for a WriteRelMem)."""
        factory = self.image.factory_image
        if factory is None:
            return []
        relative = isinstance(control, LdCtrlWriteRelMem)
        found: list[tuple[int, bytes]] = []
        for segment in factory.segments:
            if segment.data is None:
                continue
            if relative:
                if index is None or not self._factory_relative_hit(segment, index):
                    continue
            elif segment.location is not FactoryLocation.MEMORY:
                continue
            kind = (
                _FACTORY_TABLE
                if self._factory_group_object_table(segment)
                else _FACTORY_OTHER
            )
            if kind in self._factory_registered:
                found.append((segment.offset, segment.data))
        return found

    def _factory_relative_hit(self, segment: FactorySegment, index: int) -> bool:
        """Whether a relative factory segment belongs to the object at ``index``."""
        if segment.location is FactoryLocation.RELATIVE_MEMORY:
            return segment.object_ref == index
        if segment.location is FactoryLocation.RELATIVE_MEMORY_BY_TYPE:
            object_type = self._object_types.get(index)
            if object_type != segment.object_ref:
                return False
            same_type = sorted(
                i for i, t in self._object_types.items() if t == object_type
            )
            return same_type.index(index) == segment.occurrence
        return False

    def _factory_group_object_table(self, segment: FactorySegment) -> bool:
        """Whether a factory segment is the group object table: at the mask's location of it,
        or at the application's table address in memory."""
        resources = bundled_mask_resources(device_mask_version(self.application))
        location = (
            resources.location(ResourceName.GROUP_OBJECT_TABLE) if resources else None
        )
        if (
            location is not None
            and location.address_space is ResourceAddrSpace.RELATIVE_MEMORY
            and segment.location is FactoryLocation.RELATIVE_MEMORY
        ):
            return (
                segment.object_ref == location.interface_object_ref
                and segment.property_id == location.property_id
                and segment.address == (location.start_address or 0)
            )
        if segment.location is not FactoryLocation.MEMORY:
            return False
        if (
            location is not None
            and location.address_space is ResourceAddrSpace.STANDARD_MEMORY
            and segment.address == location.start_address
        ):
            return True
        return segment.address == self._group_object_table_address()

    def _group_object_table_address(self) -> int | None:
        """The memory address of the application's group object table, if it has one."""
        static = getattr(self.application.program, "static", None)
        table = getattr(static, "com_object_table", None)
        code = getattr(static, "code", None)
        if table is None or table.code_segment is None or code is None:
            return None
        for segment in code.absolute_segment:
            if segment.id == table.code_segment and segment.address is not None:
                return segment.address + (table.offset or 0)
        return None

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
        if self._property_tables() and isinstance(control, LdCtrlWriteProp):
            object_type = (
                control.obj_type
                if control.obj_idx is None and control.obj_type is not None
                else self._object_types.get(index)
            )
            # The group object table's properties have one property type each.
            known = {
                GROUP_OBJECT_TABLE: GROUP_OBJECT_ELEMENT_SIZE,
                GROUP_OBJECT_REFERENCES: GROUP_OBJECT_REFERENCE_SIZE,
            }.get((object_type, control.prop_id))  # type: ignore[arg-type]
            if known is not None:
                self._property_widths[key] = known
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
        data, covered = self._property_write_plan(index, control)
        if covered is not None:
            if not any(covered):
                logger.info(
                    "download: no parameter of %s covers object %d property %d; not written",
                    self._target(),
                    index,
                    control.prop_id,
                )
                return
            current = await self._read_property_elements(
                index, control.prop_id, control.count, control.start_element, data
            )
            data = _keep_uncovered(current, data, covered)
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
            # Without inline data the compare checks the image's own data at the address.
            for n, value in enumerate((control.inline_data or b"")[: control.size])
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
        if control.range is not None:
            await self._compare_prop_range(index, control, control.range)
            return
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

    async def _compare_prop_range(
        self, index: int, control: LdCtrlCompareProp, value_range: str
    ) -> None:
        """Read a property and check that its value lies in ``value_range`` (e.g. a minimum
        firmware version, ``[2216203124736,]``)."""
        read_back = await self._read_property(index, control)
        if not read_back:
            raise VerificationError(
                f"property compare for object {index} property {control.prop_id} "
                "read no data from the device"
            )
        matched = _value_in_range(value_range, read_back, control.mask)
        logger.debug(
            "property range compare: %s object-index=%d pid=%d range=%s read=%s -> %s",
            self._diagnostic_context(),
            index,
            control.prop_id,
            value_range,
            read_back.hex(),
            "match" if matched else "MISMATCH",
        )
        if not matched:
            raise CompareMismatch(
                f"property compare failed for object {index} property "
                f"{control.prop_id}: read {read_back.hex()} is outside {value_range}",
                object_index=index,
                property_id=control.prop_id,
                read=read_back,
                mask=control.mask,
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
        address, size = self._segment_placement(control)
        # A checksum-less segment whose image holds only the code seed has no
        # parameter bytes. ETS does not allocate it (the Merten 5701 segment at
        # 0x5100 is 25856 bytes, and the device does not ACK that record). The
        # seed stays as it already is on the device. A checksummed segment still
        # allocates, so the code seed is written into the range the device checks.
        if (
            self._memory_lsm(index) is not None
            and self._enable_segment_write
            and not ((control.seg_flags or 0) & 0x80)
            and self._memory_runs(address, size, include_initialized=False) == []
            and self._memory_runs(address, size, include_initialized=True)
        ):
            logger.info(
                "skipping code-only segment without checksum: %s address=%#06x size=%d",
                self._diagnostic_context(),
                address,
                size,
            )
            return
        event = load_state.alloc_absolute_segment(
            segment_type,
            address,
            size,
            access_attributes=control.access,
            memory_type=control.mem_type,
            memory_attributes=control.seg_flags,
        )
        await self._emit_load_event(
            index,
            event,
            load_state.LoadState.LOADING,
        )
        # Per the KNX Load Controls (KNX Standard v3.0.0, 2/3/1) an AbsSegment
        # allocation is followed by a verified memory write of the segment data -
        # but only the bytes the image actually produced (its mask). Bytes the
        # encoder did not write stay at their current device value.
        # A memory-mapped mask (BIM M112) reallocates that range on almost every
        # load, so flush the full seed there even outside a full/application
        # download (a partial download allocates only group communication tables;
        # it writes a loaded program's segments, see scoped_controls). Other masks
        # keep the scope-based default (see :meth:`_memory_runs`), or a parameter
        # download would cost as much bus time as a full one on a large
        # application. A device that
        # has the application loaded keeps its seed (see :meth:`_writes_seed`).
        runs = (
            self._memory_runs(
                address, size, include_initialized=self._writes_seed(segment=True)
            )
            if self._writes_segment_data(control)
            else []
        )
        if runs:
            programmer = await self._memory_programmer()
            for address, data in runs:
                await programmer.write_memory(address, data, verify=True)

    def _writes_segment_data(self, control: LdCtrlAbsSegment) -> bool:
        """Whether the image data of an allocated segment is written: only into EEPROM (memory
        type 3); a RAM segment (1, 2) or one of another type is only allocated."""
        return (
            self._enable_segment_write
            and (control.mem_type or 0) & _EEPROM_MEMORY == _EEPROM_MEMORY
        )

    async def _task_segment(self, control: LdCtrlTaskSegment) -> None:
        """Allocate the task segment via a load event."""
        index = await self._resolve_index(control)
        mapped = self._memory_lsm(index)
        # MV-0701 has no PEI LoadStatus; ETS skips this AllocAbsTask (LSM 5
        # would otherwise remap the group-object table at 0x4400).
        if mapped is not None and mapped[1] is None:
            return
        application_id = _application_id(self.application)
        static = getattr(self.application.program, "static", None)
        options = getattr(static, "options", None) if static is not None else None
        # Only applications that ask for it (LegacyPatchManufacturerIdInTaskSegment)
        # replace the manufacturer with 0x0002. BIM M112 product procedures such as
        # the Berker 8014 keep their own id; ETS writes 0006 E052 12 there.
        patch = (
            options is not None and options.legacy_patch_manufacturer_id_in_task_segment
        )
        if patch:
            application_id = (0x0002).to_bytes(2, "big") + application_id[2:]
        event = load_state.alloc_task_segment(
            self._task_address(control),
            self.application.program.pei_type,
            application_id,
        )
        await self._emit_load_event(
            index,
            event,
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
        if isinstance(control, LdCtrlMasterReset):
            # No reset in a preview: whether the device shows its factory stamp now decides
            # which writes the download would leave out.
            await self._register_factory(control.erase_code)
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
            if self._writes_segment_data(control):
                address, size = self._segment_placement(control)
                await self._diff_masked(
                    address,
                    size,
                    segments,
                    missing_ok=True,
                    include_initialized=self._writes_seed(segment=True),
                )
            return
        if isinstance(control, LdCtrlWriteProp):
            index = await self._resolve_index(control, extended=True)
            await self._ensure_property_width(index, control)
            planned, covered = self._property_write_plan(index, control)
            if covered is not None and not any(covered):
                return
            await self._diff_property(
                index,
                control.prop_id,
                control.count,
                control.start_element,
                planned,
                properties,
                covered=covered,
                procedure_step=_property_key(control) in self._procedure_steps,
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

    async def _note_device_written(
        self,
        programmer: DeviceProgrammer,
        address: int,
        current: bytes,
        planned: bytes,
    ) -> None:
        """Flag changed bytes the device rewrites itself at runtime.

        A changed byte whose device value differs again on a second read is written
        by the firmware, not a real configuration difference. Only the bytes that
        differ from ``planned`` are re-read, so an in-sync device costs no extra I/O.
        """
        changed = [
            i
            for i in range(min(len(current), len(planned)))
            if current[i] != planned[i]
        ]
        if not changed:
            return
        again = await programmer.read_memory(address, len(current))
        for i in changed:
            if i < len(again) and again[i] != current[i]:
                self._device_written.add(address + i)

    async def _diff_memory(
        self, address: int, planned: bytes, segments: list[SegmentDiff]
    ) -> None:
        """Read current memory at ``address`` and record a diff against ``planned``."""
        programmer = await self._bus()
        current = await programmer.read_memory(address, len(planned))
        await self._note_device_written(programmer, address, current, bytes(planned))
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
        include_initialized: bool | None = None,
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
        runs = self._memory_runs(address, size, include_initialized=include_initialized)
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
            await self._note_device_written(
                programmer, run_address, current, bytes(planned)
            )
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
            await self._note_device_written(
                programmer, base + run_offset, current, bytes(planned)
            )
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
        *,
        covered: bytes | None = None,
        procedure_step: bool = False,
    ) -> None:
        """Read the current property value and record a diff against ``planned``, of which
        only the ``covered`` bits are written (``None``: all); ``procedure_step`` marks it as a
        step of the procedure (see :attr:`PropertyDiff.procedure_step`)."""
        current = await self._read_property_elements(
            object_index, property_id, count, start_element, planned
        )
        if covered is not None:
            planned = _keep_uncovered(current, planned, covered)
        target = (
            self._extended_targets.get(object_index)
            if await self._extended_fits(object_index, property_id)
            else None
        )
        properties.append(
            PropertyDiff(
                object_index=(
                    object_index
                    if target is not None
                    else await self._object_index(object_index)
                ),
                property_id=property_id,
                current=current,
                planned=bytes(planned),
                object_type=target[0] if target is not None else None,
                procedure_step=procedure_step,
            )
        )

    async def _read_property_elements(
        self,
        object_index: int,
        property_id: int,
        count: int,
        start_element: int,
        planned: bytes,
    ) -> bytes:
        """The device's current value of the ``count`` elements from ``start_element`` that
        ``planned`` is written to, read in frames that fit one APDU."""
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
        return bytes(current)


# A property as a load control addresses it: object index, object type, occurrence, PID and
# start element.
_PropertyKey = tuple[int | None, int | None, int, int, int]


def _property_key(control: LdCtrlWriteProp) -> _PropertyKey:
    return (
        control.obj_idx,
        control.obj_type,
        control.occurrence,
        control.prop_id,
        control.start_element,
    )


def _procedure_steps(controls: Sequence[object]) -> set[_PropertyKey]:
    """The properties ``controls`` write more than once with different inline data: steps of
    the procedure (a download handshake such as ``01`` at the start and ``02`` before the
    restart), not a setting the device keeps. Parameters give every write of a property the
    same value, so only inline data can differ."""
    values: dict[_PropertyKey, set[bytes]] = {}
    for control in controls:
        if isinstance(control, LdCtrlWriteProp) and control.inline_data is not None:
            values.setdefault(_property_key(control), set()).add(control.inline_data)
    return {key for key, data in values.items() if len(data) > 1}


def _leave_factory_runs(
    data: bytearray, written: bytearray, offset: int, held: bytes, min_run: int
) -> None:
    """Clear ``written`` where ``data`` from ``offset`` on already equals ``held``: at the start
    and the end of the range, and inside it for runs of at least ``min_run`` bytes. A byte not
    written does not interrupt a run."""
    leading, run_start = True, -1
    for i, value in enumerate(held):
        pos = offset + i
        if written[pos] and data[pos] != value:
            leading, run_start = False, -1
            continue
        if leading or min_run <= 1:
            written[pos] = 0
            continue
        if run_start < 0:
            run_start = i
        if i - run_start + 1 >= min_run:
            written[offset + run_start : pos + 1] = bytes(pos + 1 - offset - run_start)
            leading, run_start = True, -1
    if run_start >= 0:
        written[offset + run_start : offset + len(held)] = bytes(len(held) - run_start)


def _keep_uncovered(current: bytes, planned: bytes, covered: bytes) -> bytes:
    """``planned`` with the bits outside ``covered`` taken from the device's ``current`` value
    (a byte the device did not return keeps ``planned``)."""
    merged = bytearray(planned)
    for i, (value, bits) in enumerate(zip(current, covered, strict=False)):
        merged[i] = (value & ~bits & 0xFF) | (planned[i] & bits)
    return bytes(merged)


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
