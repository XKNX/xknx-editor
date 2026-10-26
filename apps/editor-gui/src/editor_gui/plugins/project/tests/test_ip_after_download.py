"""After a download, an IP device whose address is assigned automatically is looked for on the
network, and the address it answers from is kept."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.project.plugin import ProjectPlugin


def _plugin(serial: bytes | None, search: Future[str | None] | None) -> Any:
    kept: list[tuple[int, str]] = []
    searched: list[bytes] = []

    def find(found_serial: bytes) -> Future[str | None] | None:
        searched.append(found_serial)
        return search

    plugin = cast(Any, object.__new__(ProjectPlugin))
    plugin._api = SimpleNamespace(
        main_thread=None,
        project=SimpleNamespace(
            serial_to_find_ip=lambda _node_id: serial,
            record_found_ip=lambda node_id, address: kept.append((node_id, address)),
        ),
        connection=SimpleNamespace(find_device_ip=find),
    )
    plugin.kept = kept
    plugin.searched = searched
    return plugin


def test_the_found_address_is_kept() -> None:
    search: Future[str | None] = Future()
    plugin = _plugin(b"\x00\xfa\x12\x34\x56\x78", search)
    plugin._find_ip_after_download(7)
    assert plugin.searched == [b"\x00\xfa\x12\x34\x56\x78"]
    assert plugin.kept == []
    search.set_result("192.168.16.103")
    assert plugin.kept == [(7, "192.168.16.103")]


def test_nothing_is_kept_when_the_device_does_not_answer() -> None:
    search: Future[str | None] = Future()
    plugin = _plugin(b"\x00\xfa\x12\x34\x56\x78", search)
    plugin._find_ip_after_download(7)
    search.set_result(None)
    failed: Future[str | None] = Future()
    plugin = _plugin(b"\x00\xfa\x12\x34\x56\x78", failed)
    plugin._find_ip_after_download(7)
    failed.set_exception(OSError("no network"))
    assert plugin.kept == []


def test_no_search_without_a_device_to_find() -> None:
    plugin = _plugin(None, Future())
    plugin._find_ip_after_download(7)
    assert plugin.searched == []
