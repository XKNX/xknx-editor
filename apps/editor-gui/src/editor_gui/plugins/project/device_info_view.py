"""The rows of a device info readout, as shown in the details of its operation."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from editor_gui.device_info import (
    DeviceInfo,
    application_version_text,
    firmware_text,
    hex_value,
    serial_text,
)
from editor_gui.plugins.project.strings import S
from editor_gui.programming import error_class_text
from xknxeditor.download.load_state import LoadState

Section = tuple[str, list[tuple[str, str]]]


class Toned(str):
    """A row value shown in a color: ``ok`` (green) or ``differs`` (orange)."""

    tone: str

    def __new__(cls, text: str, tone: str) -> Toned:
        value = super().__new__(cls, text)
        value.tone = tone
        return value


def load_state_text(state: LoadState) -> str:
    match state:
        case LoadState.UNLOADED:
            return S.LOAD_STATE_UNLOADED
        case LoadState.LOADED:
            return S.LOAD_STATE_LOADED
        case LoadState.LOADING:
            return S.LOAD_STATE_LOADING
        case LoadState.ERROR:
            return S.LOAD_STATE_ERROR
        case LoadState.UNLOADING:
            return S.LOAD_STATE_UNLOADING
        case LoadState.LOAD_COMPLETING:
            return S.LOAD_STATE_LOAD_COMPLETING


def _rows(*pairs: tuple[str, str | None]) -> list[tuple[str, str]]:
    return [(label, value) for label, value in pairs if value is not None]


def _state(state: LoadState | None) -> str | None:
    return load_state_text(state) if state is not None else None


def device_info_sections(
    info: DeviceInfo,
    *,
    manufacturers: Mapping[str, str],
    application_name: str | None,
    group_address: Callable[[int], str],
    project_version: int | None = None,
    expected: Mapping[int, set[int]] | None = None,
) -> list[Section]:
    """General, Application Program and Group Communication rows; unread values are left out.
    Compared with the project: the application version with ``project_version``, and each group
    object's group addresses with ``expected`` (object number -> the project's group addresses)."""
    manufacturer = None
    if info.manufacturer_id is not None:
        manufacturer_id = f"M-{info.manufacturer_id:04X}"
        manufacturer = manufacturers.get(manufacturer_id, manufacturer_id)
    progmode = None
    if info.programming_mode is not None:
        progmode = (
            Toned(S.DEVICE_INFO_ON, "differs")
            if info.programming_mode
            else S.DEVICE_INFO_OFF
        )
    status = None
    if info.error_code is not None:
        status = (
            Toned(S.READOUT_STATUS_OK, "ok")
            if info.error_code == 0
            else Toned(error_class_text(info.error_code) or "", "differs")
        )
    general = _rows(
        (
            S.DEVICE_INFO_MASK,
            f"{info.mask_version:04X}" if info.mask_version is not None else None,
        ),
        (S.DEVICE_INFO_ADDRESS, info.individual_address),
        (S.DEVICE_INFO_MANUFACTURER, manufacturer),
        (S.DEVICE_INFO_ORDER, hex_value(info.order_number)),
        (S.DEVICE_INFO_SERIAL, serial_text(info.serial_number)),
        (S.DEVICE_INFO_HARDWARE, hex_value(info.hardware_type)),
        (S.DEVICE_INFO_FIRMWARE, firmware_text(info.firmware_version)),
        (S.DEVICE_INFO_PROGMODE, progmode),
        (S.READOUT_STATUS, status),
    )

    application = None
    app_id = info.application
    if app_id is not None:
        vendor = manufacturers.get(app_id.manufacturer_id, app_id.manufacturer_id)
        name = application_name or (
            f"{app_id.application_number:04X} {app_id.application_version:02X}"
        )
        application = f"{vendor} {name}"
    version = None
    if app_id is not None:
        version = application_version_text(app_id.application_version)
        if (
            project_version is not None
            and app_id.application_version != project_version
        ):
            version = Toned(
                f"{version}  "
                + S.READOUT_VERSION_MISMATCH.format(
                    version=application_version_text(project_version)
                ),
                "differs",
            )
    program = _rows(
        (S.DEVICE_INFO_APPLICATION, application),
        (S.CONFIGURE_APP_VERSION, version),
        (S.DEVICE_INFO_LOAD_STATE, _state(info.application_load_state)),
    )

    group = _rows(
        (S.DEVICE_INFO_ADDRESS_TABLE, _state(info.address_table_load_state)),
        (S.DEVICE_INFO_ASSOCIATION_TABLE, _state(info.association_table_load_state)),
        (S.DEVICE_INFO_GROUP_OBJECT_TABLE, _state(info.group_object_table_load_state)),
    )
    for group_object in info.group_objects:
        details = ", ".join(
            part for part in (group_object.size, group_object.flags) if part
        )
        label = f"Obj#{group_object.number}" + (f" ({details})" if details else "")
        text = ", ".join(group_address(ga) for ga in group_object.group_addresses)
        if expected is not None:
            matches = set(group_object.group_addresses) == expected.get(
                group_object.number, set()
            )
            text = Toned(text or "-", "ok" if matches else "differs")
        group.append((label, text))
    if info.application_unknown:
        group.append(
            (S.DEVICE_INFO_GROUP_COMMUNICATION, S.DEVICE_INFO_APPLICATION_UNKNOWN)
        )
    if info.group_communication_error:
        group.append(
            (
                S.DEVICE_INFO_GROUP_COMMUNICATION,
                S.DEVICE_INFO_TABLES_FAILED.format(
                    error=info.group_communication_error
                ),
            )
        )

    return [
        (title, rows)
        for title, rows in (
            (S.DEVICE_INFO_GENERAL, general),
            (S.DEVICE_INFO_APPLICATION, program),
            (S.DEVICE_INFO_GROUP_COMMUNICATION, group),
        )
        if rows
    ]
