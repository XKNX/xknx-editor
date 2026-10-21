"""The main window: its title, changeable while the app runs, and bringing it to the front."""

from __future__ import annotations

import ctypes
import os
from typing import cast

from imgui_bundle import hello_imgui

_glfw: ctypes.CDLL | None = None
_GLFW_ICONIFIED = 0x00020002
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
        for name in ("glfwRestoreWindow", "glfwShowWindow", "glfwFocusWindow"):
            getattr(_glfw, name).argtypes = [ctypes.c_void_p]
            getattr(_glfw, name).restype = None
        _glfw.glfwGetWindowAttrib.argtypes = [ctypes.c_void_p, ctypes.c_int]
        _glfw.glfwGetWindowAttrib.restype = ctypes.c_int
        _glfw.glfwPostEmptyEvent.argtypes = []
        _glfw.glfwPostEmptyEvent.restype = None
    return _glfw


def _window() -> int:
    # Missing from the imgui_bundle stubs; imgui_bundle.glfw_utils uses it the same way.
    return cast(
        "int",
        hello_imgui.get_glfw_window_address(),  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    )


def bring_to_front() -> None:
    """Show the main window in front, restored when minimized; call from the render thread."""
    window = _window()
    library = _library() if window else None
    if library is None:
        return
    handle = ctypes.c_void_p(window)
    if library.glfwGetWindowAttrib(handle, _GLFW_ICONIFIED):
        library.glfwRestoreWindow(handle)
    library.glfwShowWindow(handle)
    library.glfwFocusWindow(handle)


def wake() -> None:
    """Let an idle render loop run its next frame now; callable from any thread."""
    library = _library() if _window() else None
    if library is not None:
        library.glfwPostEmptyEvent()


def set_window_title(title: str) -> None:
    """Set the main window's title; call from the render thread. No-op when unchanged."""
    global _title
    if title == _title:
        return
    window = _window()
    library = _library() if window else None
    if library is None:
        return
    library.glfwSetWindowTitle(ctypes.c_void_p(window), title.encode("utf-8"))
    _title = title
