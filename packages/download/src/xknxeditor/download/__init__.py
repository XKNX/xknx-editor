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
from .download import DownloadResult, download, preflight
from .errors import (
    AddressInUseError,
    DownloadError,
    ImageError,
    InsufficientApduLength,
    LoadStateError,
    ProgrammingModeError,
    SerialNumberNotFoundError,
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
from .function_property import install as _install_function_property
from .image import (
    DownloadImage,
    GroupCommunication,
    MemorySegment,
    PropertyValue,
    build_image,
    group_table_bits,
    relative_segment_fills,
    written_properties,
)
from .ip_config import IpDeviceConfig
from .ip_secure import IpSecureConfig
from .load_state import LoadEvent, LoadState
from .merge import resolve_download_controls
from .preflight import (
    ByteRange,
    PreflightReport,
    PropertyDiff,
    SegmentDiff,
)
from .procedure import LoadProcedureRunner
from .programmer import (
    DEFAULT_MAX_APDU_LENGTH,
    ApduRestriction,
    ConnectionManager,
    DeviceProgrammer,
    apdu_restriction,
    negotiated_apdu_length,
)
from .project_data import (
    GroupObjectLink,
    SeedDevice,
    group_address_table,
    group_communication_from_device,
    module_instances_from_device,
    parameter_instance_refs_from_device,
    parameter_values_from_device,
)
from .response_matching import install as _install_response_matching
from .scope import DownloadScope
from .secure_keyring import device_security_from_keyring, load_device_security
from .tables import (
    Association,
    build_association_table,
    build_group_address_table,
)

# Every point-to-point connection of the editor takes only the response that answers a
# request, also the ones xknx's own procedures open.
_install_response_matching()
_install_function_property()

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_MAX_APDU_LENGTH",
    "FILTER_TABLE_SIZE",
    "AddressInUseError",
    "ApduRestriction",
    "Association",
    "ByteRange",
    "ConnectionManager",
    "DeviceProgrammer",
    "DeviceSecurity",
    "DownloadError",
    "DownloadImage",
    "DownloadResult",
    "DownloadScope",
    "GroupCommunication",
    "GroupObjectLink",
    "ImageError",
    "InsufficientApduLength",
    "IpDeviceConfig",
    "IpSecureConfig",
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
    "SerialNumberNotFoundError",
    "UnsupportedProcedureError",
    "VerificationError",
    "addresses_in_filter_table",
    "apdu_restriction",
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
    "group_table_bits",
    "is_coupler_address",
    "load_device_security",
    "module_instances_from_device",
    "negotiated_apdu_length",
    "parameter_instance_refs_from_device",
    "parameter_values_from_device",
    "preflight",
    "program_individual_address",
    "relative_segment_fills",
    "resolve_download_controls",
    "routed_group_addresses",
    "switch_programming_mode",
    "written_properties",
]
