"""Alternative catalog tab shared by the native and browser editors."""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from imgui_bundle import imgui

from editor_gui.os_open import open_url
from editor_gui.plugins.catalog.alternative import (
    AlternativeCatalogClient,
    AlternativeEntry,
    program_version_text,
)
from editor_gui.plugins.catalog.strings import S
from editor_gui.strings import S as AppS
from editor_gui.widgets.dpi import px
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.tables import begin_data_table, data_table_headers


class AlternativeCatalogPanel:
    def __init__(
        self, client: AlternativeCatalogClient, import_product: Callable[[bytes], int]
    ) -> None:
        self._client = client
        self._import_product = import_product
        self._base_url = client.settings.base_url
        self._user = client.settings.user
        self._password = ""
        self._email = ""
        self._repeat = ""
        self._code = ""
        self._query = ""
        self._search_key: tuple[str, int] | None = None
        self._entries: list[AlternativeEntry] = []
        self._selected: AlternativeEntry | None = None
        self._message = ""
        self._registration = ""
        self._settings_key = (client.settings.base_url, client.settings.user)
        self._future: Future[Any] | None = None
        self._action = ""
        self._stop = threading.Event()
        self._refresh_thread: threading.Thread | None = None

    def start(self) -> None:
        """Refresh on startup and hourly, keeping the cached catalog usable on failure."""
        if self._refresh_thread is not None:
            return

        def refresh() -> None:
            while not self._stop.is_set():
                with contextlib.suppress(Exception):
                    self._client.refresh()
                if self._stop.wait(3600):
                    break

        self._refresh_thread = threading.Thread(
            target=refresh, name="alternative-index", daemon=True
        )
        self._refresh_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._future is not None:
            self._future.cancel()
            self._future = None
        self._client.close()

    def _submit(self, action: str, fn: Callable[[], Any]) -> None:
        if self._future is None and not self._stop.is_set():
            self._action = action
            self._message = ""
            future: Future[Any] = Future()
            self._future = future

            def work() -> None:
                if not future.set_running_or_notify_cancel():
                    return
                try:
                    future.set_result(fn())
                except Exception as exc:
                    future.set_exception(exc)

            threading.Thread(
                target=work, name="alternative-catalog", daemon=True
            ).start()

    def poll(self) -> None:
        if self._future is None or not self._future.done():
            return
        try:
            result = self._future.result()
            if self._action == "download":
                self._message = S.ALTERNATIVE_IMPORTED.format(
                    count=self._import_product(result)
                )
            elif self._action == "registration":
                self._registration = result["status"]
                self._message = result["message"]
                self._user = self._client.settings.user
                self._password = self._repeat = self._code = ""
            else:
                self._message = ""
        except Exception as exc:
            self._message = str(exc)
        finally:
            self._future = None

    def render(self) -> None:
        self.poll()
        settings_key = self._client.settings.base_url, self._client.settings.user
        if settings_key != self._settings_key:
            self._base_url, self._user = settings_key
            self._password = ""
            self._settings_key = settings_key
        imgui.begin_disabled(self._future is not None)
        if imgui.collapsing_header(AppS.SETTINGS_TITLE):
            self._render_settings()
        if imgui.collapsing_header(S.ALTERNATIVE_REGISTER):
            imgui.push_id("registration_form")
            self._render_registration()
            imgui.pop_id()
        if imgui.button(S.BTN_ONLINE_CATALOG):
            self._submit("refresh", self._client.refresh)
        same_line_if_fits(button_width(S.ALTERNATIVE_WEBSITE))
        if imgui.button(S.ALTERNATIVE_WEBSITE):
            open_url(self._client.settings.base_url)
        imgui.end_disabled()
        if self._future is not None:
            imgui.text_unformatted(S.ONLINE_LOADING_PRODUCTS)
        status = self._client.status()
        if self._message or status["error"]:
            imgui.text_wrapped(self._message or status["error"])
        if self._registration == "unverified":
            imgui.text_wrapped(S.ALTERNATIVE_VERIFY_HINT)
        elif self._registration == "pending":
            imgui.text_wrapped(S.ALTERNATIVE_PENDING)
        if status["generated_at"]:
            imgui.text_unformatted(status["generated_at"])
        self._query = filter_box(
            "##alternative_search", S.ONLINE_SEARCH_HINT, self._query
        )
        key = self._query, self._client.generation
        reset_scroll = key != self._search_key
        if key != self._search_key:
            self._entries, _ = self._client.search(self._query)
            if self._search_key is not None and self._search_key[1] != key[1]:
                self._selected = None
            self._search_key = key
        imgui.text_unformatted(S.ONLINE_MATCH_COUNT.format(count=len(self._entries)))
        if self._selected is not None:
            self._render_details(self._selected)
        flags = (
            imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.sizing_fixed_fit
        )
        if begin_data_table("##alternative_products_v5", 9, flags, min_width=1500):
            if reset_scroll:
                imgui.set_scroll_y(0)
                imgui.set_scroll_x(0)
            for name, width in (
                (
                    S.CATALOG_DETAIL_MANUFACTURER,
                    imgui.calc_text_size(S.CATALOG_DETAIL_MANUFACTURER).x / px(1) + 8,
                ),
                (S.CATALOG_DETAIL_NAME, 180),
                (S.CATALOG_DETAIL_ORDER, 170),
                (
                    S.ALTERNATIVE_VERSION,
                    imgui.calc_text_size(S.ALTERNATIVE_VERSION).x / px(1) + 8,
                ),
                (S.ALTERNATIVE_FILE, 260),
                (S.CATALOG_DETAIL_APPLICATION, 90),
                (S.ALTERNATIVE_MASK, 90),
                (S.ALTERNATIVE_SIZE, 110),
                (S.ALTERNATIVE_UPDATED, 180),
            ):
                imgui.table_setup_column(
                    name, imgui.TableColumnFlags_.width_fixed, px(width)
                )
            data_table_headers()
            clipper = imgui.ListClipper()
            clipper.begin(len(self._entries))
            while clipper.step():
                for i in range(clipper.display_start, clipper.display_end):
                    entry = self._entries[i]
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.text_unformatted(" ".join(entry.manufacturer.splitlines()))
                    imgui.table_next_column()
                    imgui.push_id(entry.id)
                    if imgui.selectable(
                        " ".join(entry.name.splitlines()),
                        self._selected == entry,
                        imgui.SelectableFlags_.span_all_columns,
                    )[0]:
                        self._selected = entry
                    imgui.pop_id()
                    for value in (
                        ", ".join(entry.order_numbers),
                        ", ".join(
                            program_version_text(p["version"]) for p in entry.programs
                        ),
                        entry.file_name,
                        ", ".join(p["applicationNumber"] for p in entry.programs),
                        ", ".join(p["maskVersion"] for p in entry.programs),
                        f"{entry.size:,} B",
                        entry.updated,
                    ):
                        imgui.table_next_column()
                        imgui.text_unformatted(" ".join(value.splitlines()))
            imgui.end_table()

    def _render_settings(self) -> None:
        for label, attr, flags in (
            (S.ALTERNATIVE_URL, "_base_url", 0),
            (S.ALTERNATIVE_USER, "_user", 0),
            (AppS.PASSWORD_LABEL, "_password", imgui.InputTextFlags_.password),
        ):
            imgui.text_unformatted(label)
            imgui.set_next_item_width(-1)
            _, value = imgui.input_text(
                "##alternative" + attr, getattr(self, attr), flags
            )
            setattr(self, attr, value)
        imgui.text_wrapped(S.ALTERNATIVE_PASSWORD_HINT)
        if imgui.button(S.ALTERNATIVE_SAVE):
            try:
                self._client.configure(
                    self._base_url, self._user, self._password or None
                )
                self._password = ""
                self._message = ""
                self._selected = None
                self._submit("refresh", self._client.refresh)
            except Exception as exc:
                self._message = str(exc)

    def _render_registration(self) -> None:
        imgui.text_wrapped(f"{S.ALTERNATIVE_URL}: {self._client.settings.base_url}")
        imgui.text_wrapped(S.ALTERNATIVE_REGISTRATION_HINT)
        for label, attr, flags in (
            (S.ALTERNATIVE_USER, "_user", 0),
            (S.ALTERNATIVE_EMAIL, "_email", 0),
            (AppS.PASSWORD_LABEL, "_password", imgui.InputTextFlags_.password),
            (AppS.PASSWORD_REPEAT_LABEL, "_repeat", imgui.InputTextFlags_.password),
        ):
            imgui.text_unformatted(label)
            imgui.set_next_item_width(-1)
            _, value = imgui.input_text(
                "##registration" + attr, getattr(self, attr), flags
            )
            setattr(self, attr, value)
        if imgui.button(S.ALTERNATIVE_REGISTER):
            if self._password != self._repeat:
                self._message = AppS.PASSWORD_MISMATCH
            else:
                user, email, password = self._user, self._email, self._password
                self._submit(
                    "registration", lambda: self._client.register(user, email, password)
                )
        same_line_if_fits(button_width(S.ALTERNATIVE_RESUME))
        if imgui.button(S.ALTERNATIVE_RESUME):
            try:
                self._client.configure(
                    self._base_url, self._user, self._password or None
                )
                self._password = self._repeat = ""
                self._submit("registration", self._client.resume_registration)
            except Exception as exc:
                self._message = str(exc)
        imgui.text_unformatted(S.ALTERNATIVE_CODE)
        imgui.set_next_item_width(-1)
        _, self._code = imgui.input_text("##registration_code", self._code)
        if imgui.button(S.ALTERNATIVE_VERIFY):
            code = self._code
            self._submit("registration", lambda: self._client.verify_registration(code))
        same_line_if_fits(button_width(S.ALTERNATIVE_RESEND))
        if imgui.button(S.ALTERNATIVE_RESEND):
            self._submit("registration", self._client.resend_registration_code)

    def _render_details(self, entry: AlternativeEntry) -> None:
        if imgui.tree_node(S.CATALOG_DETAIL_TITLE):
            if imgui.button(AppS.BTN_COPY + "##alternative_metadata"):
                imgui.set_clipboard_text(
                    json.dumps(entry.as_dict(), ensure_ascii=False, indent=2)
                )
            imgui.begin_child("##alternative_details", imgui.ImVec2(0, 180))
            for label, value in (
                (S.CATALOG_DETAIL_NAME, entry.name),
                (
                    S.CATALOG_DETAIL_MANUFACTURER,
                    f"{entry.manufacturer_id} ({entry.manufacturer})",
                ),
                (S.CATALOG_DETAIL_ORDER, ", ".join(entry.order_numbers)),
                (S.ALTERNATIVE_FILE, entry.file_name),
                (S.ALTERNATIVE_SIZE, f"{entry.size:,} B"),
                (S.ALTERNATIVE_UPDATED, entry.updated),
                ("ID", entry.id),
                ("SHA-256", entry.sha256),
                ("URL", self._client.settings.base_url + entry.download_url),
            ):
                imgui.text_wrapped(f"{label}: {value}")
            for program in entry.programs:
                imgui.separator()
                imgui.text_wrapped(
                    f"{program['name']}\n{program['key']}\n{program['displayKey']}"
                )
                imgui.text_wrapped(
                    f"{program['manufacturerId']} ({program['manufacturer']})"
                )
                imgui.text_wrapped(
                    f"{S.CATALOG_DETAIL_APPLICATION}: {program['applicationNumber']}\n{S.CATALOG_DETAIL_VERSION}: {program_version_text(program['version'])} (0x{program['version']})\n{S.ALTERNATIVE_MASK}: {program['maskVersion']}"
                )
                imgui.text_wrapped(
                    f"{S.CATALOG_DETAIL_ORDER}: {', '.join(program['orderNumbers'])}"
                )
                for name in program["productNames"]:
                    imgui.text_wrapped(name)
            imgui.end_child()
            imgui.tree_pop()
        imgui.begin_disabled(self._future is not None)
        if imgui.button(S.ONLINE_DOWNLOAD):
            self._submit("download", lambda: self._client.download(entry.id))
        imgui.end_disabled()
