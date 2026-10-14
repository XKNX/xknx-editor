"""Title of the main window, changeable while the app runs."""

from __future__ import annotations

import ctypes
import os
from typing import cast

from imgui_bundle import hello_imgui

_glfw: ctypes.CDLL | None = None
_title: str | None = None


def _library() -> ctypes.CDLL | None:
    """The GLFW library imgui_bundle created the window with (its path is set on import)."""
    global _glfw
    if _glfw is None:
        path = os.environ.get("PYGLFW_LIBRARY")
        if not path:
            return None
        try:
            _glfw = ctypes.CDLL(path)
        except OSError:
            return None
        _glfw.glfwSetWindowTitle.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        _glfw.glfwSetWindowTitle.restype = None
    return _glfw


def set_window_title(title: str) -> None:
    """Set the main window's title; call from the render thread. No-op when unchanged."""
    global _title
    if title == _title:
        return
    # Missing from the imgui_bundle stubs; imgui_bundle.glfw_utils uses it the same way.
    window = cast(
        "int",
        hello_imgui.get_glfw_window_address(),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    )
    library = _library() if window else None
    if library is None:
        return
    library.glfwSetWindowTitle(ctypes.c_void_p(window), title.encode("utf-8"))
    _title = title
