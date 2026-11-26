"""Entry point for the editor child process of measure_memory.py."""

# MCP uses these functions by registering them with decorators.
# pyright: reportUnusedFunction=false

from __future__ import annotations

import os
import runpy
import sys
from functools import partial
from pathlib import Path

import platformdirs
import psutil


def isolate(root: Path) -> None:
    # As in the screenshot launcher, override platformdirs as well as environment variables
    # on platforms that resolve user folders through OS APIs.
    def directory(kind: str, *args: object, **kwargs: object) -> str:
        return str(root / kind / "xknx-editor")

    for kind in ("data", "config", "cache", "log", "state"):
        setattr(
            platformdirs,
            f"user_{kind}_dir",
            partial(directory, kind),
        )


def simulate_pi(cores: int, available: int) -> None:
    virtual_memory = psutil.virtual_memory
    psutil.cpu_count = lambda logical=True: cores
    psutil.virtual_memory = lambda: virtual_memory()._replace(available=available)


def register_measurement_tools() -> None:
    """Install measurement controls in this editor only, after directory isolation."""
    from editor_gui.plugins.mcp.context import McpContext, make_tool
    from editor_gui.plugins.mcp.tools import project
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError

    register_project = project.register

    def register(mcp: FastMCP, ctx: McpContext) -> None:
        register_project(mcp, ctx)
        service = ctx.api.project
        tool = make_tool(mcp, ctx)

        @tool("read")
        def measurement_devices() -> dict[str, object]:
            def read() -> dict[str, object]:
                # A project can open before its background product data has been stored.
                with service._product_lock:  # pyright: ignore[reportPrivateUsage]
                    if (
                        service._product_running  # pyright: ignore[reportPrivateUsage]
                        or service._product_again  # pyright: ignore[reportPrivateUsage]
                        or service._product_results  # pyright: ignore[reportPrivateUsage]
                    ):
                        return {"loading": True}
                return {
                    "loading": False,
                    "missing": [device.node_id for device in service.unloaded_devices],
                    "devices": {
                        str(device.node_id): device.app is not None
                        for device in service.devices
                    },
                }

            return ctx.run_locked(read)

        @tool("write")
        def measurement_open_device(node_id: int) -> None:
            def select() -> None:
                device = service.find_device_by_node_id(node_id)
                if device is None:
                    raise ToolError(f"No device with node_id {node_id}")
                # Use the classic Editor's selection and focus, as the Health view does.
                service.selected_device = device
                service.focus_editor()
                device.get_ui()

            ctx.run_locked(select)

        @tool("write")
        def measurement_set_password(password: str) -> None:
            ctx.run_locked(lambda: service.set_project_password(password))

    project.register = register


def main() -> None:
    root = Path(sys.argv[1])
    (root / "editor.pid").write_text(str(os.getpid()), encoding="ascii")
    isolate(root)
    cores, available = map(int, sys.argv[2:4])
    if cores:
        simulate_pi(cores, available)
    register_measurement_tools()
    sys.argv = ["editor_gui.main", *sys.argv[4:]]
    runpy.run_module("editor_gui.main", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
