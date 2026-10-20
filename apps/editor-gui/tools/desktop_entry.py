"""Frozen-app entry point (referenced by xknx-editor.spec).

PyInstaller analyses this module as the program's start script; it just launches the GUI.
"""

import multiprocessing
import os
import sys

# A worker process (e.g. one parsing applications) is this same executable: it runs its job and
# exits here, before the GUI is imported.
if __name__ == "__main__":
    multiprocessing.freeze_support()

# The browser UI (--web) started from a terminal on Windows: the windowed exe has no console of its
# own, so it writes to that terminal (the web address, why it does not start) and Ctrl+C stops it.
# Started otherwise (Start menu, Explorer) there is none to attach to and the log file is used.
if (
    sys.platform == "win32"
    and getattr(sys, "frozen", False)
    and any(a == "--web" or a.startswith("--web=") for a in sys.argv[1:])
):
    import ctypes

    if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        _console = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
        sys.stdout = _console
        sys.stderr = _console

# A frozen windowed app (PyInstaller console=False) has no console, so sys.stdout/sys.stderr are
# None. Any print() or library that writes to them (e.g. structlog's PrintLogger) then crashes at
# startup. Redirect both to this run's log file before anything else runs; it also aids diagnosing
# the build. The log service writes to the same file.
if sys.stdout is None or sys.stderr is None:
    from editor_gui.log_file import session_log

    _log = session_log()
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log
    # hello_imgui and its backends are C++: they write errors to the OS-level stdout/stderr file
    # descriptors, not Python's sys.stderr, so their messages never reached this log (which is why a
    # failing DirectX11 window setup looked completely silent). Point fd 1/2 at the log too, so native
    # errors are captured.
    try:
        _fd = _log.fileno()
        os.dup2(_fd, 1)
        os.dup2(_fd, 2)
    except (OSError, ValueError):
        pass
    # Catch hard native crashes (access violations in C++ backends like DirectX11) and dump the Python
    # stack at the fault point into the log — otherwise such crashes are completely silent.
    try:
        import faulthandler

        faulthandler.enable(file=_log)
    except Exception:
        pass

# The "compat" build ships this marker plus Mesa's opengl32.dll next to the exe, so a GPU-less VM
# still renders on the CPU. Use the llvmpipe gallium driver (LLVM JIT -> native code, multithreaded);
# it is far faster than softpipe and runs on every VM we have tested (native x64 virtualization and
# ARM64 under QEMU). softpipe (pure C, no JIT) stays reachable via GALLIUM_DRIVER=softpipe as a manual
# fallback. Harmless in the normal build (no marker -> not set, so the system/GPU OpenGL is used).
if getattr(sys, "frozen", False):
    _here = os.path.dirname(sys.executable)
    if os.path.exists(os.path.join(_here, "software_gl.marker")):
        os.environ.setdefault("GALLIUM_DRIVER", "llvmpipe")
        os.environ.setdefault("MESA_GL_VERSION_OVERRIDE", "3.3")
        # Tell the app it is running on a CPU rasterizer so it can throttle the frame rate (main.py).
        os.environ.setdefault("XKNX_SOFTWARE_GL", "1")

from editor_gui.main import main

if __name__ == "__main__":
    main()
