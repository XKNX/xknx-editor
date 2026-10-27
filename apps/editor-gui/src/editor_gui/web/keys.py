"""Map the browser's ``KeyboardEvent.code`` (the physical key) to Dear ImGui keys.

Typed text does not go through this table: the browser sends it as characters, so every keyboard
layout (AltGr, dead keys, umlauts) types what it shows. The table only serves shortcuts and
navigation keys, which ImGui expects by position like a native backend."""

from __future__ import annotations

from imgui_bundle import imgui

_NAMED = {
    "Backspace": "backspace",
    "Tab": "tab",
    "Enter": "enter",
    "NumpadEnter": "keypad_enter",
    "Escape": "escape",
    "Space": "space",
    "PageUp": "page_up",
    "PageDown": "page_down",
    "End": "end",
    "Home": "home",
    "ArrowLeft": "left_arrow",
    "ArrowUp": "up_arrow",
    "ArrowRight": "right_arrow",
    "ArrowDown": "down_arrow",
    "Insert": "insert",
    "Delete": "delete",
    "ShiftLeft": "left_shift",
    "ShiftRight": "right_shift",
    "ControlLeft": "left_ctrl",
    "ControlRight": "right_ctrl",
    "AltLeft": "left_alt",
    "AltRight": "right_alt",
    "MetaLeft": "left_super",
    "MetaRight": "right_super",
    "ContextMenu": "menu",
    "CapsLock": "caps_lock",
    "NumLock": "num_lock",
    "ScrollLock": "scroll_lock",
    "Pause": "pause",
    "PrintScreen": "print_screen",
    "Quote": "apostrophe",
    "Comma": "comma",
    "Minus": "minus",
    "Period": "period",
    "Slash": "slash",
    "Semicolon": "semicolon",
    "Equal": "equal",
    "BracketLeft": "left_bracket",
    "Backslash": "backslash",
    "BracketRight": "right_bracket",
    "Backquote": "grave_accent",
    "IntlBackslash": "oem102",
    "NumpadDecimal": "keypad_decimal",
    "NumpadDivide": "keypad_divide",
    "NumpadMultiply": "keypad_multiply",
    "NumpadSubtract": "keypad_subtract",
    "NumpadAdd": "keypad_add",
    "NumpadEqual": "keypad_equal",
    "BrowserBack": "app_back",
    "BrowserForward": "app_forward",
}

_KEYS: dict[str, imgui.Key] = {}


def _build() -> dict[str, imgui.Key]:
    names = dict(_NAMED)
    names.update({f"Key{c}": c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"})
    names.update({f"Digit{d}": f"_{d}" for d in range(10)})
    names.update({f"Numpad{d}": f"keypad{d}" for d in range(10)})
    names.update({f"F{n}": f"f{n}" for n in range(1, 25)})
    keys: dict[str, imgui.Key] = {}
    for code, name in names.items():
        key = getattr(imgui.Key, name, None)
        if key is not None:
            keys[code] = key
    return keys


def imgui_key(code: str) -> imgui.Key | None:
    """The ImGui key for a ``KeyboardEvent.code``, None when ImGui has none."""
    if not _KEYS:
        _KEYS.update(_build())
    return _KEYS.get(code)
