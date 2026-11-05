"""Expanded registration and settings retain unique widget IDs at desktop and phone widths."""

from __future__ import annotations

import threading
from concurrent.futures import Future
from pathlib import Path

import httpx
import pytest
from imgui_bundle import imgui

from editor_gui.plugins.catalog.alternative import (
    AlternativeCatalogClient,
    AlternativeSettings,
)
from editor_gui.plugins.catalog.strings import S
from editor_gui.plugins.catalog.ui.alternative import AlternativeCatalogPanel
from editor_gui.strings import S as AppS

from .test_alternative import index, large_index


@pytest.mark.parametrize("width", [360, 1600])
def test_expanded_forms_and_product_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    monkeypatch.setattr("editor_gui.settings.config_dir", lambda: tmp_path)
    client = AlternativeCatalogClient(
        tmp_path,
        settings=AlternativeSettings("https://catalog.example"),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=index())),
    )
    client.refresh()
    panel = AlternativeCatalogPanel(client, lambda _id: 1)
    panel._selected = client.search()[0][0]
    context = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(width, 1500)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    original_button = imgui.button
    original_header = imgui.collapsing_header
    ids: dict[int, str] = {}
    columns: list[str] = []
    widths: list[float] = []
    original_column = imgui.table_setup_column

    def record(label: str) -> None:
        widget_id = imgui.get_id(label)
        assert widget_id not in ids, (
            f"conflicting IDs: {label} and {ids.get(widget_id)}"
        )
        ids[widget_id] = label

    def button(label, *args, **kwargs):
        record(label)
        return original_button(label, *args, **kwargs)

    def header(label, *args, **kwargs):
        record(label)
        imgui.set_next_item_open(True, imgui.Cond_.always)
        return original_header(label, *args, **kwargs)

    def column(label, *args, **kwargs):
        columns.append(label)
        widths.append(args[1] if len(args) > 1 else 0)
        return original_column(label, *args, **kwargs)

    monkeypatch.setattr(imgui, "button", button)
    monkeypatch.setattr(imgui, "collapsing_header", header)
    monkeypatch.setattr(imgui, "table_setup_column", column)
    try:
        for _ in range(3):
            ids.clear()
            columns.clear()
            widths.clear()
            imgui.new_frame()
            imgui.set_next_window_size(imgui.ImVec2(width, 1450), imgui.Cond_.always)
            imgui.set_next_window_pos(imgui.ImVec2(0, 0), imgui.Cond_.always)
            imgui.begin("Catalog")
            panel.render()
            assert AppS.SETTINGS_TITLE in ids.values()
            assert list(ids.values()).count(S.ALTERNATIVE_REGISTER) == 2
            assert all(
                label in columns
                for label in (
                    S.ALTERNATIVE_FILE,
                    S.CATALOG_DETAIL_APPLICATION,
                    S.ALTERNATIVE_VERSION,
                    S.ALTERNATIVE_MASK,
                    S.ALTERNATIVE_SIZE,
                    S.ALTERNATIVE_UPDATED,
                )
            )
            assert columns[:4] == [
                S.CATALOG_DETAIL_MANUFACTURER,
                S.CATALOG_DETAIL_NAME,
                S.CATALOG_DETAIL_ORDER,
                S.ALTERNATIVE_VERSION,
            ]
            assert 0 < widths[0] <= 100
            assert columns[4] == S.ALTERNATIVE_FILE
            assert 0 < widths[3] < 80
            assert 0 < widths[1] <= 200
            imgui.end()
            imgui.end_frame()
    finally:
        imgui.destroy_context(context)
        panel.stop()


