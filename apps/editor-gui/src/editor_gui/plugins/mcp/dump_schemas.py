"""CLI and library tool to dump and synchronize MCP JSON schemas for IDE clients.

Usage:
    uv run python -m editor_gui.plugins.mcp.dump_schemas [output_dir]

If output_dir is omitted, schemas are automatically synchronized to the local Gemini/Antigravity
MCP directories in ~/.gemini/antigravity/mcp/xknx-editor and ~/.gemini/antigravity-ide/mcp/xknx-editor.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from editor_gui.plugins.mcp.context import McpContext
from editor_gui.plugins.mcp.server import INSTRUCTIONS, build_server


class _NullProxy:
    """Null proxy that provides safe attribute access during tool registration."""

    def __getattr__(self, name: str) -> _NullProxy:
        return _NullProxy()

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        return None


def _stub_run_on_ui(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return None


def export_schemas(output_dirs: list[Path] | None = None) -> list[Path]:
    """Export all registered MCP tool schemas as JSON files to the given directories."""
    if not output_dirs:
        home = Path.home()
        candidates = [
            home / ".gemini" / "antigravity" / "mcp" / "xknx-editor",
            home / ".gemini" / "antigravity-ide" / "mcp" / "xknx-editor",
        ]
        output_dirs = [p for p in candidates if p.parent.exists()]
        if not output_dirs:
            # Fallback to the primary antigravity directory
            output_dirs = [candidates[0]]

    # Build server with a stub context to register all tools
    dummy: Any = _NullProxy()
    ctx = McpContext(
        api=dummy,
        run_on_ui=_stub_run_on_ui,
        connection_plugin=dummy,
        monitor=dummy,
        keyring=dummy,
        log=dummy,
    )
    mcp = build_server(ctx)
    tools = asyncio.run(mcp.list_tools())

    tool_names = {t.name for t in tools}

    for target in output_dirs:
        target.mkdir(parents=True, exist_ok=True)

        # Write each tool schema
        for t in tools:
            schema_data = {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            out_file = target / f"{t.name}.json"
            out_file.write_text(json.dumps(schema_data, indent=2), encoding="utf-8")

        # Write instructions.md
        inst_file = target / "instructions.md"
        inst_file.write_text(INSTRUCTIONS, encoding="utf-8")

        # Clean up stale tool json files in target
        for existing in target.glob("*.json"):
            if existing.stem not in tool_names:
                existing.unlink()

    return output_dirs


def main() -> None:
    targets: list[Path] | None = None
    if len(sys.argv) > 1:
        targets = [Path(sys.argv[1])]

    exported_paths = export_schemas(targets)
    for p in exported_paths:
        count = len(list(p.glob("*.json")))
        print(f"Exported {count} tool schemas to {p}")


if __name__ == "__main__":
    main()
