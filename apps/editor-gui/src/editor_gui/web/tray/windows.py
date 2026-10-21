"""The tray icon in the Windows notification area (``Shell_NotifyIconW``), with its own hidden
window and message loop on a thread. A left click opens the browser, a right click the menu."""

from __future__ import annotations

import ctypes
import itertools
import sys
import threading
from ctypes import wintypes

from editor_gui.web.tray import OPEN, STOP, TrayMenu

assert sys.platform == "win32"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_LRESULT = wintypes.LPARAM
_WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

_WM_NULL = 0x0000
_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_CONTEXTMENU = 0x007B
_WM_LBUTTONUP = 0x0202
_WM_RBUTTONUP = 0x0205
_WM_TRAY = 0x8000 + 1  # WM_APP + 1: the icon's mouse messages
_NIM_ADD, _NIM_DELETE = 0, 2
_NIF_MESSAGE, _NIF_ICON, _NIF_TIP = 0x1, 0x2, 0x4
_MF_STRING, _MF_GRAYED, _MF_SEPARATOR = 0x0, 0x1, 0x800
_TPM_RIGHTBUTTON, _TPM_NONOTIFY, _TPM_RETURNCMD = 0x2, 0x80, 0x100
_SM_CXSMICON, _SM_CYSMICON = 49, 50
_IMAGE_ICON, _LR_COPYDELETEORG = 1, 0x8

_class_ids = itertools.count(1)


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    )


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    )


def _declare() -> None:
    """Argument and result types, so handles keep their 64 bits."""
    u, s, k = _user32, _shell32, _kernel32
    k.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    k.GetModuleHandleW.restype = wintypes.HMODULE
    u.RegisterClassExW.argtypes = (ctypes.POINTER(_WNDCLASSEXW),)
    u.RegisterClassExW.restype = wintypes.ATOM
    u.UnregisterClassW.argtypes = (wintypes.LPCWSTR, wintypes.HINSTANCE)
    u.CreateWindowExW.argtypes = (
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    )
    u.CreateWindowExW.restype = wintypes.HWND
    u.DefWindowProcW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    u.DefWindowProcW.restype = _LRESULT
    u.DestroyWindow.argtypes = (wintypes.HWND,)
    u.PostMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    u.GetMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    )
    u.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    u.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    u.DispatchMessageW.restype = _LRESULT
    u.PostQuitMessage.argtypes = (ctypes.c_int,)
    u.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
    u.RegisterWindowMessageW.restype = wintypes.UINT
    u.CreatePopupMenu.restype = wintypes.HMENU
    u.AppendMenuW.argtypes = (
        wintypes.HMENU,
        wintypes.UINT,
        ctypes.c_size_t,
        wintypes.LPCWSTR,
    )
    u.SetMenuDefaultItem.argtypes = (wintypes.HMENU, wintypes.UINT, wintypes.UINT)
    u.TrackPopupMenu.argtypes = (
        wintypes.HMENU,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.LPVOID,
    )
    u.TrackPopupMenu.restype = wintypes.BOOL
    u.DestroyMenu.argtypes = (wintypes.HMENU,)
    u.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    u.SetForegroundWindow.argtypes = (wintypes.HWND,)
    u.GetSystemMetrics.argtypes = (ctypes.c_int,)
    u.CreateIconFromResourceEx.argtypes = (
        ctypes.c_char_p,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )
    u.CreateIconFromResourceEx.restype = wintypes.HICON
    u.CopyImage.argtypes = (
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )
    u.CopyImage.restype = wintypes.HANDLE
    u.DestroyIcon.argtypes = (wintypes.HICON,)
    s.Shell_NotifyIconW.argtypes = (
        wintypes.DWORD,
        ctypes.POINTER(_NOTIFYICONDATAW),
    )
    s.Shell_NotifyIconW.restype = wintypes.BOOL


_declare()


