"""Keyring / KNX Secure panel: import a ``.knxkeys`` keyring (with password), browse its
decrypted contents — backbone, tunnel interfaces, data-secure group-address keys, device keys —
and export (convert) it under a new password."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from imgui_bundle import imgui
from imgui_bundle import portable_file_dialogs as pfd
from xknx.telegram.address import GroupAddress

from editor_gui.device import address_order
from editor_gui.plugins.keyring.strings import S
from editor_gui.widgets.dpi import px_vec2

if TYPE_CHECKING:
    from editor_gui.plugins.keyring.service import KeyringService
    from xknxeditor.datasecure import DecryptedKeyring


class _GroupAddressLike(Protocol):
    """The subset of the project's group-address DTO this panel reads."""

    address: str
    data_secure: bool


def _key_preview(value: bytes | None) -> str:
    if not value:
        return "-"
    raw = value.hex()
    return f"{raw[:8]}…" if len(raw) > 8 else raw


class KeyringPanel:
    def __init__(
        self,
        service: "KeyringService",
        get_group_addresses: "Callable[[], Sequence[_GroupAddressLike]]",
    ) -> None:
        self._service = service
        self._get_group_addresses = get_group_addresses
        # import: file dialog -> password modal
        self._dialog: pfd.open_file | None = None
        self._pending_path: str | None = None
        self._password = ""
        self._error: str | None = None
        self._prompt_open = False
        # export: save dialog -> new-password modal
        self._save_dialog: pfd.save_file | None = None
        self._pending_export_path: str | None = None
        self._export_password = ""
        self._export_error: str | None = None
        self._export_prompt_open = False

    def has_keyring(self) -> bool:
        return self._service.keyring is not None

    # --- import (menu-triggered: file dialog + small password modal, no window) --------

    def begin_import(self) -> None:
        """Open the file dialog to pick a ``.knxkeys`` keyring (triggered from the menu)."""
        self._dialog = pfd.open_file(
            S.KEYRING_IMPORT, "", [S.KEYRING_FILTER, "*.knxkeys", S.ALL_FILES, "*"]
        )

    def _poll_dialog(self) -> None:
        if self._dialog is not None and self._dialog.ready():
            result = self._dialog.result()
            self._dialog = None
            if result:
                self._pending_path = result[0]
                self._password = ""
                self._error = None

    def render_import_prompt(self) -> None:
        """Poll the file dialog; once a file is picked, show a small password modal to load it.

        Call every frame (from the app's overlay pass)."""
        self._poll_dialog()
        if self._pending_path is None:
            return
        if not self._prompt_open:
            imgui.open_popup(S.KEYRING_IMPORT)
            self._prompt_open = True
        imgui.set_next_window_size(px_vec2(360.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.KEYRING_IMPORT, None)[0]:
            return
        imgui.text_disabled(Path(self._pending_path).name)
        imgui.set_next_item_width(-1)
        submitted, self._password = imgui.input_text(
            "##keyring_pw",
            self._password,
            imgui.InputTextFlags_.password | imgui.InputTextFlags_.enter_returns_true,
        )
        if self._error:
            imgui.text_colored(imgui.ImVec4(0.9, 0.4, 0.4, 1.0), self._error)
        if (imgui.button(S.KEYRING_LOAD) or submitted) and self._pending_path:
            try:
                self._service.load(Path(self._pending_path), self._password)
                self._pending_path = None
                self._password = ""
                self._error = None
                self._prompt_open = False
                imgui.close_current_popup()
            except (
                Exception
            ) as e:  # KeyringSignatureError on a bad password / invalid file
                self._error = f"{type(e).__name__}: {e}"
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL):
            self._pending_path = None
            self._password = ""
            self._error = None
            self._prompt_open = False
            imgui.close_current_popup()
        imgui.end_popup()

    # --- export (button in the window: save dialog + new-password modal) ---------------

    def begin_export(self) -> None:
        """Open the save dialog to pick the target ``.knxkeys`` path."""
        default = "keyring.knxkeys"
        self._save_dialog = pfd.save_file(
            S.KEYRING_EXPORT, default, [S.KEYRING_FILTER, "*.knxkeys", S.ALL_FILES, "*"]
        )

    def _poll_save_dialog(self) -> None:
        if self._save_dialog is not None and self._save_dialog.ready():
            result = self._save_dialog.result()
            self._save_dialog = None
            if result:
                path = result if result.endswith(".knxkeys") else f"{result}.knxkeys"
                self._pending_export_path = path
                self._export_password = ""
                self._export_error = None

    def render_export_prompt(self) -> None:
        """Poll the save dialog; once a path is picked, prompt for the target password.

        Call every frame (from the app's overlay pass)."""
        self._poll_save_dialog()
        if self._pending_export_path is None:
            return
        if not self._export_prompt_open:
            imgui.open_popup(S.KEYRING_EXPORT)
            self._export_prompt_open = True
        imgui.set_next_window_size(px_vec2(360.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.KEYRING_EXPORT, None)[0]:
            return
        imgui.text_disabled(Path(self._pending_export_path).name)
        imgui.text_disabled(S.KEYRING_EXPORT_HINT)
        imgui.set_next_item_width(-1)
        submitted, self._export_password = imgui.input_text(
            "##keyring_export_pw",
            self._export_password,
            imgui.InputTextFlags_.password | imgui.InputTextFlags_.enter_returns_true,
        )
        if self._export_error:
            imgui.text_colored(imgui.ImVec4(0.9, 0.4, 0.4, 1.0), self._export_error)
        can_export = bool(self._export_password)
        if (imgui.button(S.KEYRING_EXPORT_SAVE) or submitted) and can_export:
            try:
                self._service.export(
                    Path(self._pending_export_path), self._export_password
                )
                self._pending_export_path = None
                self._export_password = ""
                self._export_error = None
                self._export_prompt_open = False
                imgui.close_current_popup()
            except Exception as e:
                self._export_error = f"{type(e).__name__}: {e}"
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL):
            self._pending_export_path = None
            self._export_password = ""
            self._export_error = None
            self._export_prompt_open = False
            imgui.close_current_popup()
        imgui.end_popup()

    # --- contents (shown in the window after a keyring is loaded) ----------

    def render_contents(self) -> None:
        model = self._service.keyring
        decrypted = self._service.decrypted
        if model is None or decrypted is None:
            return
        if imgui.button(S.KEYRING_CLOSE):
            self._service.clear()
            return
        imgui.same_line()
        if imgui.button(S.KEYRING_EXPORT):
            self.begin_export()
        imgui.same_line()
        imgui.text_disabled(
            S.KEYRING_HEADER.format(
                project=self._service.project_name or "?",
                by=self._service.created_by or "?",
            )
        )

        if model.backbone is not None and imgui.collapsing_header(
            S.KEYRING_BACKBONE, imgui.TreeNodeFlags_.default_open
        ):
            imgui.text_disabled(
                f"{model.backbone.multicast_address}   "
                f"key {_key_preview(decrypted.backbone_key)}"
            )

        self._render_interfaces(decrypted)
        self._render_group_addresses(decrypted)
        self._render_devices(decrypted)

    def _render_interfaces(self, decrypted: "DecryptedKeyring") -> None:
        interfaces = decrypted.interfaces
        if not interfaces or not imgui.collapsing_header(
            S.KEYRING_INTERFACES.format(count=len(interfaces))
        ):
            return
        flags = imgui.TableFlags_.borders_inner | imgui.TableFlags_.sizing_stretch_prop
        if imgui.begin_table("##kr_ifaces", 4, flags):
            for header in ("Type", "Address", "Host", "User"):
                imgui.table_setup_column(header)
            imgui.table_headers_row()
            for iface in interfaces:
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                imgui.text(getattr(iface.type, "value", "") or "")
                imgui.table_set_column_index(1)
                imgui.text(str(iface.individual_address or ""))
                imgui.table_set_column_index(2)
                imgui.text_disabled(str(iface.host or ""))
                imgui.table_set_column_index(3)
                imgui.text_disabled(str(iface.user_id or ""))
            imgui.end_table()

    def _render_group_addresses(self, decrypted: "DecryptedKeyring") -> None:
        group_keys = decrypted.group_keys
        if not group_keys or not imgui.collapsing_header(
            S.KEYRING_GROUP_ADDRESSES.format(count=len(group_keys))
        ):
            return
        secure = {g.address for g in self._get_group_addresses() if g.data_secure}
        flags = imgui.TableFlags_.borders_inner | imgui.TableFlags_.sizing_stretch_prop
        if imgui.begin_table("##kr_gas", 3, flags):
            for header in ("Address", "Key", "In project"):
                imgui.table_setup_column(header)
            imgui.table_headers_row()
            for raw_address, key in sorted(group_keys.items()):
                addr = str(GroupAddress(raw_address))
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                imgui.text(addr)
                imgui.table_set_column_index(1)
                imgui.text_disabled(_key_preview(key))
                imgui.table_set_column_index(2)
                if addr in secure:
                    imgui.text("✓")
            imgui.end_table()

    def _render_devices(self, decrypted: "DecryptedKeyring") -> None:
        devices = sorted(
            decrypted.devices, key=lambda d: address_order(d.individual_address)
        )
        if not devices or not imgui.collapsing_header(
            S.KEYRING_DEVICES.format(count=len(devices))
        ):
            return
        flags = imgui.TableFlags_.borders_inner | imgui.TableFlags_.sizing_stretch_prop
        if imgui.begin_table("##kr_devices", 2, flags):
            for header in ("Address", "Tool key"):
                imgui.table_setup_column(header)
            imgui.table_headers_row()
            for dev in devices:
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                imgui.text(str(dev.individual_address or ""))
                imgui.table_set_column_index(1)
                imgui.text_disabled(_key_preview(dev.tool_key))
            imgui.end_table()
