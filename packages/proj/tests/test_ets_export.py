"""Local-ETS re-export wrapper: detection, output parsing and error handling with a mocked backend.

The real run needs Windows with a licensed ETS; here the subprocess is replaced with canned output.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from xknxeditor.proj.core import ets_export
from xknxeditor.proj.core.ets_export import (
    EtsExportError,
    EtsInstall,
    detect_ets,
    detect_ets5,
    ets5_local_sign,
    ets_local_sign,
)

_REQUIRED = (
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

_ETS5_REQUIRED = (
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


def _fake_install(tmp_path: Path, name: str, files: tuple[str, ...]) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    for member in files:
        (directory / member).write_bytes(b"")
    return directory


class _FakeProc:
    def __init__(self, lines: list[str], returncode: int) -> None:
        self.stdout: Iterator[str] = iter(f"{line}\n" for line in lines)
        self.returncode = returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:  # pragma: no cover - not reached in these tests
        pass


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lines: list[str],
    *,
    version: str = "6.4.1",
    tested: bool = True,
    returncode: int = 0,
    write_output: bool = True,
    detect_name: str = "detect_ets",
    build_name: str = "build_ets_export_exe",
) -> None:
    """Wire a fake install + fake subprocess (which writes the output file like the real helper)."""
    install = EtsInstall(str(tmp_path / "ETS"), version, tested)
    monkeypatch.setattr(ets_export, detect_name, lambda ets_dir=None: install)
    monkeypatch.setattr(ets_export, build_name, lambda: tmp_path / "helper.exe")

    def _popen(cmd: list[str], *a: object, **k: object) -> _FakeProc:
        if write_output:
            Path(cmd[3]).write_bytes(
                b"signed"
            )  # cmd = [exe, ets-dir, input, output, ...]
        return _FakeProc(lines, returncode)

    monkeypatch.setattr(ets_export.subprocess, "Popen", _popen)


def test_detect_requires_every_file(tmp_path: Path) -> None:
    directory = _fake_install(tmp_path, "ETS6", _REQUIRED)
    assert detect_ets(str(directory)) is not None
    (directory / "Autofac.dll").unlink()
    assert detect_ets(str(directory)) is None


def test_detect_ets5_requires_every_file(tmp_path: Path) -> None:
    directory = _fake_install(tmp_path, "ETS5", _ETS5_REQUIRED)
    assert detect_ets5(str(directory)) is not None
    # An ETS6-only file set is not an ETS5 install, and vice versa.
    assert detect_ets(str(directory)) is None
    (directory / "ets5n.exe").unlink()
    assert detect_ets5(str(directory)) is None


def test_ets5_sign_no_dongle_exports_uncertified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "in.knxproj"
    source.write_bytes(b"zip")
    _patch_run(
        monkeypatch,
        tmp_path,
        [
            "STAGE=exporting",
            "LICENSED_DEVICES=0",
            "CERT=false",
            "VALIDATION=false",
            "NOTE=no ETS dongle or licence was found",
            "STATUS=ok",
        ],
        version="5.7.1428",
        detect_name="detect_ets5",
        build_name="build_ets5_export_exe",
    )
    result = ets5_local_sign(source, tmp_path / "out.knxproj")
    assert result.certificate_written is False
    assert result.notes == ("no ETS dongle or licence was found",)


def test_sign_parses_a_successful_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "in.knxproj"
    source.write_bytes(b"zip")
    _patch_run(
        monkeypatch,
        tmp_path,
        [
            "STAGE=importing",
            "STAGE=exporting",
            "LICENSE_NAME=Jane Doe",
            "LICENSE_NUMBER=12345",
            "LICENSED_DEVICES=20",
            "CERT=true",
            "VALIDATION=true",
            "NOTE=all good",
            "STATUS=ok",
        ],
    )
    stages: list[str] = []
    result = ets_local_sign(source, tmp_path / "out.knxproj", progress=stages.append)
    assert result.license_name == "Jane Doe"
    assert result.license_number == "12345"
    assert result.licensed_devices == 20
    assert result.certificate_written is True
    assert result.validation_ok is True
    assert result.notes == ("all good",)
    assert stages == ["importing", "exporting"]


def test_sign_raises_on_error_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "in.knxproj"
    source.write_bytes(b"zip")
    _patch_run(monkeypatch, tmp_path, ["ERROR=ETS dropped 2 devices"])
    with pytest.raises(EtsExportError, match="dropped 2 devices"):
        ets_local_sign(source, tmp_path / "out.knxproj")


def test_sign_rejects_missing_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, tmp_path, ["STATUS=ok"])
    with pytest.raises(EtsExportError, match="not found"):
        ets_local_sign(tmp_path / "missing.knxproj", tmp_path / "out.knxproj")


def test_sign_rejects_untested_without_optin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "in.knxproj"
    source.write_bytes(b"zip")
    _patch_run(monkeypatch, tmp_path, ["STATUS=ok"], version="6.3.0", tested=False)
    with pytest.raises(EtsExportError, match="untested"):
        ets_local_sign(source, tmp_path / "out.knxproj")


def test_sign_rejects_nonzero_exit_despite_status_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "in.knxproj"
    source.write_bytes(b"zip")
    _patch_run(monkeypatch, tmp_path, ["STATUS=ok"], returncode=3)
    with pytest.raises(EtsExportError, match="code 3"):
        ets_local_sign(source, tmp_path / "out.knxproj")


def test_sign_rejects_success_without_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "in.knxproj"
    source.write_bytes(b"zip")
    _patch_run(monkeypatch, tmp_path, ["STATUS=ok"], write_output=False)
    with pytest.raises(EtsExportError, match="no output file"):
        ets_local_sign(source, tmp_path / "out.knxproj")
