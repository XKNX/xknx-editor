"""Entry of a new password: two masked fields that must match."""

from imgui_bundle import imgui

# The module, not status_color: the theme module imports the widgets package itself.
from editor_gui import theme
from editor_gui.widgets.dialog import focus_when_appearing


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
        focus_when_appearing()
        _, self.password = imgui.input_text("##password", self.password, flags)
        imgui.text_disabled(repeat_label)
        imgui.set_next_item_width(-1)
        submitted, self.repeat = imgui.input_text(
            "##password_repeat",
            self.repeat,
            flags | imgui.InputTextFlags_.enter_returns_true,
        )
        if self.repeat and self.password != self.repeat:
            imgui.text_colored(theme.status_color("error"), mismatch)
        return submitted
