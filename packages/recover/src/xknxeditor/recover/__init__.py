"""Reconstruct a KNX project by reading devices back from the bus.

This is the inverse of :mod:`xknxeditor.download`: instead of writing an
application into a device, it discovers devices on a line, identifies the
installed application, reads the configured memory/properties and decodes them
back into project data (group addresses, links, parameter values). All bus
access is read-only.
"""

from __future__ import annotations

from .dossier import DeviceDossier, read_dossier
from .errors import RecoverError
from .identify import (
    AppId,
    ProductLookup,
    match_application,
    parse_application_id,
    read_application_id,
)
from .parameters import RecoveredParameters, RecoveryEffort, recover_parameters
from .read_config import (
    RawConfiguration,
    RawGroupCommunication,
    read_group_communication,
    read_parameter_memory,
)
from .recover import (
    RecoveredDevice,
    com_object_ref_by_number,
    identify_device_at,
    recover_device,
    recover_device_at,
    search_again,
    seed_dynamic_ui,
)
from .scan import (
    DiscoveredDevice,
    iter_addresses,
    probe_and_identify,
    probe_device,
    scan_bus,
)
from .snapshot import device_snapshot, devices_from_snapshot, snapshots_json
from .tables_decode import (
    DecodedGroupObject,
    DecodedLink,
    TableDecodeError,
    decode_association_table,
    decode_association_table_b,
    decode_com_object_table,
    decode_group_address_table,
    decode_group_address_table_b,
    decode_group_object_table_b,
    decode_short_com_object_table,
)
from .validate import LinkWarning, validate_group_communication
from .verify import build_group_communication, verify_recovered

__version__ = "0.1.0"

__all__ = [
    "AppId",
    "DecodedGroupObject",
    "DecodedLink",
    "DeviceDossier",
    "DiscoveredDevice",
    "LinkWarning",
    "ProductLookup",
    "RawConfiguration",
    "RawGroupCommunication",
    "RecoverError",
    "RecoveredDevice",
    "RecoveredParameters",
    "RecoveryEffort",
    "TableDecodeError",
    "build_group_communication",
    "com_object_ref_by_number",
    "decode_association_table",
    "decode_association_table_b",
    "decode_com_object_table",
    "decode_group_address_table",
    "decode_group_address_table_b",
    "decode_group_object_table_b",
    "decode_short_com_object_table",
    "device_snapshot",
    "devices_from_snapshot",
    "identify_device_at",
    "iter_addresses",
    "match_application",
    "parse_application_id",
    "probe_and_identify",
    "probe_device",
    "read_application_id",
    "read_dossier",
    "read_group_communication",
    "read_parameter_memory",
    "recover_device",
    "recover_device_at",
    "recover_parameters",
    "scan_bus",
    "search_again",
    "seed_dynamic_ui",
    "snapshots_json",
    "validate_group_communication",
    "verify_recovered",
]
