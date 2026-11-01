"""Build the net48 ETS export helpers into the proj package's ``core/_prebuilt/`` directory.

Run in CI (a job with the .NET SDK, e.g. the Windows desktop build) after ``uv sync`` and before
PyInstaller. The spec's ``collect_data_files("xknxeditor")`` then bundles ``core/_prebuilt/*.exe``
into the release, so the desktop app runs the ETS export with only the .NET Framework 4.8 runtime
present -- no dotnet SDK on the end user's machine. The built net48 exe is portable to Windows
regardless of the runner OS, so this works from any CI runner that has the dotnet SDK.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import xknxeditor.proj.core as _core
from xknxeditor.proj.core.key_extract import (
    build_ets5_export_exe,
    build_ets_export_exe,
)


def main() -> None:
    dest = Path(_core.__file__).parent / "_prebuilt"
    # Start clean so a stale exe is never reused and build_*_exe always does a fresh dotnet build.
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for build in (build_ets_export_exe, build_ets5_export_exe):
        exe = build()
        shutil.copy2(exe, dest / exe.name)
        config = exe.with_name(exe.name + ".config")
        if config.is_file():
            shutil.copy2(config, dest / config.name)
        print(f"bundled {exe.name} -> {dest}")


if __name__ == "__main__":
    main()
