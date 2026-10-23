"""MDT DALI gateway (GC16/Hawk) bus commissioning over KNX.

Layers on ``xknxeditor.download``'s property access to scan the DALI bus, identify ballasts,
download group/ECG assignment and write scenes - the commissioning parts normally handled by a
separate configuration app. Manufacturer-specific (MDT); verify against real hardware.
"""

from xknxeditor.dali.commissioning import MdtDaliCommissioner
from xknxeditor.dali.connection import (
    DaliCommissioningError,
    MdtDaliConnection,
    Programmer,
)
from xknxeditor.dali.model import (
    DaliBusState,
    EcgAssignment,
    EcgState,
    GroupState,
    Scene,
    SceneValue,
)
from xknxeditor.dali.profiles import PROFILES, DaliProfile, profile_for
from xknxeditor.dali.project_persistence import (
    BACKUP_STORE_BASE_NAME,
    BackupStore,
    backup_store_name,
    build_backup_store_xml,
    load_backup_store,
    merge_commissioning_blob,
    patch_assignments_in_blob,
)
from xknxeditor.dali.properties import (
    DeviceCommand,
    DeviceExtCommand,
    PropertyType,
    object_index,
)
from xknxeditor.dali.siemens import (
    DaliDeviceType,
    SiemensN141BusState,
    SiemensN141EcgRecord,
    is_siemens_n141_03,
    scan_n141_03,
)

# Neutral aliases: the implementation classes are named Mdt* for history, but serve every Type-A
# gateway family (MDT, Theben). Prefer these names in new code.
DaliCommissioner = MdtDaliCommissioner
DaliConnection = MdtDaliConnection

__all__ = [
    "BACKUP_STORE_BASE_NAME",
    "PROFILES",
    "BackupStore",
    "DaliBusState",
    "DaliCommissioner",
    "DaliCommissioningError",
    "DaliConnection",
    "DaliDeviceType",
    "DaliProfile",
    "DeviceCommand",
    "DeviceExtCommand",
    "EcgAssignment",
    "EcgState",
    "GroupState",
    "MdtDaliCommissioner",
    "MdtDaliConnection",
    "Programmer",
    "PropertyType",
    "Scene",
    "SceneValue",
    "SiemensN141BusState",
    "SiemensN141EcgRecord",
    "backup_store_name",
    "build_backup_store_xml",
    "is_siemens_n141_03",
    "load_backup_store",
    "merge_commissioning_blob",
    "object_index",
    "patch_assignments_in_blob",
    "profile_for",
    "scan_n141_03",
]
