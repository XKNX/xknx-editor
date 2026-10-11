"""Download applications into KNX devices.

The package interprets the Load Procedure of a parsed application program and
executes it over a running ``xknx`` connection: driving each loadable part's
Load State Machine and writing the assembled download image via memory and
property services.
"""

from __future__ import annotations

from .commissioning import (
    download_individual_address,
    program_individual_address,
    switch_programming_mode,
)
from .data_secure import DeviceSecurity, SecureProgrammingError
from .download import download, preflight
from .errors import (
    AddressInUseError,
    DownloadError,
    ImageError,
    LoadStateError,
    ProgrammingModeError,
    UnsupportedProcedureError,
    VerificationError,
)
from .filter_table import (
    FILTER_TABLE_SIZE,
    addresses_in_filter_table,
    build_filter_table,
    compute_coupler_filter_table,
    is_coupler_address,
    routed_group_addresses,
)
from .image import (
    DownloadImage,
    GroupCommunication,
    MemorySegment,
    PropertyValue,
    build_image,
)
from .load_state import LoadEvent, LoadState
from .merge import resolve_download_controls
from .preflight import (
    ByteRange,
    PreflightReport,
    PropertyDiff,
    SegmentDiff,
)
from .procedure import LoadProcedureRunner
from .programmer import ConnectionManager, DeviceProgrammer
from .project_data import (
    GroupObjectLink,
    SeedDevice,
    group_address_table,
    group_communication_from_device,
    module_instances_from_device,
    parameter_instance_refs_from_device,
    parameter_values_from_device,
)
from .scope import DownloadScope
from .secure_keyring import device_security_from_keyring, load_device_security
from .tables import (
    Association,
    build_association_table,
    build_group_address_table,
)

__version__ = "0.1.0"

__all__ = [
    "FILTER_TABLE_SIZE",
    "AddressInUseError",
    "Association",
    "ByteRange",
    "ConnectionManager",
    "DeviceProgrammer",
    "DeviceSecurity",
    "DownloadError",
    "DownloadImage",
    "DownloadScope",
    "GroupCommunication",
    "GroupObjectLink",
    "ImageError",
    "LoadEvent",
    "LoadProcedureRunner",
    "LoadState",
    "LoadStateError",
    "MemorySegment",
    "PreflightReport",
    "ProgrammingModeError",
    "PropertyDiff",
    "PropertyValue",
    "SecureProgrammingError",
    "SeedDevice",
    "SegmentDiff",
    "UnsupportedProcedureError",
    "VerificationError",
    "addresses_in_filter_table",
    "build_association_table",
    "build_filter_table",
    "build_group_address_table",
    "build_image",
    "compute_coupler_filter_table",
    "device_security_from_keyring",
    "download",
    "download_individual_address",
    "group_address_table",
    "group_communication_from_device",
    "is_coupler_address",
    "load_device_security",
    "module_instances_from_device",
    "parameter_instance_refs_from_device",
    "parameter_values_from_device",
    "preflight",
    "program_individual_address",
    "resolve_download_controls",
    "routed_group_addresses",
    "switch_programming_mode",
]
