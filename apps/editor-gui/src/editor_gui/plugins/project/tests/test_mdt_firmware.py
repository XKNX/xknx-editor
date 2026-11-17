import asyncio
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.plugin import ProjectPlugin, _unqueued
from editor_gui.plugins.project.program_queue import ProgramQueue
from editor_gui.plugins.project.service import ProjectService
from editor_gui.plugins.project.ui.mdt_firmware import MdtFirmwarePanel
from editor_gui.programming_mdt import read_firmware_version
from packages.vendor.tests.test_mdt_firmware import metadata_archive
from xknxeditor.vendor.mdt.firmware import FirmwareInfo

INFO = FirmwareInfo(131, "1.2.3", bytes(8) + b"\xff\x00", bytes(6), b"\x08\x83")


@pytest.fixture
def firmware_project(tmp_path, monkeypatch):
    project = ProjectService(CatalogService(tmp_path / "catalog.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "project.xknx")
    device = SimpleNamespace(
        node_id=1,
        individual_address="1.1.1",
        name="Test",
        order_number="AKD-0201.02",
        app=SimpleNamespace(id="M-0083_A-FFFF-01-0000", manufacturer_id="M-0083"),
    )
    monkeypatch.setattr(
        project, "find_device_by_node_id", lambda node: device if node == 1 else None
    )
    try:
        yield project, device
    finally:
        project.close()


def test_readout_has_no_project_side_effect_and_invalidates_on_identity_changes(
    firmware_project,
):
    project, device = firmware_project
    cursor, revision = project.cursor, project.revision
    view = project.mdt_firmware(1)
    assert view["installed_version"] is None and view["catalog_variants"]
    assert view["latest_compatible_version"] is None
    target = project.prepare_mdt_firmware_read(1)
    view = project.apply_mdt_firmware_read(target, INFO)
    assert view["installed_version"] == "1.2.3"
    assert view["read_at"].endswith("+00:00")
    assert view["version_property_hex"] == "0883"
    assert view["hardware_compatibility_verified"] is False
    assert (project.cursor, project.revision) == (cursor, revision)
    device.individual_address = "1.1.2"
    assert project.mdt_firmware(1)["installed_version"] is None
    with pytest.raises(ValueError, match="changed"):
        project.apply_mdt_firmware_read(target, INFO)
    device.individual_address = "1.1.1"
    assert project.mdt_firmware(1)["installed_version"] is None
    project.close()
    assert project._mdt_firmware_reads == {}


def test_catalog_import_undo_redo_and_reopen(firmware_project, tmp_path):
    project, _device = firmware_project
    before = project.mdt_firmware_catalog()
    project.import_mdt_firmware_catalog(metadata_archive(), "new.zip")
    assert project.mdt_firmware_catalog().source == "new.zip"
    assert project.undo() and project.mdt_firmware_catalog() == before
    assert project.redo() and project.mdt_firmware_catalog().source == "new.zip"
    stored = project._svc.project_user_file(project._pid, "MDT-Firmware-Catalog.json")
    assert b"firmware binary" not in stored
    cursor = project.cursor
    with pytest.raises(ValueError):
        project.import_mdt_firmware_catalog(b"bad", "bad.zip")
    assert project.cursor == cursor
    project.close()
    project.open(tmp_path / "project.xknx")
    assert project.mdt_firmware_catalog().source == "new.zip"


@pytest.mark.parametrize("failure", [None, "open", "read", "cancel"])
async def test_session_cleanup_on_success_error_and_cancellation(monkeypatch, failure):
    from editor_gui import programming_mdt

    manager = SimpleNamespace(open=AsyncMock(return_value=object()), close=AsyncMock())
    if failure == "open":
        manager.open.side_effect = OSError("No connection")
    reader = AsyncMock(return_value=INFO)
    if failure == "read":
        reader.side_effect = ValueError("Wrong manufacturer")
    elif failure == "cancel":
        reader.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(
        programming_mdt, "management_session", Mock(return_value=manager)
    )
    monkeypatch.setattr(programming_mdt, "read_firmware", reader)
    if failure:
        with pytest.raises(
            {"open": OSError, "read": ValueError, "cancel": asyncio.CancelledError}[
                failure
            ]
        ):
            await read_firmware_version(object(), "1.1.1", None)
    else:
        assert await read_firmware_version(object(), "1.1.1", None) == INFO
    manager.close.assert_awaited_once()


def test_invalid_device_never_attempts_to_connect(firmware_project):
    from editor_gui.plugins.connection.service import ConnectionService

    _, device = firmware_project
    connection = SimpleNamespace(_read_mdt_firmware=Mock())
    device.app.manufacturer_id = "M-0007"
    with pytest.raises(ValueError):
        ConnectionService.read_mdt_firmware(connection, device)
    device.app.manufacturer_id = "M-0083"
    device.individual_address = ""
    with pytest.raises(ValueError):
        ConnectionService.read_mdt_firmware(connection, device)
    connection._read_mdt_firmware.assert_not_called()


@pytest.mark.parametrize("changed", [False, True])
def test_queue_result_is_shared_and_stale_queue_never_starts(firmware_project, changed):
    project, device = firmware_project
    future = Future()
    connection = SimpleNamespace(
        read_mdt_firmware=Mock(return_value=future), xknx=object()
    )
    busy = True
    queue = ProgramQueue(is_busy=lambda: busy, start=lambda item: item.action.start())
    plugin = SimpleNamespace(
        _api=SimpleNamespace(project=project, connection=connection),
        _program_queue=queue,
    )
    cursor = project.cursor
    item = ProjectPlugin._queue_bus_action(plugin, 1, "mdt_firmware")
    if changed:
        device.individual_address = "1.1.2"
    busy = False
    queue.tick()
    if changed:
        connection.read_mdt_firmware.assert_not_called()
        assert "changed" in item.ended.result().message
    else:
        future.set_result(INFO)
        assert item.ended.result().result == project.mdt_firmware(1)
    assert project.cursor == cursor
    assert _unqueued(("mdt_firmware", "1.1.1")) is None


def test_panel_never_autoreads_and_picker_uses_project_generation(
    firmware_project, monkeypatch, tmp_path
):
    from imgui_bundle import imgui

    from editor_gui.plugins.project.strings import S

    project, device = firmware_project
    queue = Mock(return_value=None)
    panel = MdtFirmwarePanel(queue)
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        selected = None
        monkeypatch.setattr(imgui, "button", lambda label: label == selected)
        for selected in (None, S.MDT_FIRMWARE_READ):
            imgui.new_frame()
            panel.render(device, project, default_open=True)
            imgui.end_frame()
            assert queue.call_count == (0 if selected is None else 1)
        queue.assert_called_once_with(1, "mdt_firmware")
        path = tmp_path / "catalog.zip"
        path.write_bytes(metadata_archive())
        panel._open = SimpleNamespace(ready=lambda: True, result=lambda: [str(path)])
        panel._import_target = (-1, project.revision)
        cursor = project.cursor
        panel._poll(project)
        assert project.cursor == cursor and panel._error == S.MDT_IMPORT_STALE
    finally:
        imgui.destroy_context(context)


@pytest.mark.parametrize(
    ("mapped", "comparable", "dca_kind"),
    [
        (False, False, None),
        (True, False, None),
        (False, True, None),
        (False, False, "symbols"),
        (False, False, "layout"),
        (False, False, "visucontrol"),
    ],
)
def test_firmware_opens_only_without_other_plugin_configuration(
    mapped, comparable, dca_kind
):
    device = SimpleNamespace(node_id=1)
    project = SimpleNamespace(
        plugin_mapping=lambda _device: object() if mapped else None,
        mdt_dca_profile=lambda _device: {"kind": dca_kind} if dca_kind else None,
    )
    plugin = SimpleNamespace(
        _api=SimpleNamespace(project=project),
        _poll_triton_prj_dialog=Mock(),
        _poll_triton_prj_save=Mock(),
        _has_plugin_comparison=lambda _device: comparable,
        _mdt_firmware_panel=Mock(),
        _mdt_dca_panel=Mock(),
        _native_plugin_panel=Mock(),
        _generic_plugin_panel=Mock(),
        _plugin_params_panel=Mock(),
    )
    plugin._has_plugin_configuration = lambda selected: (
        ProjectPlugin._has_plugin_configuration(plugin, selected)
    )
    ProjectPlugin._render_plugin_params_tab(plugin, device)
    plugin._mdt_firmware_panel.render.assert_called_once_with(
        device, project, default_open=not (mapped or comparable or dca_kind)
    )


@pytest.mark.parametrize("default_open", [False, True])
def test_firmware_expansion_is_per_device_and_keeps_manual_toggles(
    monkeypatch, default_open
):
    from imgui_bundle import imgui

    project = SimpleNamespace(generation=1, has_mdt_firmware=lambda _device: True)
    device = SimpleNamespace(node_id=1, individual_address="1.1.1")
    other = SimpleNamespace(node_id=2, individual_address="1.1.2")
    panel = MdtFirmwarePanel(Mock())
    body = Mock()
    monkeypatch.setattr(panel, "_render", body)
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")

        def frame(selected, initial):
            body.reset_mock()
            imgui.new_frame()
            imgui.set_next_window_pos(imgui.ImVec2(0, 0))
            imgui.set_next_window_size(imgui.ImVec2(800, 600))
            imgui.begin("Plugin Config", None, imgui.WindowFlags_.no_title_bar)
            panel.render(selected, project, default_open=initial)
            low = imgui.get_item_rect_min()
            point = (low.x + 10, low.y + 10)
            imgui.end()
            imgui.end_frame()
            return body.called, point

        assert frame(device, default_open)[0] == default_open
        assert frame(other, not default_open)[0] == (not default_open)
        opened, point = frame(device, default_open)
        assert opened == default_open
        io.add_mouse_pos_event(*point)
        frame(device, default_open)
        io.add_mouse_button_event(0, True)
        frame(device, default_open)
        io.add_mouse_button_event(0, False)
        assert frame(device, default_open)[0] == (not default_open)
        assert frame(other, not default_open)[0] == (not default_open)
        assert frame(device, default_open)[0] == (not default_open)
        device.individual_address = "1.1.3"
        assert frame(device, default_open)[0] == (not default_open)
        project.generation += 1
        assert frame(device, default_open)[0] == default_open
    finally:
        imgui.destroy_context(context)


def test_catalog_evaluation_is_cached_but_reads_refresh_without_a_revision(
    firmware_project, monkeypatch
):
    from xknxeditor.vendor.mdt.firmware import FirmwareCatalog

    project, _device = firmware_project
    original = FirmwareCatalog.for_product
    calls = []

    def lookup(self, product):
        calls.append(product)
        return original(self, product)

    monkeypatch.setattr(FirmwareCatalog, "for_product", lookup)
    for _ in range(3):
        assert project.mdt_firmware(1)["installed_version"] is None
    target = project.prepare_mdt_firmware_read(1)
    project.apply_mdt_firmware_read(target, INFO)
    assert project.mdt_firmware(1)["installed_version"] == "1.2.3"
    assert len(calls) == 2
    project.import_mdt_firmware_catalog(metadata_archive(), "new.zip")
    assert project.mdt_firmware(1)["catalog_variants"] == []
    assert len(calls) == 3


def test_led_controller_read_does_not_recommend_another_hardware_revision(
    firmware_project, monkeypatch
):
    from dataclasses import replace

    from imgui_bundle import imgui

    from editor_gui.plugins.project.strings import S

    project, device = firmware_project
    device.order_number = "AKD-0424R.02"
    view = project.apply_mdt_firmware_read(
        project.prepare_mdt_firmware_read(1),
        replace(
            INFO, version="4.10", bsl_info=bytes.fromhex("0100000c83000440") + bytes(20)
        ),
    )
    assert view["latest_compatible_version"] == "4.10"
    assert view["compatibility_status"] == "up_to_date"
    assert "latest_listed_version" not in view
    assert "releases" not in view
    assert {v["hardware_key"] for v in view["catalog_variants"]} == {
        "83000340",
        "83000440",
        "83000540",
        "83001440",
        "83001540",
    }
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(1200, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        texts = []
        monkeypatch.setattr(imgui, "text_wrapped", texts.append)
        imgui.new_frame()
        MdtFirmwarePanel(Mock())._render(device, project)
        imgui.end_frame()
        assert f"{S.MDT_FIRMWARE_INSTALLED}: 4.10" in texts
        assert f"{S.MDT_FIRMWARE_COMPATIBLE}: 4.10" in texts
        assert not any("5.9" in text or "3.15" in text for text in texts)
    finally:
        imgui.destroy_context(context)


@pytest.mark.parametrize(
    "failure",
    [None, "descriptor", "wrong_property", "device_error", "missing", "cancel"],
)
@pytest.mark.parametrize("return_code", [0, 1])
async def test_firmware_wire_requests_with_bus_writes_blocked(
    monkeypatch, failure, return_code
):
    from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
    from xknx.cemi.const import CEMIMessageCode
    from xknx.exceptions import ManagementConnectionError
    from xknx.telegram import IndividualAddress, Telegram, apci, tpci

    from editor_gui import programming_mdt
    from editor_gui.plugins.connection.write_guard import check_frame, check_telegram
    from packages.vendor.tests.test_mdt_firmware import (
        PropertyTransport,
        bsl_info,
        packets,
    )

    address = IndividualAddress("1.1.1")
    replies = packets(bsl_info())

    class Transport(PropertyTransport):
        async def request(self, payload, response_type):
            telegram = Telegram(destination_address=address, payload=payload)
            check_telegram(lambda: True, telegram)
            check_frame(
                lambda: True,
                CEMIFrame(
                    code=CEMIMessageCode.L_DATA_REQ,
                    data=CEMILData(
                        src_addr=IndividualAddress("0.0.0"),
                        dst_addr=address,
                        tpci=tpci.TDataIndividual(),
                        payload=payload,
                    ),
                ),
            )
            if isinstance(payload, apci.PropertyValueRead):
                return await super().request(payload, response_type)
            self.calls.append(payload)
            if isinstance(payload, apci.PropertyDescriptionRead):
                assert (payload.object_index, payload.property_id) == (0, 242)
                result = apci.PropertyDescriptionResponse(
                    object_index=0,
                    property_id=242,
                    type_=62,
                    max_count=0 if failure == "descriptor" else 1,
                )
            else:
                assert type(payload) is apci.FunctionPropertyCommand
                assert (payload.object_index, payload.property_id) == (0, 242)
                assert payload.data[:2] == b"\x00\x11"
                assert len(payload.data) == 3
                if failure == "missing":
                    raise ManagementConnectionError("Identity timeout")
                if failure == "cancel":
                    raise asyncio.CancelledError()
                result = apci.FunctionPropertyStateResponse(
                    object_index=0,
                    property_id=241 if failure == "wrong_property" else 242,
                    return_code=return_code,
                    data=b"\xff"
                    if failure == "device_error"
                    else replies[payload.data[2]],
                )
            assert isinstance(result, response_type)
            return Telegram(destination_address=address, payload=result)

    transport = Transport(legacy=True)
    transport.values[15] = bytes(8) + b"\x00\x4a"
    manager = SimpleNamespace(open=AsyncMock(return_value=transport), close=AsyncMock())
    monkeypatch.setattr(
        programming_mdt, "management_session", Mock(return_value=manager)
    )
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await read_firmware_version(object(), "1.1.1", None)
    else:
        info = await read_firmware_version(object(), "1.1.1", None)
        assert info.version == "4.10"
        if failure:
            assert info.hardware_key is None and info.identity_error
        else:
            assert info.hardware_key.hex() == "83000440"
            assert info.bsl_info == bsl_info()
            assert info.identity_error is None
            assert [
                p.data
                for p in transport.calls
                if isinstance(p, apci.FunctionPropertyCommand)
            ] == [bytes((0, 0x11, i)) for i in range(4)]
        if failure == "descriptor":
            assert not any(
                isinstance(p, apci.FunctionPropertyCommand) for p in transport.calls
            )
    manager.close.assert_awaited_once()
    from editor_gui.plugins.connection.write_guard import BusWritesBlocked

    with pytest.raises(BusWritesBlocked):
        check_telegram(
            lambda: True,
            Telegram(
                destination_address=address,
                payload=apci.FunctionPropertyCommand(
                    object_index=0, property_id=242, data=b"\x00\x11\x00"
                ),
            ),
        )


@pytest.mark.parametrize(
    "key,installed,status,latest",
    [
        ("83000440", "4.9", "update_available", "4.10"),
        ("83001440", "4.10-beta", "update_available", "4.10"),
        ("83000440", "4.11", "up_to_date", "4.10"),
        ("83000440", None, "installed_version_unknown", "4.10"),
        ("83000440", "0.0", "installed_version_unknown", "4.10"),
        ("83000000", "4.10", "no_compatible_release", None),
        (None, "4.10", "hardware_identity_not_read", None),
    ],
)
def test_firmware_comparison_status_and_catalog_invalidation(
    firmware_project, key, installed, status, latest
):
    from dataclasses import replace

    from packages.vendor.tests.test_mdt_firmware import bsl_info

    project, device = firmware_project
    device.order_number = "AKD-0424R.02"
    info = replace(INFO, version=installed, bsl_info=bsl_info(key) if key else None)
    view = project.apply_mdt_firmware_read(project.prepare_mdt_firmware_read(1), info)
    assert view["compatibility_status"] == status
    assert view["latest_compatible_version"] == latest
    assert view["hardware_compatibility_verified"] == (latest is not None)
    project.import_mdt_firmware_catalog(metadata_archive(), "new.zip")
    assert project.mdt_firmware(1)["latest_compatible_version"] is None
    assert project.mdt_firmware(1)["installed_version"] == installed
    assert project.undo()
    assert project.mdt_firmware(1)["latest_compatible_version"] == latest


async def test_live_led_controller_identity_replies(monkeypatch):
    from xknx.cemi.cemi_frame import CEMIFrame
    from xknx.telegram import IndividualAddress, Telegram, apci

    from editor_gui import programming_mdt
    from packages.vendor.tests.test_mdt_firmware import PropertyTransport
    from xknxeditor.vendor.mdt.firmware import bundled_catalog

    frames = [
        "2900b060110d11220f52c900f2019104000700010032830004",
        "2900b060110d11220f56c900f2019104010740020000000044",
        "2900b060110d11220f5ac900f201910402070000000000006e",
        "2900b060110d11220f5ec900f20191040307a0000000980000",
    ]

    class Transport(PropertyTransport):
        async def request(self, payload, response_type):
            if isinstance(payload, apci.PropertyValueRead):
                return await super().request(payload, response_type)
            if isinstance(payload, apci.PropertyDescriptionRead):
                response = apci.PropertyDescriptionResponse(
                    object_index=0, property_id=242, type_=0xBE, max_count=1
                )
            else:
                assert type(payload) is apci.FunctionPropertyCommand
                assert payload.data[:2] == b"\x00\x11" and len(payload.data) == 3
                response = CEMIFrame.from_knx(
                    bytes.fromhex(frames[payload.data[2]])
                ).data.payload
            assert isinstance(response, response_type)
            return Telegram(
                destination_address=IndividualAddress("1.1.34"), payload=response
            )

    transport = Transport(legacy=True)
    transport.values[15] = bytes.fromhex("213034323452ff020f4a")
    transport.values[78] = bytes.fromhex("000000000340")
    manager = SimpleNamespace(open=AsyncMock(return_value=transport), close=AsyncMock())
    monkeypatch.setattr(
        programming_mdt, "management_session", Mock(return_value=manager)
    )
    info = await read_firmware_version(object(), "1.1.13", None)
    assert info.hardware_key.hex() == "83000440"
    assert info.version == "4.10" and info.identity_error is None
    assert (
        info.bsl_info.hex()
        == "00010032830004400200000000440000000000006ea0000000980000"
    )
    assert (
        bundled_catalog().compatible_releases("AKD-0424R.02", info)[-1].version
        == "4.10"
    )


@pytest.mark.parametrize("has_identity", [False, True])
def test_panel_displays_read_identity_and_matching_catalog_metadata(
    firmware_project, monkeypatch, has_identity
):
    from dataclasses import replace

    from imgui_bundle import imgui

    from editor_gui.local_time import stamp_text
    from editor_gui.plugins.project.strings import S
    from editor_gui.plugins.project.ui import mdt_firmware

    project, device = firmware_project
    device.order_number = "AKD-0424R.02"
    captured = "00010032830004400200000000440000000000006ea0000000980000"
    info = replace(
        INFO,
        version="4.10",
        hardware_type=bytes.fromhex("000000000340"),
        order_info=bytes.fromhex("213034323452ff020f4a"),
        version_property=None,
        bsl_info=bytes.fromhex(captured) if has_identity else None,
    )
    cursor, revision = project.cursor, project.revision
    view = project.apply_mdt_firmware_read(project.prepare_mdt_firmware_read(1), info)
    expected = {
        "version_code": 256,
        "crc_type_code": 0,
        "max_telegram_length": 50,
        "encryption_type_code": 2,
    }
    assert view["bootloader"] == (expected if has_identity else None)
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(500, 800)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        texts = []
        monkeypatch.setattr(imgui, "text_wrapped", texts.append)
        labels = S.MDT_FIRMWARE_FIELDS
        for details_open in (False, True):
            monkeypatch.setattr(
                mdt_firmware, "collapsing_header", lambda _, opened=details_open: opened
            )
            texts.clear()
            imgui.new_frame()
            MdtFirmwarePanel._render_info(view)
            imgui.end_frame()
            assert f"{S.DEVICE_INFO_HARDWARE}: 000000000340" in texts
            if has_identity:
                assert f"{labels['hardware_key']}: 83000440" in texts
                assert f"{labels['version_code']}: 0x0100" in texts
                assert f"{labels['max_telegram_length']}: 50" in texts
                release = view["compatible_releases"][-1]
                assert f"{labels['filename']}: AKD-0424R.02_5326_R410.hex" in texts
                assert (
                    f"{labels['modified']}: {stamp_text(release['modified'])}" in texts
                )
                assert (f"{labels['crc_type_code']}: 0x00" in texts) == details_open
                assert (
                    f"{labels['encryption_type_code']}: 0x02" in texts
                ) == details_open
                assert (
                    f"{labels['bsl_info_hex']}: {captured}" in texts
                ) == details_open
            else:
                assert (
                    f"{labels['hardware_key']}: {S.MDT_FIRMWARE_UNDETERMINED}" in texts
                )
                assert not any(
                    labels["filename"] in text or labels["crc_type_code"] in text
                    for text in texts
                )
            assert not any("5.9" in text for text in texts)
    finally:
        imgui.destroy_context(context)
    assert (project.cursor, project.revision) == (cursor, revision)
    device.individual_address = "1.1.2"
    assert project.mdt_firmware(1)["bootloader"] is None
