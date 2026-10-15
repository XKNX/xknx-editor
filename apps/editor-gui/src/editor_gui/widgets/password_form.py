"""Entry of a new password: two masked fields that must match."""

from imgui_bundle import imgui

_ERROR_COLOR = imgui.ImVec4(1.0, 0.42, 0.42, 1.0)


class NewPasswordForm:
    def __init__(self) -> None:
        self.password = ""
        self.repeat = ""

    def reset(self) -> None:
        self.password = self.repeat = ""

    @property
    def valid(self) -> bool:
        return bool(self.password) and self.password == self.repeat

    def render(self, label: str, repeat_label: str, mismatch: str) -> bool:
        """Draw the two fields; ``True`` when Enter was pressed in the second one."""
        flags = imgui.InputTextFlags_.password
        imgui.text_disabled(label)
        imgui.set_next_item_width(-1)
        _, self.password = imgui.input_text("##password", self.password, flags)
        imgui.text_disabled(repeat_label)
        imgui.set_next_item_width(-1)
        submitted, self.repeat = imgui.input_text(
            "##password_repeat",
            self.repeat,
            flags | imgui.InputTextFlags_.enter_returns_true,
        )
        if self.repeat and self.password != self.repeat:
            imgui.text_colored(_ERROR_COLOR, mismatch)
        return submitted
