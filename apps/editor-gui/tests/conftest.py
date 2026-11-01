"""The app's tests outside the package start their imgui contexts as the package's tests do."""

from editor_gui.conftest import _no_ini_file  # pyright: ignore[reportPrivateUsage]

__all__ = ["_no_ini_file"]
