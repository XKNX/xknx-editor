"""Classic Siemens parameter downloads use complete images and confirmed device updates."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from editor_gui.plugin_blob_readers.siemens_dali import download, writer
from xknx.telegram.apci import (
    FunctionPropertyCommand,
    FunctionPropertyStateRead,
    FunctionPropertyStateResponse,
    PropertyValueRead,
    PropertyValueResponse,
    PropertyValueWrite,
)

from packages.download.tests.conftest import FakeDevice
from xknxeditor.download import DeviceProgrammer, load_state
from xknxeditor.download.errors import VerificationError

_BLOB = Path(__file__).parent / "fixtures" / "siemens_n141_blob_para.dat"
_APP = "M-0001_A-9837-11-449E"


def _config(app: str = _APP) -> download.Configuration:
    return download.compile_configuration(_BLOB.read_bytes(), app)


class Gateway(FakeDevice):
    def __init__(self, config: download.Configuration) -> None:
        super().__init__()
        variant = 0x058B0301 if config.lines == 1 else 0x058B0101
        self.properties[4, 246] = variant.to_bytes(4, "big") + b"\x04\x05\x01"
        self.property_element_sizes[4, 246] = 1
        self.properties[4, 13] = config.application_id
        self.load_states[4] = load_state.LoadState.LOADED
        self.states: dict[int, int] = {}
        self.bad_state = False
        self.command_result = b"\x01\x01\x00\x00"
        self.poll_results: deque[bytes] = deque()

    def _handle_property_write(self, payload: PropertyValueWrite) -> None:
        if payload.property_id == 53:
            assert len(payload.data) == 10 and payload.count == 1
            self.states[payload.object_index] = {4: 0, 1: 2, 2: 1}[payload.data[0]]
        else:
            super()._handle_property_write(payload)

    def _handle_property_read(
        self, payload: PropertyValueRead
    ) -> PropertyValueResponse:
        if payload.property_id == 53:
            return PropertyValueResponse(
                object_index=payload.object_index,
                property_id=53,
                start_index=1,
                count=1,
                data=bytes(
                    [3 if self.bad_state else self.states[payload.object_index]]
                ),
            )
        return super()._handle_property_read(payload)

    def _function_property_response(
        self, payload: FunctionPropertyCommand | FunctionPropertyStateRead
    ) -> FunctionPropertyStateResponse:
        data = (
            self.command_result
            if isinstance(payload, FunctionPropertyCommand)
            else (self.poll_results.popleft() if self.poll_results else b"\x02")
        )
        return FunctionPropertyStateResponse(
            object_index=payload.object_index,
            property_id=payload.property_id,
            return_code=self.function_property_return_code,
            data=data,
        )


def _writes(gateway: Gateway) -> list[PropertyValueWrite]:
    return [p for p in gateway.sent if isinstance(p, PropertyValueWrite)]


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(download.asyncio, "sleep", AsyncMock())


@pytest.mark.parametrize(
    "app,lines,arrays", [(_APP, 1, 350), ("M-0001_A-9834-11-1234", 2, 560)]
)
async def test_complete_download(app: str, lines: int, arrays: int) -> None:
    config = _config(app)
    gateway = Gateway(config)
    programmer = DeviceProgrammer(gateway)
    programmer.max_apdu_length = 55
    gateway.poll_results.extend([b"\x01\x03\x02\x50", b"\x02"])
    assert await download.program_configuration(programmer, config) == arrays
    for write in config.writes:
        assert gateway.properties[write.odx, write.pid] == write.data
    assert gateway.load_states[4] == load_state.LoadState.LOADED
    assert set(gateway.states.values()) == {1}
    commands = [p for p in gateway.sent if isinstance(p, FunctionPropertyCommand)]
    assert [(p.object_index, p.property_id, p.data) for p in commands] == [
        (12 + line, 52, bytes([command])) for line in range(lines) for command in (3, 4)
    ]
    assert _writes(gateway)[-1].data == b"\x10"
    assert all(
        p.object_index not in (1, 2, 3, 35, 36, 37, 38) for p in _writes(gateway)
    )
    assert all(1 <= p.count <= 15 and len(p.data) <= 49 for p in _writes(gateway))
    fanout = next(
        p for p in _writes(gateway) if p.object_index == 11 and p.property_id == 151
    )
    assert fanout.count == 2
    array = [
        p for p in _writes(gateway) if p.object_index == 20 and p.property_id == 100
    ]
    assert len(array) > 1
    assert [p.start_index for p in array] == list(range(1, 65, 15))


@pytest.mark.parametrize(
    "failure", ["variant", "version", "application", "load_state", "apdu"]
)
async def test_incompatible_device_is_not_written(failure: str) -> None:
    config = _config()
    gateway = Gateway(config)
    programmer = DeviceProgrammer(gateway)
    programmer.max_apdu_length = 55
    if failure == "variant":
        gateway.properties[4, 246] = b"\x05\x8b\x01\x01\x04\x05\x01"
    elif failure == "version":
        gateway.properties[4, 246] = b"\x05\x8b\x03\x01\x02\x03\x01"
    elif failure == "application":
        gateway.properties[4, 13] = b"\x00\x01\x98\x37\x10"
    elif failure == "load_state":
        gateway.load_states[4] = load_state.LoadState.UNLOADED
    else:
        programmer.max_apdu_length = 14
    with pytest.raises((ValueError, VerificationError)):
        await download.program_configuration(programmer, config)
    assert not _writes(gateway)


@pytest.mark.parametrize("result", [b"", b"\x00", b"\x03\x07", b"\x01", b"\xff"])
async def test_failed_update_never_reports_download_finished(result: bytes) -> None:
    config = _config()
    gateway = Gateway(config)
    gateway.poll_results.append(result)
    programmer = DeviceProgrammer(gateway)
    programmer.max_apdu_length = 55
    with pytest.raises(ValueError):
        await download.program_configuration(programmer, config)
    assert not any(p.object_index == 6 and p.data == b"\x10" for p in _writes(gateway))
    assert len([p for p in gateway.sent if isinstance(p, FunctionPropertyCommand)]) == 1


async def test_bad_container_state_prevents_device_update() -> None:
    config = _config()
    gateway = Gateway(config)
    gateway.bad_state = True
    programmer = DeviceProgrammer(gateway)
    programmer.max_apdu_length = 55
    with pytest.raises(ValueError, match="did not finish loading"):
        await download.program_configuration(programmer, config)
    assert not any(isinstance(p, FunctionPropertyCommand) for p in gateway.sent)
    assert gateway.load_states[4] == load_state.LoadState.LOADING


async def test_command_rejection_is_not_hidden_by_later_status() -> None:
    config = _config()
    gateway = Gateway(config)
    gateway.command_result = b"\x03\x07"
    programmer = DeviceProgrammer(gateway)
    programmer.max_apdu_length = 55
    with pytest.raises(ValueError, match="rejected"):
        await download.program_configuration(programmer, config)
    assert not any(isinstance(p, FunctionPropertyStateRead) for p in gateway.sent)


def test_scene_overwrite_false_preserves_scene_objects() -> None:
    data = writer.apply_edit(
        _BLOB.read_bytes(),
        "siemens_blob:ScenePluginInfo[0]/ScenePluginInfoOverwrite[0]",
        "false",
    )
    config = download.compile_configuration(data, _APP)
    assert not any(w.odx in (15, 16) for w in config.writes)
    assert any(w.odx == 17 for w in config.writes)


@pytest.mark.parametrize("value", ["-1", "256", "bad"])
def test_invalid_property_rejected_before_programming(value: str) -> None:
    data = writer.apply_edit(
        _BLOB.read_bytes(), "siemens_blob:DaliDevice[0]/DaliDeviceBootDelay[0]", value
    )
    with pytest.raises(ValueError, match="DaliDeviceBootDelay"):
        download.compile_configuration(data, _APP)


def test_missing_table_rejected_before_programming() -> None:
    root, blob = writer._decode(_BLOB.read_bytes())
    blob.remove(blob.findall(download._NS + "Presence")[0])
    with pytest.raises(ValueError, match="Presence"):
        download.compile_configuration(writer._encode(root), "M-0001_A-9834-11-1234")


@pytest.mark.parametrize(
    "app", ["M-0001_A-9A1B-30-0000", "M-0001_A-9837x-11-0000", "M-0083_A-9837-11-0000"]
)
def test_wrong_family_rejected(app: str) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        download.compile_configuration(_BLOB.read_bytes(), app)


def _edit(data: bytes, path: str, value: str) -> bytes:
    return writer.apply_edit(data, "siemens_blob:" + path, value)


def _value(data: bytes, path: str) -> str:
    _, blob = writer._decode(data)
    locator = writer.parse_ref_id("siemens_blob:" + path)
    assert locator is not None
    leaf = writer._resolve_locator(blob, locator)
    assert leaf is not None
    return leaf.get("Value", "")


@pytest.mark.parametrize(
    "section,item,field,target,target_item,value",
    [
        ("Group", "GroupGroup", "MaxValue", "GenericEcg", "GenericEcgEcg", "125"),
        (
            "DevType8group",
            "DevType8groupGroup",
            "MinColourTemperature",
            "DevType8",
            "DevType8Ecg",
            "2857",
        ),
        ("Minergy", "MinergyGroup", "Asignment", "Minergy", "MinergyEcg", "5"),
    ],
)
def test_group_edit_updates_only_member_properties(
    section: str, item: str, field: str, target: str, target_item: str, value: str
) -> None:
    data = _edit(
        _BLOB.read_bytes(),
        "GenericEcg[0]/GenericEcgEcg[0]/GenericEcgEcgGroupAddress[0]",
        "2",
    )
    untouched = f"{target}[0]/{target_item}[1]/{target_item}{field}[0]"
    before = _value(data, untouched)
    data = _edit(data, f"{section}[0]/{item}[2]/{item}{field}[0]", value)
    assert (
        _value(data, f"{target}[0]/{target_item}[0]/{target_item}{field}[0]") == value
    )
    assert _value(data, untouched) == before
    assert download.compile_configuration(data, _APP)


def test_group_assignment_copies_device_and_scene_settings_without_renaming() -> None:
    data = _edit(
        _BLOB.read_bytes(), "Group[0]/GroupGroup[2]/GroupGroupMaxValue[0]", "123"
    )
    data = _edit(data, "Scenes[0]/ScenesScene[0]/ScenesSceneValueGroup[2]", "01400000")
    name = _value(data, "GenericEcg[0]/GenericEcgEcg[0]/GenericEcgEcgName[0]")
    data = _edit(
        data, "GenericEcg[0]/GenericEcgEcg[0]/GenericEcgEcgGroupAddress[0]", "2"
    )
    assert (
        _value(data, "GenericEcg[0]/GenericEcgEcg[0]/GenericEcgEcgMaxValue[0]") == "123"
    )
    assert _value(data, "GenericEcg[0]/GenericEcgEcg[0]/GenericEcgEcgName[0]") == name
    assert _value(data, "Scenes[0]/ScenesScene[0]/ScenesSceneValue[0]") == "01400000"
    data = _edit(data, "Scenes[0]/ScenesScene[0]/ScenesSceneValueGroup[2]", "01200000")
    assert _value(data, "Scenes[0]/ScenesScene[0]/ScenesSceneValue[0]") == "01200000"


def test_scene_catalog_edits_update_matching_scenes_on_both_lines() -> None:
    data = _edit(
        _BLOB.read_bytes(),
        "ScenePluginInfo[0]/ScenePluginInfoScene[0]/ScenePluginInfoSceneKnxSceneNumber[0]",
        "5",
    )
    for line, slot, flags in ((0, 0, 128), (1, 3, 0)):
        data = _edit(
            data,
            f"Scenes[{line}]/ScenesScene[{slot}]/ScenesSceneSceneNumber[0]",
            str(5 | flags),
        )
    data = _edit(
        data,
        "ScenePluginInfo[0]/ScenePluginInfoScene[0]/ScenePluginInfoSceneFadeTime[0]",
        "FT_5_7S",
    )
    for line, slot in ((0, 0), (1, 3)):
        assert (
            _value(data, f"Scenes[{line}]/ScenesScene[{slot}]/ScenesSceneFadeTime[0]")
            == "FT_5_7S"
        )
    data = _edit(
        data,
        "ScenePluginInfo[0]/ScenePluginInfoScene[0]/ScenePluginInfoSceneKnxSceneNumber[0]",
        "9",
    )
    assert _value(data, "Scenes[0]/ScenesScene[0]/ScenesSceneSceneNumber[0]") == "137"
    assert _value(data, "Scenes[1]/ScenesScene[3]/ScenesSceneSceneNumber[0]") == "9"
    data = _edit(
        data,
        "ScenePluginInfo[0]/ScenePluginInfoScene[0]/ScenePluginInfoSceneKnxSceneNumber[0]",
        "255",
    )
    assert _value(data, "Scenes[0]/ScenesScene[0]/ScenesSceneSceneNumber[0]") == "255"


@pytest.mark.parametrize("number", [None, "", "bad", "255"])
def test_fade_edit_without_a_valid_scene_keeps_device_scenes(
    number: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pid 51/52 write the configuration timestamp; freeze the clock so the two compiles below
    # are compared without a second ticking over between them.
    monkeypatch.setattr(
        download,
        "datetime",
        SimpleNamespace(now=lambda: datetime(2024, 1, 1, 12, 0, 0)),
    )
    root, blob = writer._decode(_BLOB.read_bytes())
    entry = blob.find(
        download._NS + "ScenePluginInfo/" + download._NS + "ScenePluginInfoScene"
    )
    assert entry is not None
    field = entry.find(download._NS + "ScenePluginInfoSceneKnxSceneNumber")
    assert field is not None
    if number is None:
        entry.remove(field)
    else:
        field.set("Value", number)
    data = writer._encode(root)
    before = download.compile_configuration(data, _APP)
    data = _edit(
        data,
        "ScenePluginInfo[0]/ScenePluginInfoScene[0]/ScenePluginInfoSceneFadeTime[0]",
        "FT_5_7S",
    )
    after = download.compile_configuration(data, _APP)
    assert [w for w in after.writes if w.odx in (15, 16)] == [
        w for w in before.writes if w.odx in (15, 16)
    ]


@pytest.mark.parametrize("value", ["", "bad", "-1", "64"])
def test_invalid_scene_number_rejected_even_for_an_unused_slot(value: str) -> None:
    path = "ScenePluginInfo[0]/ScenePluginInfoScene[0]/ScenePluginInfoSceneKnxSceneNumber[0]"
    data = _edit(_BLOB.read_bytes(), path, "255")
    with pytest.raises(writer.EditError, match="Invalid KNX scene number"):
        _edit(data, path, value)


@pytest.mark.parametrize("value", ["", "bad", "-1", "16"])
def test_group_edit_reports_invalid_address(value: str) -> None:
    with pytest.raises(writer.EditError, match="Invalid DALI group address"):
        _edit(
            _BLOB.read_bytes(),
            "GenericEcg[0]/GenericEcgEcg[0]/GenericEcgEcgGroupAddress[0]",
            value,
        )


async def test_cancelled_update_leaves_completion_unsent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import threading

    config = _config()
    gateway = Gateway(config)
    programmer = DeviceProgrammer(gateway)
    programmer.max_apdu_length = 55
    sleep = asyncio.sleep
    this_thread = threading.get_ident()

    async def cancelled(delay: float, *args: Any, **kwargs: Any) -> Any:
        # Only the update's wait: another thread's event loop (a server another test left
        # running) sleeps as before.
        if threading.get_ident() != this_thread:
            return await sleep(delay, *args, **kwargs)
        raise asyncio.CancelledError

    monkeypatch.setattr(download.asyncio, "sleep", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await download.program_configuration(programmer, config)
    assert not any(p.object_index == 6 and p.data == b"\x10" for p in _writes(gateway))


async def test_write_failure_stops_before_device_update() -> None:
    config = _config()
    gateway = Gateway(config)
    gateway.absent_objects.add(26)
    programmer = DeviceProgrammer(gateway)
    programmer.max_apdu_length = 55
    with pytest.raises(VerificationError):
        await download.program_configuration(programmer, config)
    assert not any(isinstance(p, FunctionPropertyCommand) for p in gateway.sent)
    assert not any(p.object_index == 6 and p.data == b"\x10" for p in _writes(gateway))


@pytest.mark.parametrize("mode", ["blocked", "stale", "invalid"])
def test_service_refuses_invalid_plan_before_bus_slot(mode: str) -> None:
    from types import SimpleNamespace
    from typing import cast

    from editor_gui.plugins.connection.service import ConnectionService

    called: list[str] = []
    service = SimpleNamespace(
        writes_refused=lambda _: mode == "blocked",
        not_connected=lambda _: False,
        local_device_refused=lambda *_: False,
        begin_operation=lambda *_: called.append("bus"),
    )
    data = _BLOB.read_bytes()
    device = SimpleNamespace(
        individual_address="1.1.1",
        app=SimpleNamespace(id=_APP),
        binary_data=[
            SimpleNamespace(
                name="BLOB_PARA", data=data if mode != "stale" else b"changed"
            )
        ],
    )
    method = ConnectionService.run_siemens_dca_program.__wrapped__
    if mode == "blocked":
        assert method(cast(Any, service), device, data) is None
    else:
        if mode == "invalid":
            data = _edit(data, "DaliDevice[0]/DaliDeviceBootDelay[0]", "256")
            device.binary_data[0].data = data
        with pytest.raises(ValueError):
            method(cast(Any, service), device, data)
    assert called == []


def test_siemens_bus_write_guard_runs_before_connecting() -> None:
    from types import SimpleNamespace
    from typing import cast

    from editor_gui.plugins.connection.service import ConnectionService

    called: list[str] = []
    service = SimpleNamespace(
        writes_refused=lambda _: True,
        not_connected=lambda _: called.append("connect"),
    )
    assert (
        ConnectionService.run_siemens_dali.__wrapped__(
            cast(Any, service), None, None, writes=True
        )
        is None
    )
    assert not called


@pytest.mark.parametrize("mode", ["blocked", "stale", "invalid", "modern_blocked"])
def test_siemens_validation_precedes_automatic_connection(mode: str) -> None:
    from types import SimpleNamespace
    from typing import cast

    from editor_gui.plugins.connection.tests.test_operation_connection import _service

    service, connector = _service()
    service.block_bus_writes = mode in ("blocked", "modern_blocked")
    data = _BLOB.read_bytes()
    if mode == "invalid":
        data = _edit(data, "DaliDevice[0]/DaliDeviceBootDelay[0]", "256")
    device = cast(
        Any,
        SimpleNamespace(
            app=SimpleNamespace(id=_APP),
            binary_data=[
                SimpleNamespace(
                    name="BLOB_PARA", data=b"changed" if mode == "stale" else data
                )
            ],
        ),
    )
    if mode == "modern_blocked":
        assert service.run_siemens_dali(device, AsyncMock(), writes=True) is None
    elif mode == "blocked":
        assert service.run_siemens_dca_program(device, data) is None
    else:
        with pytest.raises(ValueError):
            service.run_siemens_dca_program(device, data)
    assert connector.users == 0
    assert not connector.opening.done()


def test_siemens_edit_during_connection_releases_the_connection() -> None:
    from types import SimpleNamespace
    from typing import cast

    from editor_gui.plugins.connection.tests.test_operation_connection import _service
    from xknx import XKNX

    service, connector = _service()
    data = _BLOB.read_bytes()
    device = cast(
        Any,
        SimpleNamespace(
            app=SimpleNamespace(id=_APP),
            individual_address="1.1.20",
            binary_data=[SimpleNamespace(name="BLOB_PARA", data=data)],
        ),
    )
    result = service.run_siemens_dca_program(device, data)
    assert result is not None and connector.users == 1
    service._xknx = XKNX()
    device.binary_data[0].data = b"changed"
    connector.opening.set_result(None)
    with pytest.raises(ValueError, match="changed while queued"):
        result.result()
    assert connector.users == 0
    assert service.busy_operation is None


def _sensor_binding():
    from editor_gui.plugin_blob_readers.siemens_dali import reader

    return next(
        s.collection
        for s in reader.read(_BLOB.read_bytes())
        if s.collection and s.collection.container_tag == "SensorList"
    )


def _empty_sensors() -> bytes:
    data = _BLOB.read_bytes()
    binding = _sensor_binding()
    _, blob = writer._decode(data)
    container = blob.find(download._NS + "SensorList")
    assert container is not None
    numbers = [
        _value(data, f"SensorList[0]/SensorListSensor[{i}]/SensorListSensorNr[0]")
        for i in range(64)
    ]
    for number in numbers:
        if number != "0":
            data = writer.remove_item(data, binding, number)
    return data


@pytest.mark.parametrize(
    "sensor_type,sections",
    [("id_03A3C2014B74", ("BinaryInput",)), ("id_0", ("Brightness", "Presence"))],
)
def test_sensor_channels_initialized_and_removed_from_download(
    sensor_type: str, sections: tuple[str, ...]
) -> None:
    binding = _sensor_binding()
    data = writer.add_item(_empty_sensors(), binding, sensor_type, "Sensor")
    for section in sections:
        data = _edit(
            data,
            f"{section}[0]/{section}Channel[0]/{section}ChannelShortAddress[0]",
            "7",
        )
        assert (
            _value(data, f"{section}[0]/{section}Channel[0]/{section}ChannelChannel[0]")
            == "1"
        )
    if sections == ("BinaryInput",):
        assert (
            _value(
                data,
                "BinaryInput[0]/BinaryInputChannel[0]/BinaryInputChannelConnectedIndex[0]",
            )
            == "1"
        )
        assert (
            _value(
                data,
                "BinaryInput[0]/BinaryInputChannel[1]/BinaryInputChannelConnectedIndex[0]",
            )
            == "0"
        )
        assert (
            _value(
                data,
                "BinaryInput[0]/BinaryInputChannel[3]/BinaryInputChannelChannelNr[0]",
            )
            == "4"
        )
    else:
        assert (
            _value(
                data, "Brightness[0]/BrightnessChannel[0]/BrightnessChannelChannelNr[0]"
            )
            == "1"
        )
        assert (
            _value(data, "Presence[0]/PresenceChannel[0]/PresenceChannelChannelNr[0]")
            == "2"
        )
    assert download.compile_configuration(data, _APP)
    data = writer.remove_item(data, binding, "1")
    config = download.compile_configuration(data, _APP)
    for section in sections:
        assert (
            _value(
                data,
                f"{section}[0]/{section}Channel[0]/{section}ChannelShortAddress[0]",
            )
            == "255"
        )
        odx = {"BinaryInput": 26, "Presence": 28, "Brightness": 30}[section]
        assert (
            next(w for w in config.writes if w.odx == odx and w.pid == 60).data[0]
            == 255
        )


def test_sensor_capacity_failure_is_atomic() -> None:
    binding = _sensor_binding()
    data = _empty_sensors()
    for _ in range(8):
        data = writer.add_item(data, binding, "id_0", "Sensor")
    with pytest.raises(writer.EditError, match="No free Brightness"):
        writer.add_item(data, binding, "id_0", "Overflow")
    assert (
        _value(data, "SensorList[0]/SensorListSensor[8]/SensorListSensorNr[0]") == "0"
    )
    assert download.compile_configuration(data, _APP)
    data = writer.remove_item(data, binding, "2")
    data = writer.add_item(data, binding, "id_0", "Reused")
    assert (
        _value(data, "SensorList[0]/SensorListSensor[1]/SensorListSensorNr[0]") == "2"
    )


def test_sensor_current_limit_includes_separate_emergency_converters() -> None:
    root, blob = writer._decode(_empty_sensors())
    ecgs = blob.find(download._NS + "GenericEcg")
    assert ecgs is not None
    for index, ecg in enumerate(ecgs):
        writer._set_leaf_value(ecg, "GenericEcgEcgNr", str(index + 1))
        writer._set_leaf_value(
            ecg,
            "GenericEcgEcgEmergencyType",
            "EMERG_TYPE_2DEV" if index < 16 else "EMERG_TYPE_NO",
        )
    data = writer._encode(root)
    binding = _sensor_binding()
    for _ in range(5):
        data = writer.add_item(data, binding, "id_0", "Sensor")
    with pytest.raises(writer.EditError, match="190 mA"):
        writer.add_item(data, binding, "id_0", "Overflow")
    data = writer.remove_item(data, binding, "1")
    data = writer.add_item(data, binding, "id_0", "Replacement")
    assert download.compile_configuration(data, _APP)


def test_sensor_address_limit_counts_addresses_per_type() -> None:
    root, blob = writer._decode(_empty_sensors())
    sensors = blob.find(download._NS + "SensorList")
    assert sensors is not None
    for index, sensor in enumerate(list(sensors)[:15]):
        writer._ensure_children(sensor, "SensorListSensorNr", "SensorListSensorId")
        writer._set_leaf_value(sensor, "SensorListSensorNr", str(index + 1))
        writer._set_leaf_value(sensor, "SensorListSensorId", "id_03A3C2014B74")
    binding = _sensor_binding()
    data = writer.add_item(writer._encode(root), binding, "id_03A3C2014B74", "Last")
    with pytest.raises(writer.EditError, match="address capacity"):
        writer.add_item(data, binding, "id_03A3C2014B74", "Overflow")
    assert (
        _value(data, "SensorList[0]/SensorListSensor[16]/SensorListSensorNr[0]") == "0"
    )


def test_unknown_sensor_type_cannot_hide_capacity_consumption() -> None:
    data = _empty_sensors()
    binding = _sensor_binding()
    data = writer.add_item(data, binding, "id_0", "Existing")
    data = _edit(
        data, "SensorList[0]/SensorListSensor[0]/SensorListSensorId[0]", "unknown"
    )
    with pytest.raises(writer.EditError, match="unknown Siemens sensor"):
        writer.add_item(data, binding, "id_0", "New")


@pytest.mark.parametrize("value", ["bad", "-1", "65"])
def test_invalid_sensor_numbers_report_edit_errors(value: str) -> None:
    binding = _sensor_binding()
    data = writer.add_item(_empty_sensors(), binding, "id_0", "Existing")
    data = _edit(data, "SensorList[0]/SensorListSensor[0]/SensorListSensorNr[0]", value)
    with pytest.raises(writer.EditError, match="Invalid Siemens sensor number"):
        writer.add_item(data, binding, "id_0", "New")
    with pytest.raises(writer.EditError, match="Invalid Siemens sensor number"):
        writer.remove_item(data, binding, value)


def test_inactive_sensor_slot_cannot_be_removed() -> None:
    with pytest.raises(writer.EditError, match="inactive Siemens sensor"):
        writer.remove_item(_empty_sensors(), _sensor_binding(), "0")


@pytest.mark.parametrize(
    "field,value", [("ShortAddress", "64"), ("GroupAddress", "16")]
)
def test_invalid_dali_address_rejected(field: str, value: str) -> None:
    root, blob = writer._decode(_BLOB.read_bytes())
    entry = blob.find(
        download._NS
        + "GenericEcg/"
        + download._NS
        + "GenericEcgEcg/"
        + download._NS
        + "GenericEcgEcg"
        + field
    )
    assert entry is not None
    entry.set("Value", value)
    with pytest.raises(ValueError, match="Invalid Siemens"):
        download.compile_configuration(writer._encode(root), _APP)
