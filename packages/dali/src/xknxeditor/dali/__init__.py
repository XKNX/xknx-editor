"""MDT DALI gateway (GC16/Hawk) bus commissioning over KNX.

Reverse-engineered from the MDT DALI DCA. Layers on ``xknxeditor.download``'s property access to scan
the DALI bus, identify ballasts, download group/ECG assignment and write scenes — the commissioning
parts normally handled by a separate DCA tool. Manufacturer-specific (MDT); verify against real hardware.
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
from xknxeditor.dali.properties import (
    DeviceCommand,
    DeviceExtCommand,
    PropertyType,
    object_index,
)

# Neutral aliases: the implementation classes are named Mdt* for history, but serve every Type-A
# gateway family (MDT, Theben). Prefer these names in new code.
DaliCommissioner = MdtDaliCommissioner
DaliConnection = MdtDaliConnection

__all__ = [
    "PROFILES",
    "DaliBusState",
    "DaliCommissioner",
    "DaliCommissioningError",
    "DaliConnection",
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
    "object_index",
    "profile_for",
]
