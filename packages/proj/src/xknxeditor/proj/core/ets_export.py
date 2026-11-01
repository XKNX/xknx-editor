"""Re-export a ``.knxproj`` through a locally installed, licensed ETS (6 or 5).

The installed tool imports the project and writes it again with its own exporter, so the licence
certificate (which lifts the import device cap) is produced from whatever licence the installation
already resolved locally. For ETS 6 that can be a cloud account or a dongle; for ETS 5 it is a
physical dongle. No login, token or network is used.

Windows only: the helper needs the .NET Framework. Off Windows, :func:`ets_local_available` /
:func:`ets5_local_available` are ``False`` and the caller keeps the existing export paths.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xknxeditor.proj.core._dll_signer import ETS_ROOTS
from xknxeditor.proj.core.key_extract import (
    build_ets5_export_exe,
    build_ets_export_exe,
    ets5_export_backend_available,
    ets_export_backend_available,
)

#: Files an ETS 6 installation must carry to drive an import/export run.
_REQUIRED_FILES = (
    "ETS6N.exe",
    "ETS6N.exe.config",
    "Autofac.dll",
    "Knx.Ets.Sdk.dll",
    "Knx.Ets.ObjectModel.dll",
    "Knx.Ets.Xml.ObjectModel.dll",
    "Knx.Ets.ViewModel.dll",
    "Knx.Ets.HawkNET.dll",
    "Knx.Ets.ObjectModelWindowsBase.dll",
)

#: Files an ETS 5 installation must carry (its main host is ``ets5n.exe``, there is no Common.dll).
_ETS5_REQUIRED_FILES = (
    "ets5n.exe",
    "ETS5N.exe.config",
    "Autofac.dll",
    "Knx.Ets.Sdk.dll",
    "Knx.Ets.ObjectModel.dll",
    "Knx.Ets.Xml.ObjectModel.dll",
    "Knx.Ets.ViewModel.dll",
    "Knx.Ets.HawkNET.dll",
    "Knx.Ets.XmlSigning.dll",
)

#: The versions each helper is known to match; others need ``accept_untested``.
_TESTED_PREFIX = "6.4."
_ETS5_TESTED_PREFIX = "5.7.1428"

#: A run can take minutes (the licence manager alone waits up to 30 s).
_RUN_TIMEOUT = 1800.0


class EtsExportError(RuntimeError):
    """Raised when the local export fails or no installation is usable."""


@dataclass(frozen=True)
class EtsInstall:
    """A detected installation able to re-export a project."""

    directory: str
    version: str
    tested: bool


@dataclass(frozen=True)
class EtsSignResult:
    """Outcome of a successful local re-export."""

    output_path: str
    license_name: str
    license_number: str
    licensed_devices: int
    certificate_written: bool
    validation_ok: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


class _FixedFileInfo(ctypes.Structure):
    _fields_ = (
        ("signature", ctypes.c_uint32),
        ("struct_version", ctypes.c_uint32),
        ("ms", ctypes.c_uint32),  # FileVersionMS
        ("ls", ctypes.c_uint32),  # FileVersionLS
        ("product_ms", ctypes.c_uint32),
        ("product_ls", ctypes.c_uint32),
        ("flags_mask", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("os", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("subtype", ctypes.c_uint32),
        ("date_ms", ctypes.c_uint32),
        ("date_ls", ctypes.c_uint32),
    )


def _file_version(exe: Path) -> str:
    """Read a Windows file version string, or ``""`` off Windows / on failure."""
    if sys.platform != "win32":
        return ""
    try:
        # getattr keeps the win32-only attribute off the type checker on other platforms.
        version_dll: Any = getattr(ctypes, "windll").version  # noqa: B009
        path = str(exe)
        size = version_dll.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        version_dll.GetFileVersionInfoW(path, 0, size, buf)
        value = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not version_dll.VerQueryValueW(
            buf, "\\", ctypes.byref(value), ctypes.byref(length)
        ):
            return ""
        info = ctypes.cast(value, ctypes.POINTER(_FixedFileInfo)).contents
        return f"{info.ms >> 16}.{info.ms & 0xFFFF}.{info.ls >> 16}.{info.ls & 0xFFFF}"
    except Exception:
        return ""


def _detect(
    required: tuple[str, ...], main_exe: str, tested_prefix: str, ets_dir: str | None
) -> EtsInstall | None:
    roots = [ets_dir] if ets_dir else list(ETS_ROOTS)
    for root in roots:
        if not root:
            continue
        directory = Path(root)
        if not all((directory / name).is_file() for name in required):
            continue
        version = _file_version(directory / main_exe)
        return EtsInstall(str(directory), version, version.startswith(tested_prefix))
    return None


def detect_ets(ets_dir: str | None = None) -> EtsInstall | None:
    """Return the first usable ETS 6 installation (optionally forced to ``ets_dir``), or ``None``."""
    return _detect(_REQUIRED_FILES, "ETS6N.exe", _TESTED_PREFIX, ets_dir)


def detect_ets5(ets_dir: str | None = None) -> EtsInstall | None:
    """Return the first usable ETS 5 installation (optionally forced to ``ets_dir``), or ``None``."""
    return _detect(_ETS5_REQUIRED_FILES, "ets5n.exe", _ETS5_TESTED_PREFIX, ets_dir)


def ets_local_available() -> bool:
    """Whether a local ETS 6 re-export can run here (dotnet/prebuilt + a detected install)."""
    return ets_export_backend_available() and detect_ets() is not None


def ets5_local_available() -> bool:
    """Whether a local ETS 5 re-export can run here (dotnet/prebuilt + a detected install)."""
    return ets5_export_backend_available() and detect_ets5() is not None


def _parse_result(output_path: str, stdout: str, returncode: int) -> EtsSignResult:
    fields: dict[str, str] = {}
    notes: list[str] = []
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "NOTE":
            notes.append(value)
        else:
            fields[key] = value
    # The contract: success is STATUS=ok AND a zero exit (a crash after printing the fields must
    # not read as success).
    if fields.get("STATUS") != "ok" or returncode != 0:
        if fields.get("ERROR"):
            detail = fields["ERROR"]
        elif returncode != 0:
            detail = f"the helper exited with code {returncode}"
        else:
            detail = stdout.strip() or "the helper did not report success"
        raise EtsExportError(detail)
    if not Path(output_path).is_file():
        raise EtsExportError("the helper reported success but wrote no output file")
    try:
        licensed_devices = int(fields.get("LICENSED_DEVICES", "0") or "0")
    except ValueError:
        licensed_devices = 0
    return EtsSignResult(
        output_path=output_path,
        license_name=fields.get("LICENSE_NAME", ""),
        license_number=fields.get("LICENSE_NUMBER", ""),
        licensed_devices=licensed_devices,
        certificate_written=fields.get("CERT") == "true",
        validation_ok=fields.get("VALIDATION") == "true",
        notes=tuple(notes),
    )


def _run_helper(
    exe: Path,
    ets_dir: str,
    source: Path,
    output: Path,
    password: str | None,
    accept_untested: bool,
    progress: Callable[[str], None] | None,
) -> EtsSignResult:
    cmd = [str(exe), ets_dir, str(source), str(output)]
    if password:
        cmd += ["--password", password]
    if accept_untested:
        cmd.append("--accept-untested")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=ets_dir,
        )
    except OSError as exc:
        raise EtsExportError(str(exc)) from exc

    # Read stdout on a thread so a helper that hangs with its pipe open cannot block us forever:
    # the deadline runs from launch, and on expiry we kill the process and raise.
    collected: list[str] = []

    def _read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            collected.append(line)
            if progress is not None and line.startswith("STAGE="):
                progress(line[len("STAGE=") :])

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(_RUN_TIMEOUT)
    if reader.is_alive():
        proc.kill()
        reader.join(5)
        raise EtsExportError("the local export timed out")
    returncode = proc.wait()
    return _parse_result(str(output), "\n".join(collected), returncode)


def _local_sign(
    input_knxproj: str | Path,
    output_knxproj: str | Path,
    *,
    password: str | None,
    accept_untested: bool,
    progress: Callable[[str], None] | None,
    install: EtsInstall | None,
    build_exe: Callable[[], Path],
) -> EtsSignResult:
    # Resolve to absolute paths: the helper runs with cwd set to the install directory, so a
    # relative input/output would otherwise be read/written there instead of the caller's location.
    source = Path(input_knxproj).resolve()
    if not source.is_file():
        raise EtsExportError(f"project not found: {source}")
    output = Path(output_knxproj).resolve()
    if install is None:
        raise EtsExportError("no usable ETS installation was found")
    if install.version and not install.tested and not accept_untested:
        raise EtsExportError(
            f"ETS {install.version} is untested; pass accept_untested to proceed"
        )
    try:
        exe = build_exe()
    except (
        Exception
    ) as exc:  # KeyExtractionError, build timeout, OSError -> the documented error
        raise EtsExportError(f"could not build the export helper: {exc}") from exc
    return _run_helper(
        exe, install.directory, source, output, password, accept_untested, progress
    )


def ets_local_sign(
    input_knxproj: str | Path,
    output_knxproj: str | Path,
    *,
    password: str | None = None,
    ets_dir: str | None = None,
    accept_untested: bool = False,
    progress: Callable[[str], None] | None = None,
) -> EtsSignResult:
    """Re-export ``input_knxproj`` to ``output_knxproj`` through the installed ETS 6.

    ``progress`` is called with a short stage name as the run advances. Raises
    :class:`EtsExportError` on any failure (nothing is written to ``output_knxproj`` then).
    """
    return _local_sign(
        input_knxproj,
        output_knxproj,
        password=password,
        accept_untested=accept_untested,
        progress=progress,
        install=detect_ets(ets_dir),
        build_exe=build_ets_export_exe,
    )


def ets5_local_sign(
    input_knxproj: str | Path,
    output_knxproj: str | Path,
    *,
    password: str | None = None,
    ets_dir: str | None = None,
    accept_untested: bool = False,
    progress: Callable[[str], None] | None = None,
) -> EtsSignResult:
    """Re-export ``input_knxproj`` to ``output_knxproj`` through the installed ETS 5.

    The licence certificate needs a physical dongle; without one the export still succeeds but stays
    uncertified (``certificate_written=False``). Raises :class:`EtsExportError` on any failure.
    """
    return _local_sign(
        input_knxproj,
        output_knxproj,
        password=password,
        accept_untested=accept_untested,
        progress=progress,
        install=detect_ets5(ets_dir),
        build_exe=build_ets5_export_exe,
    )