class WindowsTray:
    def __init__(self, menu: TrayMenu, png: bytes) -> None:
        self._menu = menu
        self._png = png
        self._hwnd: int | None = None
        self._icon: int | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._taskbar_created = -1
        self._class = f"XknxEditorTray{next(_class_ids)}"
        # Kept alive for as long as the window exists.
        self._proc = _WNDPROC(self._window_proc)
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        if not self._ready.wait(5.0) or self._error is not None:
            raise OSError(self._error or "the tray icon did not start in time")

    def pump(self) -> None:
        pass

    def wait(self, event: threading.Event, timeout: float) -> bool:
        return event.wait(timeout)

    def close(self) -> None:
        hwnd = self._hwnd
        if hwnd is not None:
            _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        if self._thread is not threading.current_thread():
            self._thread.join(2.0)

    def _run(self) -> None:
        instance = _kernel32.GetModuleHandleW(None)
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = instance
        wc.lpszClassName = self._class
        if not _user32.RegisterClassExW(ctypes.byref(wc)):
            self._fail("RegisterClassExW")
            return
        # A hidden top-level window: the popup menu closes properly only for a foreground window,
        # and only top-level windows hear that the taskbar was created again (Explorer restarted).
        hwnd = _user32.CreateWindowExW(
            0, self._class, self._class, 0, 0, 0, 0, 0, None, None, instance, None
        )
        if not hwnd:
            self._fail("CreateWindowExW")
            _user32.UnregisterClassW(self._class, instance)
            return
        self._hwnd = hwnd
        self._taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")
        self._icon = self._load_icon()
        if not self._notify(_NIM_ADD):
            self._fail("Shell_NotifyIconW")
            _user32.DestroyWindow(hwnd)
        else:
            self._ready.set()
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        if self._icon:
            _user32.DestroyIcon(self._icon)
        _user32.UnregisterClassW(self._class, instance)
        self._hwnd = None

    def _fail(self, call: str) -> None:
        self._error = f"{call} failed (error {ctypes.get_last_error()})"
        self._ready.set()

    def _load_icon(self) -> int | None:
        """The small icon from the PNG (Windows reads PNG icon resources), at the system's size."""
        cx = _user32.GetSystemMetrics(_SM_CXSMICON) or 16
        cy = _user32.GetSystemMetrics(_SM_CYSMICON) or 16
        icon = _user32.CreateIconFromResourceEx(
            self._png, len(self._png), True, 0x00030000, cx, cy, 0
        )
        if not icon:
            return None
        return _user32.CopyImage(icon, _IMAGE_ICON, cx, cy, _LR_COPYDELETEORG) or icon

    def _notify(self, message: int) -> bool:
        data = _NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = _NIF_MESSAGE | _NIF_TIP | (_NIF_ICON if self._icon else 0)
        data.uCallbackMessage = _WM_TRAY
        data.hIcon = self._icon
        data.szTip = self._menu.title[:127]
        return bool(_shell32.Shell_NotifyIconW(message, ctypes.byref(data)))

    def _window_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == _WM_TRAY:
            event = lparam & 0xFFFF
            if event == _WM_LBUTTONUP:
                self._menu.run(OPEN)
            elif event in (_WM_RBUTTONUP, _WM_CONTEXTMENU):
                self._menu.run(self._show_menu(hwnd))
            return 0
        if msg == self._taskbar_created:
            self._notify(_NIM_ADD)
            return 0
        if msg == _WM_CLOSE:
            self._notify(_NIM_DELETE)
            _user32.DestroyWindow(hwnd)
            return 0
        if msg == _WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self, hwnd: int) -> int:
        """The menu at the mouse; the chosen entry's id (0 when none)."""
        menu = _user32.CreatePopupMenu()
        _user32.AppendMenuW(menu, _MF_STRING | _MF_GRAYED, 0, self._menu.title)
        _user32.AppendMenuW(menu, _MF_SEPARATOR, 0, None)
        _user32.AppendMenuW(menu, _MF_STRING, OPEN, self._menu.open_label)
        _user32.AppendMenuW(menu, _MF_STRING, STOP, self._menu.stop_label)
        _user32.SetMenuDefaultItem(menu, OPEN, False)
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        _user32.SetForegroundWindow(hwnd)
        chosen = _user32.TrackPopupMenu(
            menu,
            _TPM_RIGHTBUTTON | _TPM_NONOTIFY | _TPM_RETURNCMD,
            point.x,
            point.y,
            0,
            hwnd,
            None,
        )
        _user32.PostMessageW(hwnd, _WM_NULL, 0, 0)
        _user32.DestroyMenu(menu)
        return int(chosen)
