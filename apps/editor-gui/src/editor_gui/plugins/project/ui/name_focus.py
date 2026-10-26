"""F2 on the element selected in a tree: the Device tab, which shows its details, comes to the
front and its name field takes the keyboard with all of its text selected, so typing replaces
the name."""

from imgui_bundle import imgui

# How many frames the name field has to show up after F2 (the tab comes to the front first),
# and to take the keyboard after that.
_FRAMES = 5


class _Pending:
    tab = False
    # The frame F2 was pressed in, None when no name field is to take the keyboard.
    frame: int | None = None
    # The frame the name field was given the keyboard in, None when nothing is to be selected.
    focused: int | None = None


def request() -> None:
    """Bring the Device tab to the front and give its name field the keyboard."""
    _Pending.tab = True
    _Pending.frame = imgui.get_frame_count()


def take_tab_request() -> bool:
    """Whether the Device tab is to come to the front, once."""
    asked, _Pending.tab = _Pending.tab, False
    return asked


def focus_name_field() -> None:
    """Called right before a name field of the details: it takes the keyboard when F2 asked for
    it a moment ago."""
    frame = _Pending.frame
    if frame is None:
        return
    _Pending.frame = None
    if imgui.get_frame_count() - frame <= _FRAMES:
        imgui.set_keyboard_focus_here()
        _Pending.focused = imgui.get_frame_count()


def select_name() -> None:
    """Called right after a name field: when it took the keyboard for F2, all of its text is
    selected."""
    focused = _Pending.focused
    if focused is None or not imgui.is_item_activated():
        return
    _Pending.focused = None
    if imgui.get_frame_count() - focused > _FRAMES:
        return
    state = imgui.internal.get_input_text_state(imgui.get_item_id())
    # The bindings type the state as non-optional, but it is None without an active edit.
    if state is not None:  # pyright: ignore[reportUnnecessaryComparison]
        state.reload_user_buf_and_select_all()
