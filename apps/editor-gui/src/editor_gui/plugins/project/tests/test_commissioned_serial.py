"""A full download that gave the device its address in programming mode records the serial
number read from that device, also over another one stored for it."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any

from editor_gui.plugins.project.plugin import ProjectPlugin
from xknxeditor.download.download import DownloadResult
from xknxeditor.download.scope import DownloadScope


def _recorded(result: object) -> list[tuple[int, str, object]]:
    device = SimpleNamespace(
        node_id=7, individual_address="1.0.58", name="IP-Interface"
    )
    done: Future[Any] = Future()
    done.set_result(result)
    plugin: Any = ProjectPlugin.__new__(ProjectPlugin)
    plugin._api = SimpleNamespace(
        connection=SimpleNamespace(
            keyring=None, program_device=lambda *args, **kwargs: done
        ),
        log=SimpleNamespace(debug=lambda *args, **kwargs: None),
        project=SimpleNamespace(
            find_device_by_node_id=lambda node_id: device,
            download_tool_key=lambda d: None,
            ip_secure_config=lambda d, keyring: None,
            download_ip_config=lambda d, parts: None,
            download_disables_security=lambda d: False,
            download_routing_blocking=lambda d: None,
        ),
    )
    plugin._group_communication_for = lambda d: None
    recorded: list[tuple[int, str, object]] = []
    plugin._record_commissioning = lambda node_id, scope, serial, *args, **kwargs: (
        recorded.append((node_id, scope, serial))
    )
    item = SimpleNamespace(
        script=None,
        action=None,
        device_info=None,
        scope=DownloadScope.FULL,
        test=False,
        node_id=7,
        medium_config=False,
        without_plugin=False,
        current_address=None,
        serial_number=None,
        address="1.0.58",
        name="IP-Interface",
        origin=None,
    )
    assert plugin._start_queued(item) is done
    return recorded


def test_the_serial_number_read_in_programming_mode_is_recorded() -> None:
    serial = bytes.fromhex("00FAD6B25D85")
    assert _recorded(DownloadResult(serial_number=serial)) == [(7, "full", serial)]


def test_no_serial_number_without_one_read() -> None:
    assert _recorded(DownloadResult()) == [(7, "full", None)]
    # an address download returns the serial number itself
    serial = bytes.fromhex("00FAD6B25D85")
    assert _recorded(serial) == [(7, "full", serial)]
