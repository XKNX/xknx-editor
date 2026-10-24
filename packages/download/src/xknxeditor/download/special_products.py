"""Applications whose devices need a different handling than the standard one.

An entry is an application id prefix: it applies to every application id that starts with it.
"""

from __future__ import annotations

# Applications whose devices take a Master Reset with erase code 5 (reset parameters) or
# 6 (reset links), channel 0, as a Function Property Command to their first application object,
# property 52, instead of A_Restart: see ``LEGACY_MASTER_RESET_FUNCTIONS``.
LEGACY_MASTER_RESET_APPLICATIONS = (
    "M-0009_A-20AA-10",
    "M-0009_A-20AB-10",
    "M-0009_A-20AC-10",
    "M-0009_A-20AD-10",
    "M-0009_A-20AE-10",
    "M-0009_A-20AF-10",
    "M-0009_A-20B0-10",
    "M-0009_A-20B1-10",
    "M-0009_A-20B2-10",
    "M-0009_A-20B3-10",
    "M-0009_A-20B4-10",
    "M-0009_A-20B5-11",
    "M-0009_A-20B6-10",
    "M-0009_A-20B8-10",
    "M-0009_A-20B9-10",
    "M-0009_A-20BC-10",
    "M-0009_A-5000-01",
    "M-0009_A-5004-01",
    "M-0009_A-5005-01",
    "M-0009_A-5006-01",
    "M-0009_A-5007-01",
    "M-0009_A-5008-01",
    "M-0009_A-5009-01",
    "M-0009_A-500A-01",
    "M-0009_A-500B-01",
    "M-0009_A-500C-01",
    "M-0009_A-500D-01",
    "M-0009_A-500D-20",
    "M-0009_A-500E-01",
    "M-0009_A-500E-20",
    "M-0009_A-500F-01",
    "M-0009_A-5010-01",
    "M-0009_A-5014-10",
    "M-0009_A-5017-10",
    "M-0009_A-5018-10",
    "M-0009_A-5019-10",
    "M-0009_A-501A-10",
    "M-0009_A-501B-10",
    "M-0009_A-501C-10",
    "M-0009_A-E0A1-",
    "M-0009_A-E0A2-",
    "M-0009_A-E0CD-",
    "M-0009_A-E0CE-",
    "M-0006_A-E00D-01",
    "M-0006_A-E00E-01",
    "M-0006_A-E00F-01",
    "M-0006_A-E010-01",
    "M-0006_A-E01A-01",
    "M-0006_A-E01B-01",
    "M-0006_A-E01C-01",
    "M-0006_A-E01D-01",
    "M-0006_A-E01E-01",
    "M-0006_A-E01F-01",
    "M-0006_A-E01F-20",
    "M-0006_A-E020-01",
    "M-0006_A-E020-20",
    "M-0006_A-E021-01",
    "M-0006_A-E022-01",
    "M-0006_A-E023-01",
    "M-0006_A-E024-10",
    "M-0006_A-E025-10",
    "M-0006_A-E026-10",
    "M-0006_A-E027-10",
    "M-0006_A-E028-10",
    "M-0006_A-E029-10",
    "M-0006_A-E02A-10",
    "M-0006_A-E02B-10",
    "M-0006_A-E02C-10",
    "M-0006_A-E02D-10",
    "M-0006_A-E030-10",
    "M-0006_A-E031-10",
    "M-0006_A-E034-10",
    "M-0006_A-E036-10",
    "M-0006_A-E037-10",
    "M-0006_A-E038-11",
    "M-0006_A-E039-10",
    "M-0006_A-E098-10",
    "M-0006_A-E099-10",
    "M-0006_A-E09A-10",
    "M-0006_A-E09D-10",
    "M-0006_A-E09E-10",
    "M-0006_A-E09F-10",
    "M-0006_A-E0A1-",
    "M-0006_A-E0A2-",
    "M-0006_A-E0CD-",
    "M-0006_A-E0CE-",
    "M-006C_A-0305-",
)

# The property and the function code, by erase code, of such a Master Reset.
LEGACY_MASTER_RESET_PROPERTY = 52
LEGACY_MASTER_RESET_FUNCTIONS = {5: 0x10, 6: 0x04}

# How long (seconds) the device gets after that command before the next load control.
LEGACY_MASTER_RESET_DELAY = 0.2


def legacy_master_reset(application_id: str) -> bool:
    """Whether devices of ``application_id`` take a Master Reset as a Function Property
    Command."""
    return application_id.startswith(LEGACY_MASTER_RESET_APPLICATIONS)


def _listed(application_id: str | None, entries: tuple[str, ...]) -> bool:
    return bool(application_id) and str(application_id).startswith(entries)


# Applications whose devices do not report their serial number reliably: it is not read after
# an individual address download.
NEVER_READ_SERIAL_NUMBER = (
    "M-004E_A-0002-11",
    "M-004E_A-0003-10",
    "M-004E_A-0003-20",
    "M-004E_A-0020-10",
    "M-004E_A-0020-20",
    "M-004E_A-0030-10",
    "M-004E_A-0101-13",
    "M-004E_A-1001-20",
)

