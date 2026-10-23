"""Port of ``Parameters.dll::GetPropertyImage()`` for Siemens N141_21 containers.

Each ``build_<container>_properties(blob_root, line_idx)`` emits a
``dict[int, bytes]`` — exactly what the ETS DCA plug-in would send as
``A_PropertyValue_Write(object_index=odx, pid, element_count=NoE, start=1, data)`` on the
Interface Object of the device. The reference C# calls on the typed value instances live
inside ``DaliDeviceType.GetPropertyImage()``, ``DaliDeviceType.GetBinaryInputPropertyImage``
etc. — we re-emit the exact byte stream using :mod:`.encoder` primitives.

Scope today: ``DaliDevice`` only (global settings PIDs 51/52/100-248). Other containers
(BinaryInput, Presence, Brightness, SensorList, ...) are placeholders and will be filled in
subsequent iterations; each follows the same pattern.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from editor_gui.plugin_blob_readers.siemens_dali.encoder import (
    encode_binary_information,
    encode_byte,
    encode_date,
    encode_enum,
    encode_time,
    encode_ushort,
)
from editor_gui.plugin_blob_readers.siemens_dali.schema import (
    ELEMENT_TO_ENUM,
    ENUM_TOKENS,
)

_N141_NS = "http://www.siemens.com/knx/N141_21_Parameters"
_NS = f"{{{_N141_NS}}}"


# Per PID the ``DaliDevice`` properties: (xml tag suffix under <DaliDevice>, byte encoder).
# Order matches ``DaliDeviceType.GetPropertyImage()``. The
# property's ``Size`` byte in the KNX frame is implied by the encoder (1 for Byte/Enum/BI,
# 2 for UShort, 3 for Date/Time); the ETS plug-in sends them with ``NoE=1``.
#
# Line-indexed sub-fields (PIDs 151, 166, 167) read from both <DaliDeviceLine> children and
# are emitted here as a 2-byte array (one byte per line) matching ``list.ToArray()`` from
# the C# source.
_DALIDEVICE_SCALAR_PIDS: tuple[tuple[int, str, str], ...] = (
    (51, "DaliDeviceDownloadTime", "time"),
    (52, "DaliDeviceDownloadDate", "date"),
    (100, "DaliDeviceBootDelay", "byte"),
    (101, "DaliDeviceVoltageRecoveryOptimiziation", "bi"),
    (102, "DaliDeviceBehaviourAfterDownload", "enum"),
    (103, "DaliDeviceConfStandAloneEnable", "bi"),
    (104, "DaliDeviceHandModeTime", "byte"),
    (105, "DaliDeviceBurnInTime", "byte"),
    (110, "DaliDeviceEmergencyOffDelay", "ushort"),
    (111, "DaliDeviceNightModeActivation", "bi"),
    (112, "DaliDeviceNightModeTime", "byte"),
    (150, "DaliDeviceComObjErrorState", "enum"),
    (152, "DaliDeviceComObjStateGrpSwitch", "enum"),
    (153, "DaliDeviceComObjStateGrpDimm", "enum"),
    (176, "DaliDeviceComObjStateGrpTemp", "enum"),
    (154, "DaliDeviceComObjStateGrpError", "enum"),
    (155, "DaliDeviceComObjStateEcgSwitch", "enum"),
    (156, "DaliDeviceComObjStateEcgDimm", "enum"),
    (177, "DaliDeviceComObjStateEcgTemp", "enum"),
    (157, "DaliDeviceComObjStateEcgError", "enum"),
    (158, "DaliDeviceComObjStateErrorEcgXY", "enum"),
    (171, "DaliDeviceComObjStateDimmEcgXY", "enum"),
    (172, "DaliDeviceComObjStateSwitchEcgXY", "enum"),
    (173, "DaliDeviceComObjStateErrorOldEcgXY", "enum"),
    (174, "DaliDeviceComObjStateGroupSummary", "enum"),
    (175, "DaliDeviceComObjStateEcgDetails", "enum"),
    (159, "DaliDeviceComObjSendLockTime", "byte"),
    (160, "DaliDeviceComObjTestState", "enum"),
    (161, "DaliDeviceComObjTestResalt", "enum"),
    (162, "DaliDeviceComObjExtTestResalt", "enum"),
    (163, "DaliDeviceComObjTestStorageLimit", "enum"),
    (164, "DaliDeviceComObjTestStorageCnt", "enum"),
    (169, "DaliDeviceComObjEffectState", "enum"),
    (165, "DaliDeviceStorageCountThreshold", "ushort"),
    (168, "DaliDeviceActivateTestStorage", "bi"),
    (170, "DaliDeviceLockErrorStateResetTime", "ushort"),
    (220, "DaliDeviceBsStartupTime", "ushort"),
    (221, "DaliDeviceBsPollingTime", "ushort"),
    (222, "DaliDeviceBininpPollingTimeShort", "ushort"),
    (223, "DaliDeviceBininpPollingTimeLong", "ushort"),
    (230, "DaliDeviceStatusInterframeTime", "ushort"),
    (231, "DaliDeviceStatusInterframeTimeBroadcast", "ushort"),
    (235, "DaliDeviceSenderTimeslice", "ushort"),
    (236, "DaliDeviceSenderTimesliceCount", "byte"),
    (240, "DaliDeviceSchedulerErrorCylceTime", "ushort"),
    (241, "DaliDeviceSchedulerForceIdleTime", "ushort"),
    (242, "DaliDeviceSchedulerTrafficProlongTime", "ushort"),
    (243, "DaliDeviceSchedulerStartupDelay", "ushort"),
    (244, "DaliDeviceSchedulerCycleTime", "ushort"),
    (245, "DaliDeviceTransactionTimeout", "ushort"),
    (246, "DaliDeviceNightModeImmidiateOn", "bi"),
    (247, "DaliDeviceNightModeImmidiateOff", "bi"),
    (248, "DaliDeviceBehaviourAfterFullDownload", "enum"),
)

# Line-fanout PIDs: tag is read from each <DaliDeviceLine> and concatenated.
_DALIDEVICE_LINE_PIDS: tuple[tuple[int, str, str], ...] = (
    (151, "DaliDeviceLineFailureLimitEcg", "byte"),
    (166, "DaliDeviceLineLineMode", "enum"),
    (167, "DaliDeviceLineEcgLineTestEnable", "bi"),
)


# Vendor defaults from the ``DaliDeviceType`` + DaliDeviceLineType constructors. Values here
# mirror the C# field-initializer so a BLOB without the
# element produces the same byte on the wire as the plug-in would. Keys match the leaf tag
# suffix the properties table uses (so missing scalars + fanout fields both resolve).
_DEFAULTS: dict[str, str] = {
    # DaliDevice scalars
    "DaliDeviceDownloadTime": "00:00:00",
    "DaliDeviceDownloadDate": "1990-01-01T00:00:00",
    "DaliDeviceBootDelay": "0",
    "DaliDeviceVoltageRecoveryOptimiziation": "BI_FALSE",
    "DaliDeviceBehaviourAfterDownload": "BEHAV_AFT_DOWNLOAD_BEFORE",
    "DaliDeviceConfStandAloneEnable": "BI_FALSE",
    "DaliDeviceHandModeTime": "0",
    "DaliDeviceBurnInTime": "100",
    "DaliDeviceEmergencyOffDelay": "0",
    "DaliDeviceNightModeActivation": "BI_TRUE",
    "DaliDeviceNightModeTime": "5",
    "DaliDeviceComObjErrorState": "COM_OBJ_BAHAV_BOOTCHANGE",
    "DaliDeviceComObjStateGrpSwitch": "COM_OBJ_BAHAV_CHANGE",
    "DaliDeviceComObjStateGrpDimm": "COM_OBJ_BAHAV_CHANGE",
    "DaliDeviceComObjStateGrpTemp": "COM_OBJ_BAHAV_CHANGE",
    "DaliDeviceComObjStateGrpError": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjStateEcgSwitch": "COM_OBJ_BAHAV_CHANGE",
    "DaliDeviceComObjStateEcgDimm": "COM_OBJ_BAHAV_CHANGE",
    "DaliDeviceComObjStateEcgTemp": "COM_OBJ_BAHAV_CHANGE",
    "DaliDeviceComObjStateEcgError": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjStateErrorEcgXY": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjStateDimmEcgXY": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjStateSwitchEcgXY": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjStateErrorOldEcgXY": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjStateGroupSummary": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjStateEcgDetails": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjSendLockTime": "15",
    "DaliDeviceComObjTestState": "COM_OBJ_BAHAV_READ",
    "DaliDeviceComObjTestResalt": "COM_OBJ_BAHAV_CHANGE",
    "DaliDeviceComObjExtTestResalt": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjTestStorageLimit": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjTestStorageCnt": "COM_OBJ_BAHAV_NA",
    "DaliDeviceComObjEffectState": "COM_OBJ_BAHAV_NA",
    "DaliDeviceStorageCountThreshold": "100",
    "DaliDeviceActivateTestStorage": "BI_FALSE",
    "DaliDeviceLockErrorStateResetTime": "0",
    "DaliDeviceBsStartupTime": "5000",
    "DaliDeviceBsPollingTime": "500",
    "DaliDeviceBininpPollingTimeShort": "250",
    "DaliDeviceBininpPollingTimeLong": "1000",
    "DaliDeviceStatusInterframeTime": "500",
    "DaliDeviceStatusInterframeTimeBroadcast": "500",
    "DaliDeviceSenderTimeslice": "500",
    "DaliDeviceSenderTimesliceCount": "8",
    "DaliDeviceSchedulerErrorCylceTime": "1000",
    "DaliDeviceSchedulerForceIdleTime": "50",
    "DaliDeviceSchedulerTrafficProlongTime": "10",
    "DaliDeviceSchedulerStartupDelay": "200",
    "DaliDeviceSchedulerCycleTime": "10000",
    "DaliDeviceTransactionTimeout": "500",
    "DaliDeviceNightModeImmidiateOn": "BI_FALSE",
    "DaliDeviceNightModeImmidiateOff": "BI_FALSE",
    "DaliDeviceBehaviourAfterFullDownload": "BEHAV_AFT_DOWNLOAD_ON",
    # DaliDeviceLine per-line defaults.
    "DaliDeviceLineFailureLimitEcg": "1",
    "DaliDeviceLineLineMode": "LINEMODE_BROADCAST",
    "DaliDeviceLineEcgLineTestEnable": "BI_TRUE",
}

# ``DaliDeviceType`` always carries exactly two ``DaliDeviceLine`` instances (``_line =
# new DaliDeviceLineType[2]``).
_DALIDEVICE_LINE_COUNT = 2


def build_dalidevice_properties(dali_device: ET.Element) -> dict[int, bytes]:
    """Emit the full ``DaliDeviceType.GetPropertyImage()`` dictionary from a
    ``<DaliDevice>`` XML element. Element-level enum typing comes from
    :data:`siemens_dali.schema.ELEMENT_TO_ENUM`.

    Missing leaves fall back to the vendor's constructor defaults so the emitted bytes
    match the plug-in's property-image byte for byte. The two ``DaliDeviceLine`` slots are
    always emitted (one byte per line) to match the plug-in's fixed array layout.
    """
    out: dict[int, bytes] = {}
    for pid, tag, kind in _DALIDEVICE_SCALAR_PIDS:
        leaf = dali_device.find(f"{_NS}{tag}")
        raw = leaf.get("Value", "") if leaf is not None else ""
        if not raw:
            raw = _DEFAULTS.get(tag, "")
            if not raw:
                continue  # no default known -> leave property unset
        out[pid] = _encode_leaf(tag, kind, raw)
    lines = dali_device.findall(f"{_NS}DaliDeviceLine")
    for pid, tag, kind in _DALIDEVICE_LINE_PIDS:
        parts: list[bytes] = []
        for i in range(_DALIDEVICE_LINE_COUNT):
            line = lines[i] if i < len(lines) else None
            leaf = line.find(f"{_NS}{tag}") if line is not None else None
            raw = leaf.get("Value", "") if leaf is not None else ""
            if not raw:
                raw = _DEFAULTS.get(tag, "")
                if not raw:
                    # Should never happen with the table above; fail loud instead of
                    # emitting a short payload that silently shifts the next line's byte.
                    raise KeyError(f"no default for {tag}")
            parts.append(_encode_leaf(tag, kind, raw))
        out[pid] = b"".join(parts)
    return out


def _encode_leaf(tag: str, kind: str, raw: str) -> bytes:
    """Dispatch to the right primitive encoder. Enum types resolve via
    ``ELEMENT_TO_ENUM`` so each PID picks the exact token list the vendor DLL expects."""
    if kind == "byte":
        return encode_byte(raw)
    if kind == "ushort":
        return encode_ushort(raw)
    if kind == "time":
        return encode_time(raw)
    if kind == "date":
        return encode_date(raw)
    if kind == "bi":
        return encode_binary_information(raw)
    if kind == "enum":
        enum_name = ELEMENT_TO_ENUM.get(tag)
        if enum_name is None or enum_name not in ENUM_TOKENS:
            raise KeyError(f"no enum catalogued for {tag}")
        return encode_enum(enum_name, raw)
    raise ValueError(f"unknown encoder kind: {kind}")


__all__ = (
    "_DALIDEVICE_LINE_PIDS",
    "_DALIDEVICE_SCALAR_PIDS",
    "build_dalidevice_properties",
)
