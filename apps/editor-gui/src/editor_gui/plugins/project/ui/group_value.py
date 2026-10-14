"""Read and write the value of a group address: the "Read Value" / "Write Value…" menu items and
the Write Value dialog, shared by the Group Addresses panel and the device's Group Objects."""

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px, px_vec2
from editor_gui.widgets.dpt_value import DptValueInput, GroupValueBus, dpt_label


class GroupValueActions:
    def __init__(self, bus: GroupValueBus, value_id: str) -> None:
        self._bus = bus
        self._open = False
        self._address = ""
        self._name = ""
        self._dpt: str | None = None
        self._value = DptValueInput(value_id)

    def render_menu_items(self, address: str, name: str, dpt: str | None) -> None:
        """The menu items of a context menu; ``dpt`` is the type token the value is written as."""
        connected = self._bus.connected()
        if imgui.menu_item(S.GA_READ_VALUE, "", False, connected)[0]:
            self._bus.read(address)
        if not connected:
            imgui.set_item_tooltip(S.GA_NOT_CONNECTED)
        if imgui.menu_item(S.GA_WRITE_VALUE, "", False, connected)[0]:
            self._address = address
            self._name = name
            self._dpt = dpt or None
            self._value.reset()
            self._open = True
        if not connected:
            imgui.set_item_tooltip(S.GA_NOT_CONNECTED)

    def render(self) -> None:
        """The Write Value dialog; call once per frame in the window that shows the menu."""
        if self._open:
            imgui.open_popup(S.GA_WRITE_VALUE_TITLE)
            self._open = False
        bus = self._bus
        # At least the usual width; wider when a value field needs the room.
        imgui.set_next_window_size_constraints(
            px_vec2(480.0, 0.0), imgui.ImVec2(1e9, 1e9)
        )
        if not begin_dialog(S.GA_WRITE_VALUE_TITLE):
            return
        formats = bus.formats()
        fields = self._value.field_labels(self._dpt, formats) if self._dpt else []
        labels = [S.GA_WRITE_ADDRESS, S.GA_WRITE_DPT, S.GA_WRITE_VALUE_LABEL, *fields]
        label_w = max(imgui.calc_text_size(label).x for label in labels) + px(16.0)
        imgui.text_disabled(S.GA_WRITE_ADDRESS)
        imgui.same_line(label_w)
        imgui.text(f"{self._address}  {self._name}".strip())
        imgui.text_disabled(S.GA_WRITE_DPT)
        imgui.same_line(label_w)
        imgui.text(dpt_label(self._dpt, bus.names()) or "-")
        imgui.spacing()
        submitted = False
        if self._dpt and fields:
            # A compound value: its fields share the label column of the dialog.
            submitted = self._value.render(self._dpt, formats, column=label_w)
        else:
            imgui.align_text_to_frame_padding()
            imgui.text_disabled(S.GA_WRITE_VALUE_LABEL)
            imgui.same_line(label_w)
            if self._dpt:
                imgui.begin_group()
                submitted = self._value.render(self._dpt, formats)
                imgui.end_group()
            else:
                imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + px(300.0))
                imgui.text_wrapped(S.GA_WRITE_NO_DPT)
                imgui.pop_text_wrap_pos()
        if self._value.error:
            imgui.text_colored(imgui.ImVec4(0.9, 0.3, 0.3, 1.0), self._value.error)
        connected = bus.connected()
        can_write = connected and self._value.can_write(self._dpt, formats)
        if not connected:
            imgui.text_disabled(S.GA_NOT_CONNECTED)
        clicked = dialog_buttons(
            S.GA_WRITE_BUTTON, S.BTN_CANCEL, enabled=[can_write, True]
        )
        if (clicked == 0 or (submitted and can_write)) and can_write:
            payload = self._value.payload()
            if payload is not None:
                bus.write(self._address, payload)
                imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()
