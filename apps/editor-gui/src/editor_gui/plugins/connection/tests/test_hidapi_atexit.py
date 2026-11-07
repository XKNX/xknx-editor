"""hidapi's exit-time teardown is cancelled on shutdown, so quitting the GUI does not crash in
``IOHIDManagerClose`` after the run loop is gone (macOS)."""

from __future__ import annotations

import asyncio
import sys
import types
import weakref

from editor_gui.plugins.connection import usb as usb_mod
from editor_gui.plugins.connection.usb import (
    cancel_hidapi_atexit,
    search_usb_interfaces,
)


def test_no_op_without_hidapi(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delitem(sys.modules, "hid", raising=False)
    cancel_hidapi_atexit()  # must not raise when hidapi was never imported


def test_detaches_the_hid_module_finalizer(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_hid = types.ModuleType("hid")
    monkeypatch.setitem(sys.modules, "hid", fake_hid)
    ran: list[int] = []
    finalizer = weakref.finalize(fake_hid, ran.append, 1)

    # An unrelated finalizer on another object must be left alone.
    class _Other:
        pass

    other = _Other()
    other_ran: list[int] = []
    other_finalizer = weakref.finalize(other, other_ran.append, 1)

    cancel_hidapi_atexit()

    assert not finalizer.alive  # the hid teardown will not run at exit
    assert ran == []  # detaching does not run it
    assert other_finalizer.alive  # other finalizers untouched
    other_finalizer.detach()


def test_left_alone_off_macos(monkeypatch) -> None:
    # The crash is macOS-only; elsewhere hidapi keeps its normal teardown.
    monkeypatch.setattr(sys, "platform", "linux")
    fake_hid = types.ModuleType("hid")
    monkeypatch.setitem(sys.modules, "hid", fake_hid)
    finalizer = weakref.finalize(fake_hid, lambda: None)

    cancel_hidapi_atexit()

    assert finalizer.alive  # untouched off macOS
    finalizer.detach()


def test_scan_cancels_the_finalizer_without_shutdown(monkeypatch) -> None:
    # The scan imports hidapi in a worker thread; cancelling there closes the race where the app
    # quits before shutdown sees the (not-yet-registered) finalizer.
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_hid = types.ModuleType("hid")
    monkeypatch.setitem(sys.modules, "hid", fake_hid)
    finalizer = weakref.finalize(fake_hid, lambda: None)

    def fake_find():  # stands in for find_usb_interfaces importing hidapi
        return []

    monkeypatch.setattr(usb_mod, "find_usb_interfaces", fake_find)
    assert asyncio.run(search_usb_interfaces()) == []
    assert not finalizer.alive  # cancelled by the scan itself, no shutdown needed