# Applications whose devices need 10 s after a restart before they answer again.
WAIT_EXTRA_AFTER_RESET = (
    "M-00FD_A-7001",
    "M-00FD_A-7101",
    "M-00FD_A-1101",
    "M-00FD_A-1102",
    "M-00FD_A-1103",
    "M-00FD_A-1104",
    "M-00FD_A-1105",
    "M-007E_A-0002",
    "M-007E_A-0003",
)
EXTRA_RESET_WAIT = 10.0

# Applications whose devices take at most 12 octets per memory service, whatever their APDU.
FORCE_SHORT_MEMORY_SERVICES = (
    "M-0083_A-0064",
    "M-0083_A-0065",
    "M-0083_A-0066",
    "M-0083_A-0067",
    "M-0083_A-006A",
    "M-0083_A-006B",
)
SHORT_MEMORY_SERVICE_COUNT = 12

# Applications whose group address and association tables stay at their nominal place and size,
# even though the application declares dynamic table management.
DO_NOT_USE_DYNAMIC_GROUP_COMMUNICATION_TABLES = (
    "M-0004_A-9019-01",
    "M-00A6_A-0004-10",
)

# Applications whose load procedure is run with all its controls (see
# ``merge.filter_application_controls``).
DO_NOT_FILTER_LOAD_CONTROLS = (
    "M-0001_A-8018-04",
    "M-0001_A-8018-06",
    "M-0001_A-8018-08",
    "M-0001_A-8020-01",
    "M-0001_A-8020-02",
    "M-0001_A-8020-03",
    "M-0001_A-8023-11",
)

# Applications (BCU 2) whose group object table keeps the value pointers of the device: they are
# not written by a download that writes only what changed.
DO_NOT_MAKE_GROUP_OBJECT_TABLE_POINTERS_VISIBLE = (
    "M-00FD_A-0006-10",
    "M-00FD_A-0001-11",
    "M-00FD_A-0001-1B",
    "M-00FD_A-0003-10",
    "M-00FD_A-0007-10",
    "M-00FD_A-0008-10",
    "M-00FD_A-0009-10",
    "M-00FD_A-000A-10",
    "M-00FD_A-000B-10",
    "M-00FD_A-000C-10",
)

# Download plugins (GUIDs) whose devices are identified again after every reconnect.
DESCRIPTOR_READ_ON_RECONNECT_PLUGINS = (
    "CC18BBDF-C882-4DD2-80FC-EC0C62428F85",
    "A1D70116-ADBD-431B-8F8D-D2722BFB8127",
)

# Manufacturer ids some devices report instead of their own.
_DEVICE_MANUFACTURER_IDS = {0xABB2: 0x0002, 0xABB7: 0x0007}


def never_read_serial_number(application_id: str | None) -> bool:
    """Whether the serial number of a device of ``application_id`` is not read."""
    return _listed(application_id, NEVER_READ_SERIAL_NUMBER)


def restart_wait(application_id: str | None, wait: float) -> float:
    """How long to wait after a restart of a device of ``application_id`` before connecting
    again, ``wait`` being the standard time."""
    if _listed(application_id, WAIT_EXTRA_AFTER_RESET):
        return max(wait, EXTRA_RESET_WAIT)
    return wait


def memory_service_count(application_id: str | None, count: int | None) -> int | None:
    """The most octets per memory service for a device of ``application_id``, ``count`` being
    the standard limit (``None``: none)."""
    if _listed(application_id, FORCE_SHORT_MEMORY_SERVICES):
        return min(count or SHORT_MEMORY_SERVICE_COUNT, SHORT_MEMORY_SERVICE_COUNT)
    return count


def dynamic_group_communication_tables(application_id: str | None, declared: bool) -> bool:
    """Whether the group communication tables of ``application_id`` are placed dynamically,
    ``declared`` being the application's ``DynamicTableManagement``."""
    return declared and not _listed(
        application_id, DO_NOT_USE_DYNAMIC_GROUP_COMMUNICATION_TABLES
    )


def filter_load_controls(application_id: str | None) -> bool:
    """Whether the load controls of ``application_id`` are filtered."""
    return not _listed(application_id, DO_NOT_FILTER_LOAD_CONTROLS)


def group_object_table_pointers_visible(application_id: str | None) -> bool:
    """Whether a BCU 2 group object table writes the value pointers with the table."""
    return not _listed(application_id, DO_NOT_MAKE_GROUP_OBJECT_TABLE_POINTERS_VISIBLE)


def descriptor_read_on_reconnect(download_plugin: str | None) -> bool:
    """Whether a device whose application has the download plugin ``download_plugin`` is
    identified again after every reconnect."""
    if not download_plugin:
        return False
    return download_plugin.strip("{}").upper() in DESCRIPTOR_READ_ON_RECONNECT_PLUGINS


def device_manufacturer_id(reported: int) -> int:
    """The manufacturer id of a device that reports ``reported``."""
    return _DEVICE_MANUFACTURER_IDS.get(reported, reported)