def test_download_imports_on_polling_thread_and_shutdown_discards_result(
    tmp_path: Path,
) -> None:
    calls: list[tuple[int, bytes]] = []
    c = AlternativeCatalogClient(
        tmp_path,
        settings=AlternativeSettings("https://catalog.example"),
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    panel = AlternativeCatalogPanel(
        c, lambda data: calls.append((threading.get_ident(), data)) or 1
    )
    panel._submit("download", lambda: b"verified knxprod")
    assert panel._future is not None
    panel._future.result(timeout=2)
    assert calls == []
    panel.poll()
    assert calls == [(threading.get_ident(), b"verified knxprod")]
    pending: Future[bytes] = Future()
    pending.set_result(b"must not import after shutdown")
    panel._future = pending
    panel.stop()
    panel.poll()
    assert len(calls) == 1


def test_shutdown_does_not_wait_for_stalled_network(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def serve(_r):
        entered.set()
        assert release.wait(5)
        finished.set()
        return httpx.Response(200, json=index())

    c = AlternativeCatalogClient(
        tmp_path,
        settings=AlternativeSettings("https://catalog.example"),
        transport=httpx.MockTransport(serve),
    )
    panel = AlternativeCatalogPanel(
        c, lambda _data: pytest.fail("imported at shutdown")
    )
    panel._submit("refresh", c.refresh)
    assert entered.wait(2)
    stopped = threading.Event()
    thread = threading.Thread(target=lambda: (panel.stop(), stopped.set()))
    thread.start()
    try:
        assert stopped.wait(0.5)
    finally:
        release.set()
        thread.join(2)
        assert finished.wait(2)


@pytest.mark.parametrize("width", [360, 1600])
def test_all_results_scroll_select_and_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    monkeypatch.setattr("editor_gui.settings.config_dir", lambda: tmp_path)
    client = AlternativeCatalogClient(
        tmp_path,
        settings=AlternativeSettings("https://catalog.example"),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=large_index())
        ),
    )
    client.refresh()
    panel = AlternativeCatalogPanel(client, lambda _data: 1)
    context = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(width, 700)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    rendered: list[str] = []
    positions: dict[str, tuple[float, float]] = {}
    texts: list[str] = []
    scroll: list[float] = []
    move_to_bottom = False
    original_selectable = imgui.selectable
    original_text = imgui.text_unformatted
    original_headers = imgui.table_headers_row

    def selectable(label, *args, **kwargs):
        rendered.append(label)
        result = original_selectable(label, *args, **kwargs)
        lo, hi = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        positions[label] = (150, (lo.y + hi.y) / 2)
        return result

    def text(value, *args, **kwargs):
        texts.append(value)
        return original_text(value, *args, **kwargs)

    def headers(*args, **kwargs):
        original_headers(*args, **kwargs)
        scroll[:] = [imgui.get_scroll_y(), imgui.get_scroll_max_y()]
        if move_to_bottom:
            imgui.set_scroll_y(scroll[1])

    monkeypatch.setattr(imgui, "selectable", selectable)
    monkeypatch.setattr(imgui, "text_unformatted", text)
    monkeypatch.setattr(imgui, "table_headers_row", headers)

    def frame() -> None:
        rendered.clear()
        texts.clear()
        positions.clear()
        imgui.new_frame()
        imgui.set_next_window_size(imgui.ImVec2(width, 700), imgui.Cond_.always)
        imgui.set_next_window_pos(imgui.ImVec2(0, 0), imgui.Cond_.always)
        imgui.begin("Catalog")
        panel.render()
        imgui.end()
        imgui.end_frame()
        assert len(rendered) < 60
        assert not any("\n" in text for text in rendered + texts)

    try:
        for _ in range(3):
            frame()
        assert len(panel._entries) == 1205
        assert S.ONLINE_MATCH_COUNT.format(count=1205) in texts
        assert "Product 0" in rendered and "Product 1204" not in rendered
        move_to_bottom = True
        frame()
        move_to_bottom = False
        for _ in range(3):
            frame()
        assert scroll[0] > 10000
        assert "Product 1204" in rendered
        io.add_mouse_pos_event(*positions["Product 1204"])
        io.add_mouse_button_event(0, True)
        frame()
        io.add_mouse_button_event(0, False)
        frame()
        assert panel._selected is not None
        assert panel._selected.id == "product-1204"
        panel._query = "missing"
        for _ in range(3):
            frame()
        assert panel._entries == [] and rendered == []
        assert S.ONLINE_MATCH_COUNT.format(count=0) in texts
        panel._query = ""
        for _ in range(3):
            frame()
        assert "Product 0" in rendered and scroll[0] == 0
    finally:
        imgui.destroy_context(context)
        panel.stop()
