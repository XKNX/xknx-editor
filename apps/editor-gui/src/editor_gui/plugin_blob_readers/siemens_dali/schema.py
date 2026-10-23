"""Schema catalog extracted from the Siemens DCA plug-in DLLs (``ImportExportSchema.dll``).

Two lookups together give every XML leaf in ``BLOB_PARA`` a typed meaning:

* ``ENUM_TOKENS`` maps an enum name (e.g. ``LineModeEnum``) to the ordered list of raw
  token strings the plug-in writes to the ``Value=`` attribute (``LINEMODE_OFF``, ...).
* ``ELEMENT_TO_ENUM`` maps every XML element tag (less the ``n141`` namespace prefix) to
  its enum name, so the reader can translate a raw token to a human label without
  hard-coding each one.

``COLLECTIONS`` describes the list-containers inside ``<Blob>`` so a generic viewer can
offer Add/Remove without a per-type regex in the UI code. Each spec maps a container tag
(e.g. ``SensorList``, ``Presence``, ``ConnectedEcgs``) to its item tag + the field that
marks a slot as occupied + its identifier/name fields + an optional ``item_types`` catalog
for an "Add" dropdown. The data is derived from the vendor plug-in's ``IList<X> _name =
new X[N]`` lines plus the matching ``Type``/``AttributeList`` classes.

Both tables are generated from the plug-in's ``XmlAttribn141_*Enum`` / ``*TypeAttributeList``
classes. The names are kept verbatim so a future re-run can diff cleanly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Non-positional runtime enum values: generated from ``public enum X { TOKEN=VAL, ... }``
# declarations. The byte emitted on the wire is the runtime value, which is
# NOT always the token's XML list position — e.g. ``AckBehavior.ACKBEHAV_ALL = 3``, not
# 1. Only enums whose runtime value differs from the list position are listed here; the
# encoder falls back to the list position for enums absent from this table.
ENUM_RUNTIME_VALUES: dict[str, dict[str, int]] = {
    "AckBehaviorEnum": {"ACKBEHAV_DEFAULT": 0, "ACKBEHAV_ALL": 3},
    "StartupModeEnum": {
        "STARTUP_MODE_VALUE": 0,
        "STARTUP_MODE_VALUE_SWUITCH_OFF": 3,
        "STARTUP_MODE_LAST_RECEIVED": 4,
        "STARTUP_MODE_DAY_NIGHT_VALUE": 5,
    },
    "BinaryInputFunctionEnum": {
        "BIF_1_STATE": 0,
        "BIF_1_SWITCH": 1,
        "BIF_1_DIMM": 2,
        "BIF_1_SUN": 3,
        "BIF_1_1SCENE": 4,
        "BIF_1_SET_VALUE": 5,
        "BIF_1_1_EDGE": 6,
        "BIF_2_EDGE": 7,
        "BIF_2_SWITCH": 8,
        "BIF_2_DIMM": 16,
        "BIF_2_SUN": 17,
        # ``BIF_2_DIMMDOUBLE = 18`` exists in the runtime enum but is not in ENUM_TOKENS;
        # add here for completeness when it starts appearing in XML.
    },
    "SetValueChangeEnum": {
        "SVCH_1_64": 6,
        "SVCH_1_32": 5,
        "SVCH_1_16": 4,
        "SVCH_1_8": 3,
        "SVCH_1_4": 2,
        "SVCH_1_2": 1,
        "SVCH_1": 0,
        "SVCH_2": 7,
        "SVCH_4": 8,
    },
}


ENUM_TOKENS: dict[str, list[str]] = {
    "AckBehaviorEnum": ["ACKBEHAV_DEFAULT", "ACKBEHAV_ALL"],
    "BehaviourAfterDownloadEnum": [
        "BEHAV_AFT_DOWNLOAD_BEFORE",
        "BEHAV_AFT_DOWNLOAD_OFF",
        "BEHAV_AFT_DOWNLOAD_ON",
    ],
    "BinaryInformationValueEnum": [
        "BI_FALSE",
        "BI_TRUE",
        "BI_DISABLE",
        "BI_ENABLE",
        "BI_OFF",
    ],
    "BinaryInputActionParaEnum": [
        "BIA_NO_ACTION",
        "BIA_BIT_1",
        "BIA_BIT_0",
        "BIA_BIT_TOGGLE",
        "BIA_BIT_ONLY1",
        "BIA_BIT_ONLY0",
        "BIA_8BIT",
        "BIA_SCENE",
        "BIA_SWITCH",
        "BIA_DIMM_INC",
        "BIA_DIMM_DEC",
        "BIA_DIMM_STOP",
    ],
    "BinaryInputCyclicFunctionEnum": [
        "BICF_NO",
        "BICF_ALWAYS",
        "BICF_ONLY1",
        "BICF_ONLY0",
    ],
    "BinaryInputFunctionEnum": [
        "BIF_1_STATE",
        "BIF_1_SWITCH",
        "BIF_1_DIMM",
        "BIF_1_SUN",
        "BIF_1_1SCENE",
        "BIF_1_SET_VALUE",
        "BIF_1_1_EDGE",
        "BIF_2_EDGE",
        "BIF_2_SWITCH",
        "BIF_2_DIMM",
    ],
    "ComObjectBehaviourEnum": [
        "COM_OBJ_BAHAV_NA",
        "COM_OBJ_BAHAV_READ",
        "COM_OBJ_BAHAV_CHANGE",
        "COM_OBJ_BAHAV_BOOTCHANGE",
        "COM_OBJ_BAHAV_BORDERCHANGE",
    ],
    "DaliFadeTimeEnum": [
        "DFT_0S",
        "DFT_0_7S",
        "DFT_1_0S",
        "DFT_1_4S",
        "DFT_2_0S",
        "DFT_2_8S",
        "DFT_4_0S",
        "DFT_5_7S",
        "DFT_8_0S",
        "DFT_11_3S",
        "DFT_16_0S",
        "DFT_22_6S",
        "DFT_32_0S",
        "DFT_45_3S",
        "DFT_64_0S",
        "DFT_90_5S",
    ],
    "DaliStateEnum": [
        "DALI_STATE_IDLE",
        "DALI_STATE_EMERGENCY_MODE",
        "DALI_STATE_SHORTED",
    ],
    "EcgModeEnum": [
        "ECG_MODE_NORMAL",
        "ECG_MODE_NORMALNIGHT",
        "ECG_MODE_PERMANENT",
        "ECG_MODE_TIME1",
        "ECG_MODE_TIME2",
    ],
    "EffectChannelTypesEnum": [
        "EFFECT_CH_TYPE_ONOFF",
        "EFFECT_CH_TYPE_DIMMVALUE",
        "EFFECT_CH_TYPE_TIMEDIMMVALUE",
        "EFFECT_CH_TYPE_SCENE",
        "EFFECT_CH_TYPE_TIMESCENE",
        "EFFECT_CH_TYPE_COLORTEMP",
    ],
    "EmergencyTypeEnum": [
        "EMERG_TYPE_NO",
        "EMERG_TYPE_CENTRAL",
        "EMERG_TYPE_1DEV",
        "EMERG_TYPE_2DEV",
        "EMERG_TYPE_1DEV_NO_DIMM",
    ],
    "EmergencyValueModeEnum": [
        "EMERG_VAL_MODE_VALUE",
        "EMERG_VAL_MODE_NOACTION",
    ],
    "FadeTimeEnum": [
        "FADE_TIME_JUMP",
        "FADE_TIME_00_7",
        "FADE_TIME_01_0",
        "FADE_TIME_01_4",
        "FADE_TIME_02_0",
        "FADE_TIME_02_8",
        "FADE_TIME_04_0",
        "FADE_TIME_05_7",
        "FADE_TIME_08_0",
        "FADE_TIME_11_3",
        "FADE_TIME_16_0",
        "FADE_TIME_22_6",
        "FADE_TIME_32_0",
        "FADE_TIME_45_3",
        "FADE_TIME_64_0",
        "FADE_TIME_90_5",
    ],
    "FollowUpTimeModeEnum": ["FOLUP_TIME_MODE_1", "FOLUP_TIME_MODE_2"],
    "LightControlFirstValueEnum": [
        "LCFV_PARA",
        "LCFV_CALC",
        "LCFV_OBJECT",
        "LCFV_OBJECT_RECEIVED",
    ],
    "LightControlOnOffActionEnum": [
        "LCOOA_DIMM",
        "LCOOA_SWITCH_AT_START",
        "LCOOA_SWITCH_AT_STOP",
        "LCOOA_SWITCH_AT_START_STOP",
    ],
    "LightControlSlaveCalcModeEnum": ["LCSCM_CURVE", "LCSCM_OFFSET"],
    "LineModeEnum": ["LINEMODE_OFF", "LINEMODE_NORMAL", "LINEMODE_BROADCAST"],
    "LockModeEnum": ["LOCK_MODE_NO", "LOCK_MODE_YES_0", "LOCK_MODE_YES_1"],
    "LockRecoveryEnum": [
        "LOCK_REC_OFF",
        "LOCK_REC_ON",
        "LOCK_REC_BEFORE",
        "LOCK_REC_BUS",
    ],
    "OnOffPossibleDimmEnum": [
        "ONOFF_POSS_DIMM_NO",
        "ONOFF_POSS_DIMM_ON",
        "ONOFF_POSS_DIMM_OFF",
        "ONOFF_POSS_DIMM_ONOFF",
    ],
    "OnOffPossibleSetValueEnum": [
        "ONOFF_POSS_VAL_NO",
        "ONOFF_POSS_VAL_ON",
        "ONOFF_POSS_VAL_OFF",
        "ONOFF_POSS_VAL_ONOFF",
        "ONOFF_POSS_VAL_ONOFF_NULL",
    ],
    "PresenceActionEnum": [
        "PA_NO_ACTION",
        "PA_ON",
        "PA_OFF",
        "PA_8BIT",
        "PA_SCENE",
    ],
    "RetraceModeEnum": ["RETRACE_MODE_NO", "RETRACE_MODE_YES", "RETRACE_MODE_LAST"],
    "RetraceRecoveryEnum": ["RETRACE_RECOVERY_ALL", "RETRACE_RECOVERY_MIDNIGHT"],
    "SaveSetValueEnum": ["SSV_DIRECT", "SSV_OBJECT"],
    "SetValueChangeEnum": [
        "SVCH_1_64",
        "SVCH_1_32",
        "SVCH_1_16",
        "SVCH_1_8",
        "SVCH_1_4",
        "SVCH_1_2",
        "SVCH_1",
        "SVCH_2",
        "SVCH_4",
    ],
    "SetValueModeEnum": ["SVMODE_ONLYPARA", "SVMODE_PARA_OBJECT"],
    "StartupModeEnum": [
        "STARTUP_MODE_VALUE",
        "STARTUP_MODE_VALUE_SWUITCH_OFF",
        "STARTUP_MODE_LAST_RECEIVED",
        "STARTUP_MODE_DAY_NIGHT_VALUE",
    ],
    "TakeValueBehaviourEnum": ["TV_BEHAV_IMMEDIATLY", "TV_BEHAV_IFON"],
    "TimeSwitchActionFunctionEnum": [
        "TIME_SWITCH_SWITCH",
        "TIME_SWITCH_CALLSCENE",
        "TIME_SWITCH_STARTEFFECT",
        "TIME_SWITCH_FORCEDSWITCH",
        "TIME_SWITCH_COLOURTEMP",
    ],
    "ValueAfterBurninEnum": [
        "VAB_BEFORE",
        "VAB_OFF",
        "VAB_MAX",
        "VAB_MIN",
        "VAB_DISABLED",
    ],
    "ValueBehaviourEnum": [
        "VAL_BEHAV_NOACTION",
        "VAL_BEHAV_BEFORE",
        "VAL_BEHAV_START",
        "VAL_BEHAV_MAX",
        "VAL_BEHAV_MIN",
    ],
}

# Element (tag without the ``n141:`` namespace) -> enum name. Extracted from each XML element's
# AttributeList class (private field ``XmlAttribn141_<Enum>Enum _attr...``).
ELEMENT_TO_ENUM: dict[str, str] = {
    "BinaryInputChannelCyclicEnable": "BinaryInputCyclicFunctionEnum",
    "BinaryInputChannelFunction": "BinaryInputFunctionEnum",
    "BinaryInputChannelLockObject": "BinaryInformationValueEnum",
    "BinaryInputChannelPara1Enum": "BinaryInputActionParaEnum",
    "BinaryInputChannelPara2Enum": "BinaryInputActionParaEnum",
    "BrightnessChannelCyclicEnable": "BinaryInformationValueEnum",
    "BrightnessChannelSendOnChangeEnable": "BinaryInformationValueEnum",
    "DaliDeviceActivateTestStorage": "BinaryInformationValueEnum",
    "DaliDeviceBehaviourAfterDownload": "BehaviourAfterDownloadEnum",
    "DaliDeviceBehaviourAfterFullDownload": "BehaviourAfterDownloadEnum",
    "DaliDeviceComObjEffectState": "ComObjectBehaviourEnum",
    "DaliDeviceComObjErrorState": "ComObjectBehaviourEnum",
    "DaliDeviceComObjExtTestResalt": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateDimmEcgXY": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateEcgDetails": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateEcgDimm": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateEcgError": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateEcgSwitch": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateEcgTemp": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateErrorEcgXY": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateErrorOldEcgXY": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateGroupSummary": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateGrpDimm": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateGrpError": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateGrpSwitch": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateGrpTemp": "ComObjectBehaviourEnum",
    "DaliDeviceComObjStateSwitchEcgXY": "ComObjectBehaviourEnum",
    "DaliDeviceComObjTestResalt": "ComObjectBehaviourEnum",
    "DaliDeviceComObjTestState": "ComObjectBehaviourEnum",
    "DaliDeviceComObjTestStorageCnt": "ComObjectBehaviourEnum",
    "DaliDeviceComObjTestStorageLimit": "ComObjectBehaviourEnum",
    "DaliDeviceConfStandAloneEnable": "BinaryInformationValueEnum",
    "DaliDeviceLineEcgLineTestEnable": "BinaryInformationValueEnum",
    "DaliDeviceLineLineMode": "LineModeEnum",
    "DaliDeviceNightModeActivation": "BinaryInformationValueEnum",
    "DaliDeviceNightModeImmidiateOn": "BinaryInformationValueEnum",
    "DaliDeviceNightModeImmidiateOff": "BinaryInformationValueEnum",
    "DaliDeviceVoltageRecoveryOptimiziation": "BinaryInformationValueEnum",
    "DevType0EcgEmergencyType": "EmergencyTypeEnum",
    "DevType0EcgEmergencyValueMode": "EmergencyValueModeEnum",
    "DevType0EcgFadeTime": "FadeTimeEnum",
    "DevType0EcgMode": "EcgModeEnum",
    "DevType0EcgStartupMode": "StartupModeEnum",
    "DevType0EcgValueAfterBurnin": "ValueAfterBurninEnum",
    "DevType1EcgEmergencyType": "EmergencyTypeEnum",
    "DevType1EcgEmergencyValueMode": "EmergencyValueModeEnum",
    "EffectsEffectChannel": "EffectChannelTypesEnum",
    "EffectsStepFadeTime": "DaliFadeTimeEnum",
    "GroupGroupFadeTime": "FadeTimeEnum",
    "GroupGroupOnOffPossibleDimm": "OnOffPossibleDimmEnum",
    "GroupGroupOnOffPossibleSetValue": "OnOffPossibleSetValueEnum",
    "GroupGroupSaveSetValue": "SaveSetValueEnum",
    "GroupGroupSetValueChange": "SetValueChangeEnum",
    "GroupGroupSetValueMode": "SetValueModeEnum",
    "GroupGroupStartupMode": "StartupModeEnum",
    "GroupGroupTakeValueBehaviour": "TakeValueBehaviourEnum",
    "GroupGroupValueBehaviour": "ValueBehaviourEnum",
    "LightControlConstantFirstValue": "LightControlFirstValueEnum",
    "LightControlConstantOnOffAction": "LightControlOnOffActionEnum",
    "LightControlConstantSlaveCalcMode": "LightControlSlaveCalcModeEnum",
    "LightControlConstantRetraceMode": "RetraceModeEnum",
    "LightControlConstantRetraceRecovery": "RetraceRecoveryEnum",
    "LightControlTwoPointHysteresisMode": "LightControlFirstValueEnum",
    "PresenceChannelActionDetectedA": "PresenceActionEnum",
    "PresenceChannelActionNotDetectedC": "PresenceActionEnum",
    "PresenceChannelFollowUpTimeMode": "FollowUpTimeModeEnum",
    "PresenceChannelIsSlave": "BinaryInformationValueEnum",
    "PresenceChannelLockMode": "LockModeEnum",
    "PresenceChannelLockRecovery": "LockRecoveryEnum",
    "PresenceChannelSecondFrameBAction": "PresenceActionEnum",
    "PresenceChannelSecondFrameDAction": "PresenceActionEnum",
    "PresenceChannelSendCDOnRecovery": "BinaryInformationValueEnum",
    "RtcDaylightSavingEnable": "BinaryInformationValueEnum",
    "ScenesSceneDimmValue": "SaveSetValueEnum",
    "SensorListSensorActive": "BinaryInformationValueEnum",
    "SystemParameters0SystemConfig": "AckBehaviorEnum",
    "TimeSwitchActionFunction": "TimeSwitchActionFunctionEnum",
}


# Human-friendly token labels: strip common prefix, lowercase + spaces, special-case the
# truly non-obvious tokens (e.g. BI_* and the two-letter DALI codes).
_SPECIAL_HUMANIZE: dict[str, str] = {
    "BI_TRUE": "yes",
    "BI_FALSE": "no",
    "BI_ENABLE": "enable",
    "BI_DISABLE": "disable",
    "BI_OFF": "off",
    "ACKBEHAV_DEFAULT": "default",
    "ACKBEHAV_ALL": "ack all",
    "PA_NO_ACTION": "no action",
    "PA_ON": "on",
    "PA_OFF": "off",
    "PA_8BIT": "send 8-bit value",
    "PA_SCENE": "call scene",
    "BIA_NO_ACTION": "no action",
    "BIA_BIT_TOGGLE": "toggle",
    "BIA_BIT_1": "write 1",
    "BIA_BIT_0": "write 0",
    "BIA_BIT_ONLY1": "write 1 only",
    "BIA_BIT_ONLY0": "write 0 only",
    "BIA_8BIT": "send 8-bit value",
    "BIA_SCENE": "call scene",
    "BIA_SWITCH": "switch",
    "BIA_DIMM_INC": "dim up",
    "BIA_DIMM_DEC": "dim down",
    "BIA_DIMM_STOP": "dim stop",
    "COM_OBJ_BAHAV_NA": "n/a",
    "COM_OBJ_BAHAV_READ": "on read",
    "COM_OBJ_BAHAV_CHANGE": "on change",
    "COM_OBJ_BAHAV_BOOTCHANGE": "boot + change",
    "COM_OBJ_BAHAV_BORDERCHANGE": "border + change",
}

_PREFIX_STRIPPERS = [
    "BEHAV_AFT_DOWNLOAD_",
    "BICF_",
    "BIF_",
    "DFT_",
    "DALI_STATE_",
    "ECG_MODE_",
    "EFFECT_CH_TYPE_",
    "EMERG_VAL_MODE_",
    "EMERG_TYPE_",
    "FADE_TIME_",
    "FOLUP_TIME_MODE_",
    "LCFV_",
    "LCOOA_",
    "LCSCM_",
    "LINEMODE_",
    "LOCK_MODE_",
    "LOCK_REC_",
    "ONOFF_POSS_DIMM_",
    "ONOFF_POSS_VAL_",
    "RETRACE_MODE_",
    "RETRACE_RECOVERY_",
    "SSV_",
    "SVCH_",
    "SVMODE_",
    "STARTUP_MODE_",
    "TV_BEHAV_",
    "TIME_SWITCH_",
    "VAB_",
    "VAL_BEHAV_",
]


def humanize_token(token: str) -> str:
    """Translate a vendor enum token to a prose label (lower-case words, spaces for
    underscores). Falls back to the raw token if no stripping rule applies."""
    special = _SPECIAL_HUMANIZE.get(token)
    if special is not None:
        return special
    stripped = token
    for prefix in _PREFIX_STRIPPERS:
        if token.startswith(prefix):
            stripped = token[len(prefix) :]
            break
    # Lower-case, replace underscore with space, de-stutter numeric time strings a bit.
    label = stripped.lower().replace("_", " ").strip()
    # Common pattern: "0_7s" -> "0.7 s", "16_0s" -> "16.0 s"
    label = re.sub(r"(\d+) (\d+)s", r"\1.\2 s", label)
    return label or token


def resolve_token(element_tag: str, token: str) -> str:
    """Return the humanized label for ``token`` if ``element_tag`` is known to use an enum;
    otherwise return the raw token (so numeric/string values pass through unchanged)."""
    enum_name = ELEMENT_TO_ENUM.get(element_tag)
    if enum_name is None:
        return token
    choices = ENUM_TOKENS.get(enum_name)
    if not choices or token not in choices:
        return token
    return humanize_token(token)


def choices_for_element(element_tag: str) -> list[str] | None:
    """Return the full enum token list for an element tag, or ``None`` for non-enum fields."""
    enum_name = ELEMENT_TO_ENUM.get(element_tag)
    if enum_name is None:
        return None
    return list(ENUM_TOKENS.get(enum_name, ()))


@dataclass(frozen=True, slots=True)
class ItemTypeSpec:
    """One choice in a Collection's Add dropdown.

    When adding this type, ``type_field_setter`` writes ``type_id`` to the referenced XML
    tag on the new item. ``allocates`` describes any side-effect items that must also be
    created in sibling collections (e.g. a 4-fold pushbutton allocates 4 BinaryInputChannel
    slots in addition to the SensorList slot).
    """

    type_id: str
    label: str
    type_field_setter: str  # XML tag on the new item that receives ``type_id``
    # Side effects: ``{container_tag: count_of_new_items}``.
    allocates: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class CollectionSpec:
    """A list container in <Blob> + the generic UI contract.

    ``container_tag`` sits directly under <Blob>; the plug-in instantiates one container
    per DALI line (so ``blob.findall(container_tag)`` yields the per-line list). Each
    container holds ``capacity`` static ``item_tag`` slots; a slot is "active" when
    ``activity_field`` != ``activity_inactive_value``. ``name_field`` is the human label
    for the Add/Remove UI; ``id_field`` is passed to :func:`writer.remove_item`.

    ``item_types`` is optional and only populated when the "Add" affordance needs a type
    dropdown (e.g. SensorList needs a sensor-type choice, Presence does not).
    """

    container_tag: str
    item_tag: str
    display_label: str
    activity_field: str
    activity_inactive_value: str
    id_field: str
    name_field: str | None = None
    capacity: int = 0
    item_types: tuple[ItemTypeSpec, ...] = ()
    # Fields the Add form seeds on a new item in addition to id/name (e.g. ChannelNr=1).
    seed_fields: tuple[tuple[str, str], ...] = ()


# --- Siemens sensor-type catalog (10 types from PluginInterface.resources.dll) -----------
# Flags: "b" = binaryInput slot per button, "p" = presence, "l" = brightness. Capacity
# ``allocates`` on each type is derived from the Channel list in SensorDescriptions.xml.
_PUSHBUTTON_4FOLD = (
    ItemTypeSpec(
        type_id="id_03A3C2014B74",
        label="DALI pushbutton input 4-fold (5WG1 141-2AB71)",
        type_field_setter="SensorListSensorId",
        allocates=(("BinaryInput", 4),),
    ),
    ItemTypeSpec(
        type_id="id_03A54294358D",
        label="DALI PRO PB Coupler (EAN 4008321496461)",
        type_field_setter="SensorListSensorId",
        allocates=(("BinaryInput", 4),),
    ),
)
_COMBI_SENSORS = tuple(
    ItemTypeSpec(
        type_id=tid,
        label=label,
        type_field_setter="SensorListSensorId",
        allocates=(("Presence", 1), ("Brightness", 1)),
    )
    for tid, label in (
        ("id_03A3C20175BA", "DALI combi sensor office (5WG1 141-2AB51)"),
        ("id_03A542926BC5", "DALI PRO Sensor Coupler (EAN 4008321379269)"),
        ("id_03A542987234", "DALI HIGHBAY Adapter (EAN 4008321774132)"),
        ("id_03AFA39B9A72", "DALI LS/PD LI (EAN 4052899043954)"),
        ("id_03AFA3A920B4", "DALI LS/PD CI (EAN 4052899930292)"),
        ("id_03AFA39D1860", "DALI Sensorcoupler HF LS LI (EAN 4052899141728)"),
        ("id_03AFA39E731B", "DALI Sensor Coupler E (EAN 4052899230491)"),
        ("id_0", "DALI motion detector (6 mA)"),
    )
)

SENSOR_TYPES: tuple[ItemTypeSpec, ...] = (*_PUSHBUTTON_4FOLD, *_COMBI_SENSORS)


# --- Collection specs ---------------------------------------------------------------------
# Derived from IList<X> _y = new X[N] declarations plus the
# matching Type / AttributeList classes in ImportExportSchema. Only user-relevant
# containers are modelled; the rest render as a flat read-only table in the generic
# section view. Add/Remove is driven entirely by this table.
COLLECTIONS: dict[str, CollectionSpec] = {
    "SensorList": CollectionSpec(
        container_tag="SensorList",
        item_tag="SensorListSensor",
        display_label="Sensors",
        activity_field="SensorListSensorNr",
        activity_inactive_value="0",
        id_field="SensorListSensorNr",
        name_field="SensorListSensorName",
        capacity=64,
        item_types=SENSOR_TYPES,
    ),
    "Presence": CollectionSpec(
        container_tag="Presence",
        item_tag="PresenceChannel",
        display_label="Presence channels",
        activity_field="PresenceChannelSensorNr",
        activity_inactive_value="0",
        id_field="PresenceChannelSensorNr",
        name_field="PresenceChannelName",
        capacity=16,
        seed_fields=(("PresenceChannelChannelNr", "1"),),
    ),
    "Brightness": CollectionSpec(
        container_tag="Brightness",
        item_tag="BrightnessChannel",
        display_label="Brightness channels",
        activity_field="BrightnessChannelSensorNr",
        activity_inactive_value="0",
        id_field="BrightnessChannelSensorNr",
        name_field="BrightnessChannelName",
        capacity=8,
        seed_fields=(("BrightnessChannelChannelNr", "1"),),
    ),
    "BinaryInput": CollectionSpec(
        container_tag="BinaryInput",
        item_tag="BinaryInputChannel",
        display_label="Binary inputs",
        activity_field="BinaryInputChannelSensorNr",
        activity_inactive_value="0",
        id_field="BinaryInputChannelSensorNr",
        capacity=128,
        seed_fields=(("BinaryInputChannelLongPressTime", "5"),),
    ),
    "ConnectedSensors": CollectionSpec(
        container_tag="ConnectedSensors",
        item_tag="ConnectedSensorsSensor",
        display_label="Connected sensors",
        activity_field="ConnectedSensorsSensorActive",
        activity_inactive_value="BI_FALSE",
        id_field="ConnectedSensorsSensorShortAddress",
        capacity=64,
    ),
    "ConnectedEcgs": CollectionSpec(
        container_tag="ConnectedEcgs",
        item_tag="ConnectedEcgsEcg",
        display_label="Connected ECGs",
        activity_field="ConnectedEcgsEcgActive",
        activity_inactive_value="BI_FALSE",
        id_field="ConnectedEcgsEcgShortAddress",
        capacity=64,
    ),
    "Group": CollectionSpec(
        container_tag="Group",
        item_tag="GroupGroup",
        display_label="Groups",
        activity_field="GroupGroupStartupMode",  # non-default -> group in use
        activity_inactive_value="",
        id_field="GroupGroupStartupMode",
        name_field="GroupGroupName",
        capacity=16,
    ),
}


def collection_for(container_tag: str) -> CollectionSpec | None:
    """Return the :class:`CollectionSpec` for a direct <Blob> child tag, or ``None``."""
    return COLLECTIONS.get(container_tag)
